#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOOLBOX_HOME = os.environ.get("TOOLBOX_HOME")
if TOOLBOX_HOME:
    sys.path.append(str(Path(TOOLBOX_HOME) / "python"))

from toolbox.cdm_metrics import CDMMetrics
from toolbox.fileio import open_read_text_file

SEC_TO_MSEC = 1000
KBPS_TO_GBPS = 1000000

debug = False


def debug_print(msg):
    if debug:
        print(msg, end="")


def get_rate_divisor(rateunit):
    if rateunit == "Kbits/sec":
        return 1000000
    elif rateunit == "Mbits/sec":
        return 1000
    elif rateunit == "Gbits/sec":
        return 1
    else:
        print(f"Error: Did not recognize rate unit: {rateunit}")
        sys.exit(1)


def extract_role(line):
    """Extract role marker from bidirectional output line.

    Returns role string like 'TX-C', 'RX-C', 'TX-S', 'RX-S', or None if not bidir.
    """
    # Pattern: [ ID][Role] e.g., "[  5][TX-C]"
    match = re.search(r'\[\s*\d+\]\[([A-Z]{2}-[CS])\]', line)
    if match:
        return match.group(1)
    return None


def extract_stream_id(line):
    """Extract the iperf3 stream ID from a per-stream data line.

    With --parallel/-P > 1 (one OS thread per stream since iperf3 3.16),
    each interval has one row per stream plus a "[SUM]" aggregate row.
    Returns the stream ID string (e.g. "5"), or None for a "[SUM]" row
    or any line without a leading "[ ID]" marker.
    """
    match = re.match(r'\[\s*(\d+)\]', line)
    if match:
        return match.group(1)
    return None


def is_hunting_result_file(path):
    """Return True when a result file contains multiple timestamped trials."""
    try:
        fh, _ = open_read_text_file(path)
    except FileNotFoundError:
        return False
    with fh:
        return sum(1 for line in fh if line.startswith("BEGIN-TS")) > 1


def extract_pass_bitrate(line):
    columns = line.split()
    for index in range(len(columns) - 1):
        if "bits/sec" in columns[index + 1]:
            try:
                return float(columns[index])
            except ValueError:
                return None
    return None


def parse_client_trials(path):
    """Split client output into timestamped trials and capture pass/fail state."""
    try:
        fh, _ = open_read_text_file(path)
    except FileNotFoundError:
        print(f"ERROR: Could not open client result file {path}")
        sys.exit(1)

    trials = []
    current = None
    with fh:
        for line in fh:
            match = re.match(r"BEGIN-TS-(\d+)\s+(\d+(?:\.\d+)?)", line)
            if match:
                if current is not None:
                    trials.append(current)
                current = {
                    "number": int(match.group(1)),
                    "begin": float(match.group(2)),
                    "end": None,
                    "passed": False,
                    "bitrate": None,
                    "lines": [line],
                }
                continue

            if current is None:
                continue

            current["lines"].append(line)
            end_match = re.match(r"END-TS-(\d+)\s+(\d+(?:\.\d+)?)", line)
            if end_match:
                current["end"] = float(end_match.group(2))
            elif line.startswith("PASS:"):
                current["passed"] = True
                current["bitrate"] = extract_pass_bitrate(line)
            elif line.startswith("FAIL:"):
                current["passed"] = False

    if current is not None:
        trials.append(current)

    if not trials:
        print(f"ERROR: No BEGIN-TS trial found in {path}")
        sys.exit(1)
    if any(trial["end"] is None for trial in trials):
        print(f"ERROR: At least one trial in {path} has no END-TS marker")
        sys.exit(1)
    return trials


def select_primary_trial(trials):
    passing = [
        trial for trial in trials if trial["passed"] and trial["bitrate"] is not None
    ]
    if not passing:
        print("ERROR: No PASS confirm found in hunting results - every confirm run")
        print("       exceeded the loss threshold (--max-loss-pct). Failing this")
        print("       sample rather than publishing an over-threshold (FAILED) rate.")
        print("HUNT-STATUS: no-pass-confirm")
        sys.exit(1)

    selected = max(passing, key=lambda trial: (trial["bitrate"], trial["number"]))
    print(
        f"Selected highest passing trial {selected['number']} "
        f"({selected['bitrate']} bits/sec)"
    )
    return selected


def write_client_trial_files(trials):
    os.makedirs("postprocess", exist_ok=True)
    paths = {}
    for trial in trials:
        path = f"postprocess/client-trial-{trial['number']}.txt"
        temporary_path = path + ".tmp"
        with open(temporary_path, "w") as output:
            output.writelines(trial["lines"])
        os.replace(temporary_path, path)
        paths[trial["number"]] = path
    return paths


def split_server_tests(path):
    """Split iperf server output into files keyed by its test number."""
    try:
        fh, _ = open_read_text_file(path)
    except FileNotFoundError:
        print(f"ERROR: Could not open server result file {path}")
        sys.exit(1)

    os.makedirs("postprocess", exist_ok=True)
    tests = {}
    current_number = None
    current_lines = []
    with fh:
        for line in fh:
            match = re.search(r"Server listening on \d+ \(test #(\d+)\)", line)
            if match:
                if current_number is not None:
                    tests[current_number] = current_lines
                current_number = int(match.group(1))
                current_lines = []
            elif current_number is not None:
                current_lines.append(line)
    if current_number is not None:
        tests[current_number] = current_lines

    paths = {}
    for number, lines in tests.items():
        path = f"postprocess/server-test-{number}.txt"
        temporary_path = path + ".tmp"
        with open(temporary_path, "w") as output:
            output.writelines(lines)
        os.replace(temporary_path, path)
        paths[number] = path
    return paths


def process_proto(data_file, times, names, omit, metrics, period_name):
    print("process_proto: enter")
    rateunit = "none"
    bitrate_div = 1
    sample_count = 0
    primary_metric = None
    bidir_mode = False
    # For bidirectional: accumulate samples by timestamp
    # Key: (begin_ts, end_ts), Value: {'tx-Gbps': value, 'rx-Gbps': value}
    throughput_samples = {}

    # Track whether we've seen header (for protocol detection)
    has_retr_column = False  # TCP sender has "Retr" column
    has_lost_total_column = False  # UDP receiver has "Lost/Total" column
    has_datagrams_column = False  # UDP has "Datagrams" column

    try:
        fh, _ = open_read_text_file(data_file)
    except FileNotFoundError:
        print(f"iperf-post-process(): could not open {data_file}")
        print("Is the current directory for a iperf server (no result file)?")
        return None

    for line in fh:
        line = line.rstrip("\n")

        # Detect protocol from header line (but don't use for column positions!)
        if "[ ID]" in line and "Interval" in line:
            print(f"Header line: {line}")
            if "Retr" in line:
                has_retr_column = True
                print("Detected TCP sender mode (header has Retr column)")
            if "Lost/Total" in line:
                has_lost_total_column = True
                print("Detected UDP receiver mode (header has Lost/Total column)")
            if "Datagrams" in line:
                has_datagrams_column = True
                print("Detected UDP mode (header has Datagrams column)")
            if not has_retr_column and not has_lost_total_column and not has_datagrams_column:
                print("Detected TCP receiver mode (header has no special columns)")
            continue

        # Stop processing if we hit embedded server output (from --get-server-output)
        if line == "Server output:":
            print("Detected embedded server output - stopping client processing")
            print("(Server data will be processed from separate server-result.txt file)")
            break

        if "sender" in line or "receiver" in line:
            debug_print(f"Skip line: {line}\n")
            rateunit = "none"
            continue

        if "SUM" in line:
            continue

        if "omitted" in line:
            continue

        # Parse data lines - use header detection to determine protocol-specific parsing
        # UDP is identified by "Lost/Total" (receiver) or "Datagrams" (sender or receiver)
        # TCP is identified by "Retr" (sender) or no special columns (receiver)
        is_udp_line = has_lost_total_column or has_datagrams_column

        if is_udp_line and " sec " in line:
            debug_print(f"Proc line: {line}\n")
            columns = line.split()

            # Tag each stream's samples with its iperf3 stream ID so
            # nthreads > 1 runs log per-stream series instead of colliding
            # into one; CDM's default-aggregation sums across the breakout.
            stream_id = extract_stream_id(line)
            sample_names = {**names, "stream": stream_id} if stream_id else names

            # Find interval using X.XX-Y.YY pattern
            interval_val = None
            interval_start = None
            interval_end = None
            for col in columns:
                if "-" in col and "." in col:
                    try:
                        parts = col.split("-")
                        if len(parts) == 2:
                            start = float(parts[0])
                            end = float(parts[1])
                            sec_delta = end - start
                            if sec_delta > 0:
                                interval_val = sec_delta * SEC_TO_MSEC
                                interval_start = start
                                interval_end = end
                                break
                    except ValueError:
                        continue

            if interval_val is None or interval_val == 0:
                continue

            debug_print(f"interval={interval_val}\n")

            # Find bitrate value (number before column containing 'bits/sec')
            bitrate = None
            bitrate_unit_idx = None
            for i in range(len(columns) - 1):
                if 'bits/sec' in columns[i + 1]:
                    try:
                        bitrate = float(columns[i])
                        bitrate_unit_idx = i + 1
                        break
                    except ValueError:
                        continue

            if bitrate is None:
                continue

            debug_print(f"bitrate {bitrate}\n")

            if rateunit == "none":
                rateunit = columns[bitrate_unit_idx]
                bitrate_div = get_rate_divisor(rateunit)

            # Derive timestamps from this row's own interval bounds (relative
            # to the test's begin time) rather than an incrementing counter --
            # with nthreads > 1 there are multiple rows per real interval, and
            # incrementing per-row stretched the series out by a factor of N.
            ts = times["begin"] + interval_start * SEC_TO_MSEC
            ts_end = times["begin"] + interval_end * SEC_TO_MSEC - 1

            # Find Lost/Total column by searching for X/Y pattern (receiver side)
            lost_total_idx = None
            for i, col in enumerate(columns):
                if "/" in col and "bits/sec" not in col:
                    lost_total_idx = i
                    break

            print(f"UDP: Found lost_total_idx={lost_total_idx} in columns: {columns}")
            if lost_total_idx is not None:
                lost_total = columns[lost_total_idx]
                # Extract the "X/Y" part from "X/Y(pct%)" format
                lost_total_clean = lost_total.split("(")[0] if "(" in lost_total else lost_total
                parts = lost_total_clean.split("/")
                if len(parts) == 2:
                    try:
                        lost = int(parts[0])
                        total = int(parts[1])

                        print(f"UDP: Logging rx-lost/sec={lost}, rx-pps={total}")

                        desc = {"source": "iperf", "class": "throughput", "type": "rx-lost/sec"}
                        s = {"begin": int(ts), "end": int(ts_end), "value": lost}
                        metrics.log_sample(period_name, desc, sample_names, s)

                        desc = {"source": "iperf", "class": "throughput", "type": "rx-pps"}
                        s = {"begin": int(ts), "end": int(ts_end), "value": total}
                        metrics.log_sample(period_name, desc, sample_names, s)

                        if primary_metric is None:
                            primary_metric = "rx-Gbps"

                        desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
                    except (ValueError, IndexError) as e:
                        print(f"UDP: Error parsing Lost/Total: {e}")
                        # Fall through to log just throughput
                        if primary_metric is None:
                            primary_metric = "tx-Gbps"
                        desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
                else:
                    print(f"UDP: len(parts)={len(parts)}, not 2 - sender side")
                    desc = {"source": "iperf", "class": "throughput", "type": "tx-Gbps"}
            else:
                print(f"UDP: No Lost/Total found - sender side")
                desc = {"source": "iperf", "class": "throughput", "type": "tx-Gbps"}

            s = {"begin": int(ts), "end": int(ts_end), "value": bitrate / bitrate_div}
            debug_print(f"begin: int {ts}, end: int {ts_end}\n")
            metrics.log_sample(period_name, desc, sample_names, s)
            sample_count += 1

        elif not is_udp_line and re.search(r'sec\s', line):
            # TCP
            debug_print(f"Proc line: {line}\n")

            # Check for bidirectional role marker
            role = extract_role(line)
            if role and not bidir_mode:
                bidir_mode = True
                print("BIDIRECTIONAL MODE DETECTED from role markers in output")
                print("TX and RX throughput will be aggregated separately")
                print()

            columns = line.split()

            # Tag each stream's samples with its iperf3 stream ID so
            # nthreads > 1 runs log per-stream series instead of colliding
            # into one; CDM's default-aggregation sums across the breakout.
            stream_id = extract_stream_id(line)
            sample_names = {**names, "stream": stream_id} if stream_id else names

            # Find interval using X.XX-Y.YY pattern
            interval_val = None
            interval_start = None
            interval_end = None
            for col in columns:
                if "-" in col and "." in col:
                    try:
                        parts = col.split("-")
                        if len(parts) == 2:
                            start = float(parts[0])
                            end = float(parts[1])
                            sec_delta = end - start
                            if sec_delta > 0:
                                interval_val = sec_delta * SEC_TO_MSEC
                                interval_start = start
                                interval_end = end
                                break
                    except ValueError:
                        continue

            if interval_val is None or interval_val == 0:
                continue

            debug_print(f"interval={interval_val}\n")

            # Find bitrate (number before column containing 'bits/sec')
            bitrate = None
            bitrate_unit_idx = None
            for i in range(len(columns) - 1):
                if 'bits/sec' in columns[i + 1]:
                    try:
                        bitrate = float(columns[i])
                        bitrate_unit_idx = i + 1
                        break
                    except ValueError:
                        continue

            if bitrate is None:
                continue

            debug_print(f"bitrate {bitrate}\n")

            if rateunit == "none":
                rateunit = columns[bitrate_unit_idx]
                bitrate_div = get_rate_divisor(rateunit)

            # Derive timestamps from this row's own interval bounds (relative
            # to the test's begin time) rather than an incrementing counter --
            # with nthreads > 1 there are multiple rows per real interval, and
            # incrementing per-row stretched the series out by a factor of N.
            ts = times["begin"] + interval_start * SEC_TO_MSEC
            ts_end = times["begin"] + interval_end * SEC_TO_MSEC - 1

            # Find retry value: search for integer AFTER bits/sec unit and BEFORE last 2 columns
            # Pattern: ... Kbits/sec <retry> <cwnd_value> <cwnd_unit>
            # The retry is the first integer after bits/sec
            # If header has Retr column, this is TX side
            is_tx = has_retr_column
            if has_retr_column and bitrate_unit_idx is not None:
                # Look for integer after bitrate_unit_idx
                for i in range(bitrate_unit_idx + 1, len(columns) - 1):
                    try:
                        retry = int(columns[i])
                        debug_print(f"Retry: {retry} at position {i}\n")

                        desc = {"source": "iperf", "class": "count", "type": "tx-retry/sec"}
                        s = {"begin": int(ts), "end": int(ts_end), "value": retry}
                        metrics.log_sample(period_name, desc, sample_names, s)
                        break  # Found retry, stop searching
                    except (ValueError, IndexError):
                        # Not an integer, keep looking
                        continue

            # Determine metric type
            if bidir_mode and role:
                # In bidir mode, TX-* roles map to tx-Gbps, RX-* to rx-Gbps
                metric_type = 'tx-Gbps' if role.startswith('TX') else 'rx-Gbps'
                # Set primary_metric on first RX sample
                if primary_metric is None and metric_type == 'rx-Gbps':
                    primary_metric = 'rx-Gbps'
                    debug_print(f"primary_metric {primary_metric}\n")
            else:
                # Unidirectional mode
                if is_tx:
                    metric_type = "tx-Gbps"
                else:
                    if primary_metric is None:
                        primary_metric = "rx-Gbps"
                        debug_print(f"primary_metric {primary_metric}\n")
                    metric_type = primary_metric

            throughput_value = bitrate / bitrate_div

            if bidir_mode:
                # Accumulate for later aggregation, keyed per-stream so
                # nthreads > 1 doesn't collapse separate streams together
                key = (int(ts), int(ts_end), stream_id)
                if key not in throughput_samples:
                    throughput_samples[key] = {}
                if metric_type not in throughput_samples[key]:
                    throughput_samples[key][metric_type] = 0
                throughput_samples[key][metric_type] += throughput_value
                debug_print(f"Accum {metric_type} at {key}: {throughput_samples[key][metric_type]}\n")
            else:
                # Log immediately in unidirectional mode
                desc = {"source": "iperf", "class": "throughput", "type": metric_type}
                s = {"begin": int(ts), "end": int(ts_end), "value": throughput_value}
                metrics.log_sample(period_name, desc, sample_names, s)

            sample_count += 1

    if sample_count == 0:
        primary_metric = "rx-Gbps"
        desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
        s = {"begin": int(times["begin"]), "end": int(times["end"]), "value": 0}
        metrics.log_sample(period_name, desc, names, s)

    fh.close()

    # Log accumulated bidirectional samples
    if bidir_mode and throughput_samples:
        print(f"Logging {len(throughput_samples)} accumulated bidirectional samples")
        for (begin_ts, end_ts, stream_id), metrics_dict in sorted(
            throughput_samples.items(), key=lambda item: item[0][:2]
        ):
            sample_names = {**names, "stream": stream_id} if stream_id else names
            for metric_type, value in metrics_dict.items():
                desc = {"source": "iperf", "class": "throughput", "type": metric_type}
                s = {"begin": begin_ts, "end": end_ts, "value": value}
                metrics.log_sample(period_name, desc, sample_names, s)
                debug_print(f"Logged {metric_type}: {value} Gbps for period {begin_ts}-{end_ts}\n")

    metric_data_name = metrics.finish_samples(dont_delete=True)

    # Ensure primary_metric is set (default to rx-Gbps if not set)
    if primary_metric is None:
        primary_metric = "rx-Gbps"

    return primary_metric, metric_data_name


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remotehost", default=None)
    parser.add_argument("--length", default=None)
    parser.add_argument("--passthru", default=None)
    parser.add_argument("--protocol", default=None)  # Accepted but ignored - protocol auto-detected from header
    parser.add_argument("--time", type=int, default=None)
    parser.add_argument("--bitrate", default=None)
    parser.add_argument("--max-loss-pct", default="0")
    parser.add_argument("--ifname", default=None)
    parser.add_argument("--cpu-pin", default=None)
    parser.add_argument("--bitrate-range", default="0")
    parser.add_argument("--omit", type=int, default=0)
    parser.add_argument("--ipv", type=int, default=None)
    args, _ = parser.parse_known_args()

    # Print header - rickshaw redirects to post-process-output.txt
    print("=== iperf-post-process.py (unidirectional + bidirectional support) ===")
    print(f"Arguments: {' '.join(sys.argv)}")
    print("=" * 60)
    print()

    # Detect if running on server vs client
    is_server = False
    if os.path.exists("iperf-client-result.txt"):
        result_file = "iperf-client-result.txt"
        timestamp_file = result_file  # Client uses its own timestamps
    elif os.path.exists("iperf-server-result.txt"):
        result_file = "iperf-server-result.txt"
        is_server = True

        # Server reads timestamps from client result file
        # Determine engine index from environment or current directory
        engine_index = None

        # Try cs_label environment variable first (e.g., "server-3")
        cs_label = os.environ.get("cs_label", "")
        if cs_label:
            parts = cs_label.split("-")
            if len(parts) >= 2:
                try:
                    engine_index = parts[-1]
                    print(f"Detected engine index {engine_index} from cs_label: {cs_label}")
                except (ValueError, IndexError):
                    pass

        # Fallback: extract from current working directory path
        if engine_index is None:
            cwd = os.getcwd()
            # Path is typically: .../sample-1/server/3/
            path_parts = cwd.rstrip("/").split("/")
            if len(path_parts) >= 2 and path_parts[-2] in ["server", "client"]:
                engine_index = path_parts[-1]
                print(f"Detected engine index {engine_index} from current directory: {cwd}")

        # Fail if we can't determine the engine index
        if engine_index is None:
            print("ERROR: Could not detect engine index from cs_label or working directory")
            print(f"  cs_label environment variable: {os.environ.get('cs_label', '(not set)')}")
            print(f"  Current working directory: {os.getcwd()}")
            sys.exit(1)

        timestamp_file = f"../../client/{engine_index}/iperf-client-result.txt"
        if not os.path.exists(timestamp_file):
            print(f"ERROR: Server needs client timestamps from {timestamp_file}")
            print("Client result file not found")
            sys.exit(1)
        print(f"Server mode: using timestamps from {timestamp_file}")
    else:
        print("ERROR: Neither iperf-client-result.txt nor iperf-server-result.txt found")
        print("Is the current directory for an iperf run?")
        sys.exit(1)

    names = {"cmd": "write"}

    if os.path.exists("./begin.txt"):
        print("Cannot process old results with this version. Please use an older bench-iperf version.")
        sys.exit(1)

    hunting_mode = args.bitrate_range != "0" or is_hunting_result_file(timestamp_file)
    omit_val = args.omit
    trials = parse_client_trials(timestamp_file)
    selected_trial = select_primary_trial(trials) if hunting_mode else trials[0]
    primary_trial_number = selected_trial["number"]

    if is_server:
        trial_files = (
            split_server_tests(result_file)
            if hunting_mode
            else {primary_trial_number: result_file}
        )
    else:
        trial_files = (
            write_client_trial_files(trials)
            if hunting_mode
            else {primary_trial_number: result_file}
        )

    periods = []
    primary_metric = None
    for trial in trials:
        number = trial["number"]
        if number not in trial_files:
            print(f"ERROR: No server test #{number} for client trial #{number}")
            sys.exit(1)

        period_name = (
            "measurement" if trial is selected_trial else f"hunt-trial-{number}"
        )
        file_id = "0" if period_name == "measurement" else period_name
        metrics = CDMMetrics()
        times = {
            "begin": trial["begin"] * SEC_TO_MSEC,
            "end": trial["end"] * SEC_TO_MSEC,
        }
        trial_primary_metric, metric_data_name = process_proto(
            trial_files[number], times, names, omit_val, metrics, file_id
        )
        if trial is selected_trial:
            primary_metric = trial_primary_metric
        periods.append({"name": period_name, "metric-files": [metric_data_name]})

    sample_data = {
        "rickshaw-bench-metric": {"schema": {"version": "2021.04.12"}},
        "benchmark": "iperf",
        "primary-period": "measurement",
        "primary-metric": primary_metric,
        "periods": periods,
    }
    with open("postprocess/post-process-data.json", "w") as output:
        json.dump(sample_data, output, indent=2)
        output.write("\n")

    # Print status to stdout for test infrastructure (on separate line)
    print("POST-PROCESS-STATUS: success")


if __name__ == "__main__":
    main()
