#!/usr/bin/env python3
"""
Test runner for iperf post-processor

Runs the post-processor against test samples and verifies expected metrics are generated.
"""

import json
import lzma
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
BENCH_IPERF_DIR = SCRIPT_DIR.parent
TEST_DATA_DIR = SCRIPT_DIR / "test-data"
POST_PROCESSOR = BENCH_IPERF_DIR / "iperf-post-process.py"
VENV_DIR = SCRIPT_DIR / ".venv"

# SCRIPT_DIR is already resolved (follows symlinks), so it points to the real repo path
# This is important because the container sees /opt/crucible/repos/..., not /opt/crucible/subprojects/...
REAL_SCRIPT_DIR = BENCH_IPERF_DIR

def run_test_case(test_dir):
    """Run post-processor on a test case and verify results"""
    test_name = test_dir.name
    print(f"\n{'='*60}")
    print(f"Test: {test_name}")
    print(f"{'='*60}")

    # Load expected results
    expected_file = test_dir / "expected-metrics.json"
    if not expected_file.exists():
        print(f"  SKIP: No expected-metrics.json found")
        return None

    with open(expected_file) as f:
        expected = json.load(f)

    print(f"  Description: {expected.get('description', 'N/A')}")
    print(f"  Expected metrics: {expected['expected_metrics']}")

    # The post-processor picks client vs. server mode by checking which
    # result file exists in its cwd, checking for a client file first. A
    # flat copy of both files into one directory would always resolve to
    # client mode, silently skipping server-mode testing whenever a case
    # provides both files (needed so server mode can read the client's
    # timestamps). Lay out a real sample-1/<role>/1/ tree instead, matching
    # how rickshaw actually invokes this script, so role is unambiguous.
    role = expected.get('role', 'client')
    run_dir = Path(tempfile.mkdtemp(prefix="iperf-unit-test-"))
    try:
        client_dir = run_dir / "sample-1" / "client" / "1"
        server_dir = run_dir / "sample-1" / "server" / "1"
        client_dir.mkdir(parents=True)
        server_dir.mkdir(parents=True)

        if (test_dir / "iperf-client-result.txt").exists():
            shutil.copy(test_dir / "iperf-client-result.txt", client_dir)
        if (test_dir / "iperf-server-result.txt").exists():
            shutil.copy(test_dir / "iperf-server-result.txt", server_dir)

        cwd = server_dir if role == 'server' else client_dir

        # Determine protocol arg
        protocol = expected.get('protocol', 'tcp')

        # Run post-processor using Python 3.11 venv (no container needed)
        # Set TOOLBOX_HOME and PYTHONPATH for imports to work
        venv_python = VENV_DIR / "bin" / "python3"
        script_path = REAL_SCRIPT_DIR / "iperf-post-process.py"
        toolbox_home = Path("/opt/crucible/subprojects/core/toolbox")

        env = os.environ.copy()
        env['TOOLBOX_HOME'] = str(toolbox_home)
        env['PYTHONPATH'] = str(toolbox_home / "python")

        cmd = [venv_python, str(script_path), f"--protocol={protocol}", "--ipv=4", "--ifname=eth0"]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=str(cwd),
            env=env
        )

        # Check if this test should fail
        should_fail = expected.get('should_fail', False)

        # Check for success status in stdout
        success_marker = "POST-PROCESS-STATUS: success"
        processing_succeeded = success_marker in result.stdout

        if should_fail:
            if processing_succeeded:
                # Should have failed but succeeded
                print(f"  FAIL ✗ Expected failure but post-processor succeeded")
                if result.stdout:
                    print(f"  STDOUT:\n{result.stdout}")
                return False
            else:
                # Correctly failed (no success marker)
                print(f"  PASS ✓ (correctly failed - no success marker in output)")
                if result.stdout:
                    print(f"  STDOUT:\n{result.stdout}")
                if result.stderr:
                    print(f"  STDERR:\n{result.stderr}")
                return True
        else:
            if not processing_succeeded:
                # Expected success but no success marker
                print(f"  FAIL ✗ Post-processor did not complete successfully")
                if result.stdout:
                    print(f"  STDOUT:\n{result.stdout}")
                if result.stderr:
                    print(f"  STDERR:\n{result.stderr}")
                return False

            metric_types, stream_ids = read_metric_metadata(cwd / "postprocess")

            expected_types = set(expected['expected_metrics'])
            if metric_types != expected_types:
                print(f"  FAIL ✗ Metric types {sorted(metric_types)} != expected {sorted(expected_types)}")
                return False

            expected_stream_count = expected.get('expected_stream_count')
            if expected_stream_count is not None and len(stream_ids) != expected_stream_count:
                print(f"  FAIL ✗ Found {len(stream_ids)} distinct stream(s) {sorted(stream_ids)}, expected {expected_stream_count}")
                return False

            print(f"  PASS ✓ (metric types {sorted(metric_types)} match" +
                  (f", {len(stream_ids)} stream(s) {sorted(stream_ids)})" if stream_ids else ")"))
            return True

    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def read_metric_metadata(pp_dir):
    """Return (set of metric types, set of stream breakout values) from a postprocess/ dir."""
    meta_file = pp_dir / "metric-data-0.json.xz"
    if not meta_file.exists():
        return set(), set()
    with lzma.open(meta_file, "rt") as f:
        metric_types = json.load(f)
    types = {m["desc"]["type"] for m in metric_types}
    streams = {m["names"]["stream"] for m in metric_types if "stream" in m["names"]}
    return types, streams

def main():
    """Run all tests"""
    print("=" * 60)
    print("iperf Post-Processor Test Suite")
    print("=" * 60)

    # Check if venv exists
    venv_python = VENV_DIR / "bin" / "python3"
    if not venv_python.exists():
        print(f"ERROR: Python venv not found at {VENV_DIR}")
        print(f"")
        print(f"Run the bootstrap script first:")
        print(f"  cd {SCRIPT_DIR}")
        print(f"  ./bootstrap.sh")
        sys.exit(1)

    if not TEST_DATA_DIR.exists():
        print(f"ERROR: Test data directory not found: {TEST_DATA_DIR}")
        print("Run ./extract-test-samples.sh first")
        sys.exit(1)

    # Find all test cases
    test_cases = [d for d in TEST_DATA_DIR.iterdir() if d.is_dir()]
    test_cases.sort()

    if not test_cases:
        print(f"ERROR: No test cases found in {TEST_DATA_DIR}")
        sys.exit(1)

    print(f"\nFound {len(test_cases)} test cases")

    # Run tests
    results = {}
    for test_dir in test_cases:
        result = run_test_case(test_dir)
        if result is not None:
            results[test_dir.name] = result

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = sum(1 for r in results.values() if r)
    failed = sum(1 for r in results.values() if not r)

    for test_name, result in sorted(results.items()):
        status = "PASS ✓" if result else "FAIL ✗"
        print(f"  {test_name:40s} {status}")

    print("\n" + "=" * 60)
    print(f"Total: {len(results)}  Passed: {passed}  Failed: {failed}")
    print("=" * 60)

    # Exit code
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()
