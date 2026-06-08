#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import argparse
import json
import os
import re
import subprocess
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


def dup_one_run(fh, first_line, outfile):
    # Write to temp file first, then atomic rename to prevent race condition
    # where server might read partially-written file
    temp_file = outfile + ".tmp"
    with open(temp_file, "w") as ofh:
        ofh.write(first_line)
        for line in fh:
            ofh.write(line)
            if "END-TS" in line:
                break

    # Atomic rename - file appears complete or not at all
    os.rename(temp_file, outfile)


def pre_process_hunting_results(from_file, to_file, client_hunt_result=None, engine_index="1"):
    """Extract the best run from hunting results.

    For client: finds highest bitrate PASS and extracts that run.
    For server: reads which run the client selected, then extracts that test number.
    """
    debug_print("process_hunting_results: enter\n")

    # Server mode: extract run number from client's hunt-temp-result.txt
    if client_hunt_result is not None:
        # Server mode - MUST have client hunt result file
        # Wait up to 30 seconds for the file to appear (client might still be post-processing)
        import time
        max_wait = 30
        wait_interval = 0.5
        waited = 0
        while not os.path.exists(client_hunt_result) and waited < max_wait:
            if waited == 0:
                print(f"Waiting for client hunt result file: {client_hunt_result}")
            time.sleep(wait_interval)
            waited += wait_interval

        if not os.path.exists(client_hunt_result):
            print(f"ERROR: Server hunting mode requires {client_hunt_result} to exist")
            print(f"Waited {max_wait} seconds but file was not created")
            print("Client post-processing must complete first to create this file")
            sys.exit(1)
        elif waited > 0:
            print(f"Client hunt result file appeared after {waited:.1f} seconds")

        print(f"Server hunting mode: reading client's selected run from {client_hunt_result}")
        try:
            with open(client_hunt_result, 'r') as fh:
                first_line = fh.readline()
                # Client hunt-temp has "BEGIN-TS-N" where N is the run number
                match = re.search(r'BEGIN-TS-(\d+)', first_line)
                if match:
                    selected_run = int(match.group(1))
                    print(f"Client selected run {selected_run}, extracting test #{selected_run} from server results")
                    extract_server_test(from_file, to_file, selected_run, engine_index)
                    return
                else:
                    print(f"ERROR: Could not find BEGIN-TS-N pattern in {client_hunt_result}")
                    sys.exit(1)
        except Exception as e:
            print(f"ERROR: Failed to read client hunt result: {e}")
            sys.exit(1)

    # Client mode: find highest bitrate PASS
    highest_bitrate = 0
    highest_run_number = 0
    cur_run_number = 0

    try:
        fh, _ = open_read_text_file(from_file)
    except FileNotFoundError:
        return

    for line in fh:
        if "BEGIN-TS" in line:
            cur_run_number += 1
        debug_print(f"Proc line: {line}")
        if "PASS" in line:
            columns = line.split()
            # Find bitrate by searching for "bits/sec" pattern (same as interval parsing)
            bitrate = None
            for i in range(len(columns) - 1):
                if 'bits/sec' in columns[i + 1]:
                    try:
                        bitrate = float(columns[i])
                        break
                    except (ValueError, IndexError):
                        continue

            if bitrate is None:
                # Skip PASS lines where we couldn't find bitrate
                debug_print(f"Could not extract bitrate from PASS line: {line}")
                continue

            if bitrate > highest_bitrate:
                highest_bitrate = bitrate
                highest_run_number = cur_run_number
    fh.close()
    print(f"Highest run num = {highest_run_number}")

    if highest_run_number == 0:
        print("WARNING: No PASS found in hunting results - using last run")
        # Use the last run instead
        highest_run_number = cur_run_number

    fh, _ = open_read_text_file(from_file)
    cur_run_number = 0
    for line in fh:
        if "BEGIN-TS" in line:
            cur_run_number += 1
            if highest_run_number == cur_run_number:
                dup_one_run(fh, line, to_file)
                break
    fh.close()


def extract_server_test(from_file, to_file, test_number, engine_index="1"):
    """Extract a specific test number from server result file.

    Server output has markers like 'Server listening on 30002 (test #N)'
    We also need to read the timestamps from the client's hunt-temp-result.txt
    """
    try:
        fh, _ = open_read_text_file(from_file)
    except FileNotFoundError:
        print(f"ERROR: Could not open {from_file}")
        return

    # Read timestamps from client hunt-temp-result.txt
    client_hunt = f"../../client/{engine_index}/hunt-temp-result.txt"
    begin_ts = None
    end_ts = None
    if os.path.exists(client_hunt):
        with open(client_hunt, 'r') as cfh:
            for line in cfh:
                if f"BEGIN-TS-{test_number}" in line:
                    begin_ts = line.strip()
                elif f"END-TS-{test_number}" in line:
                    end_ts = line.strip()
                    break

    # Find the test marker
    target_marker = f"(test #{test_number})"
    next_test_marker = f"(test #{test_number + 1})"
    in_target_test = False

    # Write to temp file first, then atomic rename to prevent race condition
    temp_file = to_file + ".tmp"
    with open(temp_file, "w") as ofh:
        # Write BEGIN timestamp if found
        if begin_ts:
            ofh.write(begin_ts + "\n")

        for line in fh:
            if target_marker in line:
                in_target_test = True
                debug_print(f"Found start of {target_marker}\n")
                continue  # Skip the "Server listening" line

            if in_target_test:
                if next_test_marker in line or "END-TS" in line:
                    # Reached next test or end marker
                    debug_print(f"Reached end of test {test_number}\n")
                    break
                ofh.write(line)

        # Write END timestamp if found
        if end_ts:
            ofh.write(end_ts + "\n")

    fh.close()

    # Atomic rename - file appears complete or not at all
    os.rename(temp_file, to_file)
    print(f"Extracted test #{test_number} from server results")


def process_proto(data_file, times, names, omit, metrics):
    print("process_proto: enter")
    rateunit = "none"
    bitrate_div = 1
    sample_count = 0
    primary_metric = None
    ts = times["begin"]
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
            ts = times["begin"]
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

            # Find interval using X.XX-Y.YY pattern
            interval_val = None
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

            ts_end = ts + interval_val - 1

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
                        metrics.log_sample("0", desc, names, s)

                        desc = {"source": "iperf", "class": "throughput", "type": "rx-pps"}
                        s = {"begin": int(ts), "end": int(ts_end), "value": total}
                        metrics.log_sample("0", desc, names, s)

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
            metrics.log_sample("0", desc, names, s)
            sample_count += 1
            ts = ts + interval_val

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

            # Find interval using X.XX-Y.YY pattern
            interval_val = None
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

            ts_end = ts + interval_val - 1

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
                        metrics.log_sample("0", desc, names, s)
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
                # Accumulate for later aggregation
                key = (int(ts), int(ts_end))
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
                metrics.log_sample("0", desc, names, s)

            sample_count += 1
            ts = ts + interval_val

    if sample_count == 0:
        primary_metric = "rx-Gbps"
        desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
        s = {"begin": int(times["begin"]), "end": int(times["end"]), "value": 0}
        metrics.log_sample("0", desc, names, s)

    fh.close()

    # Log accumulated bidirectional samples
    if bidir_mode and throughput_samples:
        print(f"Logging {len(throughput_samples)} accumulated bidirectional samples")
        for (begin_ts, end_ts), metrics_dict in sorted(throughput_samples.items()):
            for metric_type, value in metrics_dict.items():
                desc = {"source": "iperf", "class": "throughput", "type": metric_type}
                s = {"begin": begin_ts, "end": end_ts, "value": value}
                metrics.log_sample("0", desc, names, s)
                debug_print(f"Logged {metric_type}: {value} Gbps for period {begin_ts}-{end_ts}\n")

    metric_data_name = metrics.finish_samples(dont_delete=True)

    # Ensure primary_metric is set (default to rx-Gbps if not set)
    if primary_metric is None:
        primary_metric = "rx-Gbps"

    sample_data = {
        "rickshaw-bench-metric": {"schema": {"version": "2021.04.12"}},
        "benchmark": "iperf",
        "primary-period": "measurement",
        "primary-metric": primary_metric,
        "periods": [
            {
                "name": "measurement",
                "metric-files": [metric_data_name],
            }
        ],
    }

    with open("postprocess/post-process-data.json", "w") as f:
        json.dump(sample_data, f)

    return primary_metric


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

    hunting_mode = args.bitrate_range != "0"
    omit_val = args.omit

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

    final_hunt_result = "hunt-temp-result.txt"
    names = {"cmd": "write"}

    if os.path.exists("./begin.txt"):
        print("Cannot process old results with this version. Please use an older bench-iperf version.")
        sys.exit(1)

    metrics = CDMMetrics()

    if not hunting_mode:
        # Extract timestamps from timestamp_file (client file for both client and server)
        begin_out = subprocess.run(
            f"grep 'BEGIN-TS' {timestamp_file} | head -1 | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        end_out = subprocess.run(
            f"grep 'END-TS' {timestamp_file} | head -1 | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        times = {
            "begin": float(begin_out) * SEC_TO_MSEC,
            "end": float(end_out) * SEC_TO_MSEC,
        }
        process_proto(result_file, times, names, omit_val, metrics)
    else:
        # Hunting mode: process selected run from result_file
        # For server, pass client hunt result so it can extract the same run number
        if is_server:
            client_hunt_result = f"../../client/{engine_index}/hunt-temp-result.txt"
            pre_process_hunting_results(result_file, final_hunt_result, client_hunt_result, engine_index)
            timestamp_source = client_hunt_result if os.path.exists(client_hunt_result) else timestamp_file
        else:
            # Client mode - find best run from its own results
            pre_process_hunting_results(result_file, final_hunt_result)
            timestamp_source = final_hunt_result

        begin_out = subprocess.run(
            f"grep BEGIN-TS {timestamp_source} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        end_out = subprocess.run(
            f"grep END-TS {timestamp_source} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        times = {
            "begin": float(begin_out) * SEC_TO_MSEC,
            "end": float(end_out) * SEC_TO_MSEC,
        }
        process_proto(final_hunt_result, times, names, omit_val, metrics)

    # Print status to stdout for test infrastructure (on separate line)
    print("POST-PROCESS-STATUS: success")


if __name__ == "__main__":
    main()
