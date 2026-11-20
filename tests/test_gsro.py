import importlib.util
import json
import lzma
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO_ROOT / "iperf-validate-gsro.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("iperf_validate_gsro", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_client_harness(temporary, version_supports_gsro=True):
    temporary = Path(temporary)
    binary_directory = temporary / "bin"
    binary_directory.mkdir()

    base = temporary / "iperf-base"
    base.write_text(
        "\n".join(
            [
                "dump_runtime() { :; }",
                "validate_label() { :; }",
                "validate_sw_prereqs() { :; }",
                "iperf_parse_cpu_pin() { return 0; }",
                "iperf_get_passthru_time() { return 1; }",
                "exit_error() { echo \"TEST exit_error: $*\"; exit 1; }",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    iperf3 = binary_directory / "iperf3"
    iperf3.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                'if [[ "$1" == "--version" ]]; then',
                '    echo "iperf3 3.21"',
                '    echo "GSO/GRO support"'
                if version_supports_gsro
                else '    echo "no UDP offload support"',
                "    exit 0",
                "fi",
                'printf "%s\\n" "$@" >> "$IPERF3_LOG"',
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    iperf3.chmod(0o755)

    ss = binary_directory / "ss"
    ss.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    ss.chmod(0o755)

    client = temporary / "iperf-client-test"
    source = (REPO_ROOT / "iperf-client").read_text(encoding="utf-8")
    source = source.replace(". /usr/bin/iperf-base", f". {base}")
    source = source.replace("timeout_sec=10", "timeout_sec=0")
    client.write_text(source, encoding="utf-8")
    return client, binary_directory, temporary / "iperf3-args.txt"


def run_client(temporary, arguments, version_supports_gsro=True):
    client, binary_directory, log_path = write_client_harness(
        temporary, version_supports_gsro=version_supports_gsro
    )

    listener = None
    for port in range(32000, 32100, 2):
        candidate = socket.socket()
        try:
            candidate.bind(("127.0.0.1", port))
            candidate.listen(1)
            listener = candidate
            break
        except OSError:
            candidate.close()
    if listener is None:
        raise RuntimeError("could not reserve an iperf control port")

    engine_id = (listener.getsockname()[1] - 30000) // 2
    environment = dict(
        os.environ,
        PATH=f"{binary_directory}:{os.environ['PATH']}",
        id=str(engine_id),
        IPERF3_LOG=str(log_path),
    )
    try:
        result = subprocess.run(
            ["bash", str(client), *arguments],
            cwd=temporary,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        listener.close()

    output = (Path(temporary) / "iperf-client-stderrout.txt").read_text(encoding="utf-8")
    return result, output, log_path


def wait_for_server_log(log_path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if log_path.exists():
            try:
                arguments = log_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                arguments = []
            if "--format" in arguments and "-s" in arguments:
                return
        time.sleep(0.01)
    raise AssertionError(f"server did not write its command to {log_path}")


class GsroPrevalidationTests(unittest.TestCase):
    def setUp(self):
        self.validator = load_validator()

    def parameter(self, role="client", **overrides):
        parameter = {"arg": "gsro", "vals": ["true"], "role": role}
        parameter.update(overrides)
        return parameter

    def test_valid_client_udp_parameter_passes(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client"},
                self.parameter(role="client"),
            ]
        )
        self.assertEqual(errors, [])

    def test_other_benchmark_protocol_is_ignored(self):
        errors, _ = self.validator.validate_parameters(
            [
                {
                    "arg": "protocol",
                    "vals": ["tcp"],
                    "role": "client",
                    "benchmark": "uperf",
                    "id": "1",
                },
                {
                    "arg": "protocol",
                    "vals": ["udp"],
                    "role": "client",
                    "benchmark": "iperf",
                    "id": "1",
                },
                self.parameter(benchmark="iperf", id="1"),
            ]
        )
        self.assertEqual(errors, [])

    def test_same_benchmark_protocol_is_still_validated(self):
        errors, _ = self.validator.validate_parameters(
            [
                {
                    "arg": "protocol",
                    "vals": ["tcp"],
                    "role": "client",
                    "benchmark": "iperf",
                    "id": "1",
                },
                self.parameter(benchmark="iperf", id="1"),
            ]
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("gsro=true requires protocol=udp", errors[0])

    def test_id_scoped_protocol_is_used(self):
        parameters = [
            {"arg": "protocol", "vals": ["udp"], "role": "client", "id": "1"},
            {"arg": "protocol", "vals": ["tcp"], "role": "client", "id": "2"},
        ]

        for gsro_id, expected_errors in (("1", 0), ("2", 1)):
            with self.subTest(gsro_id=gsro_id):
                errors, _ = self.validator.validate_parameters(
                    [*parameters, self.parameter(id=gsro_id)]
                )
                self.assertEqual(len(errors), expected_errors)

    def test_global_gsro_requires_udp_for_every_client(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client", "id": "2"},
                self.parameter(),
            ],
            {"1", "2"},
        )
        self.assertEqual(len(errors), 1)

    def test_global_gsro_fails_closed_without_client_ids(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client", "id": "2"},
                self.parameter(),
            ]
        )
        self.assertEqual(len(errors), 1)

        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client"},
                {"arg": "protocol", "vals": ["tcp"], "role": "client", "id": "2"},
                self.parameter(),
            ]
        )
        self.assertEqual(len(errors), 1)

    def test_global_udp_rejects_one_non_udp_client(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client"},
                {"arg": "protocol", "vals": ["tcp"], "role": "client", "id": "2"},
                self.parameter(),
            ],
            {"1", "2"},
        )
        self.assertEqual(len(errors), 1)

    def test_plus_joined_id_scopes_overlap_on_any_member(self):
        errors, _ = self.validator.validate_parameters(
            [
                {
                    "arg": "protocol",
                    "vals": ["udp"],
                    "role": "client",
                    "id": "1+3",
                },
                {
                    "arg": "protocol",
                    "vals": ["udp"],
                    "role": "client",
                    "id": "2+4",
                },
                self.parameter(id="1+2"),
            ]
        )
        self.assertEqual(errors, [])

    def test_absent_id_applies_to_all_engine_ids(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "client"},
                self.parameter(),
            ],
            {"1", "2"},
        )
        self.assertEqual(errors, [])

        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["tcp"], "role": "client"},
                self.parameter(id="2"),
            ]
        )
        self.assertEqual(len(errors), 1)

    def test_validate_run_expands_bench_ids(self):
        run = {
            "bench-ids": "uperf:9,iperf:1-2",
            "iterations": [
                {
                    "params": [
                        {"arg": "protocol", "vals": ["udp"], "role": "client", "id": "2"},
                        self.parameter(),
                    ]
                }
            ],
        }
        errors, _ = self.validator.validate_run(run)
        self.assertEqual(len(errors), 1)

    def test_false_does_not_require_udp(self):
        errors, _ = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["tcp"], "role": "client"},
                self.parameter(vals=["false"]),
            ]
        )
        self.assertEqual(errors, [])

    def test_rejects_tcp_missing_udp_invalid_boolean_and_server_role(self):
        cases = {
            "tcp": [
                {"arg": "protocol", "vals": ["tcp"], "role": "client"},
                self.parameter(),
            ],
            "missing-udp": [self.parameter()],
            "invalid-boolean": [
                {"arg": "protocol", "vals": ["udp"], "role": "client"},
                self.parameter(vals=["yes"]),
            ],
            "server-role": [self.parameter(role="server")],
        }
        for name, parameters in cases.items():
            with self.subTest(name=name):
                errors, _ = self.validator.validate_parameters(parameters)
                self.assertTrue(errors)

    def test_role_all_passes_and_logs_client_negotiation(self):
        errors, messages = self.validator.validate_parameters(
            [
                {"arg": "protocol", "vals": ["udp"], "role": "all"},
                self.parameter(role="all"),
            ]
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("server ignores", messages[0])
        self.assertIn("control connection", messages[0])

    def test_all_iterations_are_validated(self):
        run = {
            "iterations": [
                {"params": [{"arg": "protocol", "vals": ["udp"]}, self.parameter()]},
                {"params": [{"arg": "protocol", "vals": ["tcp"]}, self.parameter()]},
            ]
        }
        errors, _ = self.validator.validate_run(run)
        self.assertEqual(len(errors), 1)
        self.assertIn("iteration 2", errors[0])

    def test_script_reads_run_config_from_rickshaw_run_directory(self):
        with tempfile.TemporaryDirectory(prefix="iperf-gsro-config-") as temporary:
            base = Path(temporary)
            config_directory = base / "config"
            run_directory = base / "run"
            config_directory.mkdir()
            run_directory.mkdir()
            with lzma.open(
                config_directory / "rickshaw-run.json.xz",
                mode="wt",
                encoding="utf-8",
            ) as config_file:
                json.dump(
                    {
                        "iterations": [
                            {
                                "params": [
                                    {"arg": "protocol", "vals": ["udp"], "role": "client"},
                                    {"arg": "gsro", "vals": ["true"], "role": "all"},
                                ]
                            }
                        ]
                },
                config_file,
            )
            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH)],
                cwd=run_directory,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("server ignores", result.stdout)

            result = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH)],
                cwd=base,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("server ignores", result.stdout)

            result = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATOR_PATH),
                    "--run-dir",
                    str(run_directory),
                    "--protocol=tcp",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("server ignores", result.stdout)

    def test_validator_is_registered_and_executable(self):
        config = json.loads((REPO_ROOT / "rickshaw.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config["controller"]["pre-script"],
            "%bench-dir%/iperf-validate-gsro.py --run-dir %run-dir%",
        )
        self.assertTrue(os.access(VALIDATOR_PATH, os.X_OK))

    def test_multiplex_accepts_only_boolean_values(self):
        config = json.loads((REPO_ROOT / "multiplex.json").read_text(encoding="utf-8"))
        pattern = config["validations"]["gsro"]["vals"]
        self.assertTrue(re.fullmatch(pattern, "true"))
        self.assertTrue(re.fullmatch(pattern, "false"))
        for value in ("True", "FALSE", "1", "yes"):
            with self.subTest(value=value):
                self.assertIsNone(re.fullmatch(pattern, value))


class GsroClientScriptTests(unittest.TestCase):
    def test_udp_true_emits_gsro(self):
        with tempfile.TemporaryDirectory(prefix="iperf-gsro-client-") as temporary:
            result, output, log_path = run_client(
                temporary,
                ["--protocol", "udp", "--gsro", "true", "--remotehost", "127.0.0.1"],
            )
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("TEST exit_error:", output)
            arguments = log_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("--udp", arguments)
            self.assertIn("--gsro", arguments)

    def test_udp_false_omits_gsro(self):
        with tempfile.TemporaryDirectory(prefix="iperf-gsro-client-") as temporary:
            result, output, log_path = run_client(
                temporary,
                ["--protocol", "udp", "--gsro", "false", "--remotehost", "127.0.0.1"],
            )
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("TEST exit_error:", output)
            arguments = log_path.read_text(encoding="utf-8").splitlines()
            self.assertIn("--udp", arguments)
            self.assertNotIn("--gsro", arguments)

    def test_rejects_tcp_missing_udp_invalid_boolean_and_old_iperf(self):
        cases = {
            "tcp": (
                ["--protocol", "tcp", "--gsro", "true", "--remotehost", "127.0.0.1"],
                True,
                "TEST exit_error: gsro=true requires protocol=udp",
            ),
            "missing-udp": (
                ["--gsro", "true", "--remotehost", "127.0.0.1"],
                True,
                "TEST exit_error: gsro=true requires protocol=udp",
            ),
            "invalid-boolean": (
                ["--protocol", "udp", "--gsro", "yes", "--remotehost", "127.0.0.1"],
                True,
                "TEST exit_error: invalid gsro value 'yes': expected true or false",
            ),
            "old-iperf": (
                ["--protocol", "udp", "--gsro", "true", "--remotehost", "127.0.0.1"],
                False,
                "TEST exit_error: iperf3 does not advertise GSO/GRO support",
            ),
        }
        for name, (arguments, version_support, expected_message) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(
                prefix="iperf-gsro-invalid-"
            ) as temporary:
                result, output, _ = run_client(
                    temporary, arguments, version_supports_gsro=version_support
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_message, output.splitlines())


class GsroServerScriptTests(unittest.TestCase):
    def test_server_discards_gsro_from_final_command(self):
        with tempfile.TemporaryDirectory(prefix="iperf-gsro-server-") as temporary:
            temporary = Path(temporary)
            client, binary_directory, log_path = write_client_harness(temporary)
            del client
            (temporary / "msgs" / "tx").mkdir(parents=True)

            server = temporary / "iperf-server-start-test"
            source = (REPO_ROOT / "iperf-server-start").read_text(encoding="utf-8")
            source = source.replace(". /usr/bin/iperf-base", f". {temporary / 'iperf-base'}")
            source = source.replace("sleep 10", "sleep 0")
            server.write_text(source, encoding="utf-8")

            environment = dict(
                os.environ,
                PATH=f"{binary_directory}:{os.environ['PATH']}",
                id="1600",
                IPERF3_LOG=str(log_path),
            )
            result = subprocess.run(
                ["bash", str(server), "--protocol", "udp", "--gsro", "false"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = (temporary / "iperf-server-stderrout.txt").read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("server ignores it", output)
            self.assertIn("control connection", output)
            wait_for_server_log(log_path)
            arguments = log_path.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("--gsro", arguments)


if __name__ == "__main__":
    unittest.main()
