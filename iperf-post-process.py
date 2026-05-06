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


def dup_one_run(fh, first_line, outfile):
    with open(outfile, "w") as ofh:
        ofh.write(first_line)
        for line in fh:
            ofh.write(line)
            if "END-TS" in line:
                break


def pre_process_hunting_results(from_file, to_file):
    debug_print("process_hunting_results: enter\n")
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
            bitrate = float(columns[7])
            if bitrate > highest_bitrate:
                highest_bitrate = bitrate
                highest_run_number = cur_run_number
    fh.close()
    print(f"Highest run num = {highest_run_number}")

    fh, _ = open_read_text_file(from_file)
    cur_run_number = 0
    for line in fh:
        if "BEGIN-TS" in line:
            cur_run_number += 1
            if highest_run_number == cur_run_number:
                dup_one_run(fh, line, to_file)
                break
    fh.close()


def process_proto(data_file, protocol, times, names, omit, metrics):
    print("process_proto: enter")
    rateunit = "none"
    bitrate_div = 1
    sample_count = 0
    primary_metric = None
    ts = times["begin"]

    try:
        fh, _ = open_read_text_file(data_file)
    except FileNotFoundError:
        print(f"iperf-post-process(): could not open {data_file}")
        print("Is the current directory for a iperf server (no result file)?")
        return None

    for line in fh:
        line = line.rstrip("\n")

        if "sender" in line or "receiver" in line:
            debug_print(f"Skip line: {line}\n")
            ts = times["begin"]
            rateunit = "none"
            continue

        if "SUM" in line:
            continue

        if "omitted" in line:
            continue

        if protocol == "udp":
            if " sec " in line:
                debug_print(f"Proc line: {line}\n")
                columns = line.split()

                tuble = columns[2].split("-")
                start = float(tuble[0])
                end = float(tuble[1])
                sec_delta = end - start

                if sec_delta == 0:
                    continue

                interval = sec_delta * SEC_TO_MSEC
                debug_print(f"interval={interval}\n")

                bitrate = float(columns[6])
                debug_print(f"bitrate {bitrate}\n")

                if rateunit == "none":
                    rateunit = columns[7]
                    bitrate_div = get_rate_divisor(rateunit)

                ts_end = ts + interval - 1
                num_fields = len(columns)

                if num_fields > 10:
                    lost_total = columns[10]
                    tuble = lost_total.split("/")
                    lost = int(tuble[0])
                    total = int(tuble[1])
                    debug_print(f"Lost:{lost} Total:{total}\n")

                    if omit != 0 and end <= omit:
                        debug_print(f"fudge lost value due to on-demand omit, line: {line}\n")
                        lost = 0

                    desc = {"source": "iperf", "class": "throughput", "type": "rx-lost/sec"}
                    s = {"begin": int(ts), "end": int(ts_end), "value": lost}
                    debug_print(f"log_sample: lost={lost}\n")
                    metrics.log_sample("0", desc, names, s)

                    desc = {"source": "iperf", "class": "throughput", "type": "rx-pps"}
                    s = {"begin": int(ts), "end": int(ts_end), "value": total}
                    metrics.log_sample("0", desc, names, s)

                    if primary_metric is None:
                        primary_metric = "rx-Gbps"
                        debug_print(f"primary_metric {primary_metric}\n")

                    debug_print(f"log_sample: primary_metric={primary_metric}\n")
                    desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
                else:
                    desc = {"source": "iperf", "class": "throughput", "type": "tx-Gbps"}

                s = {"begin": int(ts), "end": int(ts_end), "value": bitrate / bitrate_div}
                debug_print(f"begin: int {ts}, end: int {ts_end}\n")
                metrics.log_sample("0", desc, names, s)
                sample_count += 1
                ts = ts + interval
            else:
                debug_print(f"Skip line: {line}\n")

        else:
            # TCP
            if re.search(r'sec\s', line):
                debug_print(f"Proc line: {line}\n")
                columns = line.split()

                tuble = columns[2].split("-")
                start = float(tuble[0])
                end = float(tuble[1])
                sec_delta = end - start
                interval = sec_delta * SEC_TO_MSEC
                debug_print(f"interval={interval}\n")

                if sec_delta == 0:
                    continue

                bitrate = float(columns[6])
                debug_print(f"bitrate {bitrate}\n")

                if rateunit == "none":
                    rateunit = columns[7]
                    bitrate_div = get_rate_divisor(rateunit)

                num_fields = len(columns)
                debug_print(f"num_fields: {num_fields}\n")
                ts_end = ts + interval - 1

                if num_fields > 8:
                    retry = int(columns[8])
                    debug_print(f"Retry: {retry}\n")

                    desc = {"source": "iperf", "class": "count", "type": "tx-retry/sec"}
                    s = {"begin": int(ts), "end": int(ts_end), "value": retry}
                    metrics.log_sample("0", desc, names, s)

                    desc = {"source": "iperf", "class": "throughput", "type": "tx-Gbps"}
                else:
                    if primary_metric is None:
                        primary_metric = "rx-Gbps"
                        debug_print(f"primary_metric {primary_metric}\n")
                    desc = {"source": "iperf", "class": "throughput", "type": primary_metric}

                s = {"begin": int(ts), "end": int(ts_end), "value": bitrate / bitrate_div}
                metrics.log_sample("0", desc, names, s)
                sample_count += 1
                ts = ts + interval
            else:
                debug_print(f"Skip line: {line}\n")

    if sample_count == 0:
        primary_metric = "rx-Gbps"
        desc = {"source": "iperf", "class": "throughput", "type": primary_metric}
        s = {"begin": int(times["begin"]), "end": int(times["end"]), "value": 0}
        metrics.log_sample("0", desc, names, s)

    fh.close()
    metric_data_name = metrics.finish_samples(dont_delete=True)

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

    with open("post-process-data.json", "w") as f:
        json.dump(sample_data, f)

    return primary_metric


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--remotehost", default=None)
    parser.add_argument("--length", default=None)
    parser.add_argument("--passthru", default=None)
    parser.add_argument("--protocol", default=None)
    parser.add_argument("--time", type=int, default=None)
    parser.add_argument("--bitrate", default=None)
    parser.add_argument("--max-loss-pct", default="0")
    parser.add_argument("--ifname", default=None)
    parser.add_argument("--cpu-pin", default=None)
    parser.add_argument("--bitrate-range", default="0")
    parser.add_argument("--omit", type=int, default=0)
    parser.add_argument("--ipv", type=int, default=None)
    args, _ = parser.parse_known_args()

    print(" ".join(sys.argv[1:]))

    protocol = args.protocol
    hunting_mode = args.bitrate_range != "0"
    omit_val = args.omit

    result_file = "iperf-client-result.txt"
    final_hunt_result = "hunt-temp-result.txt"
    names = {"cmd": "write"}

    if os.path.exists("./begin.txt"):
        print("Cannot process old results with this version. Please use an older bench-iperf version.")
        sys.exit(1)

    metrics = CDMMetrics()

    if not hunting_mode:
        begin_out = subprocess.run(
            f"grep BEGIN-TS {result_file} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        end_out = subprocess.run(
            f"grep END-TS {result_file} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        times = {
            "begin": float(begin_out) * SEC_TO_MSEC,
            "end": float(end_out) * SEC_TO_MSEC,
        }
        process_proto(result_file, protocol, times, names, omit_val, metrics)
    else:
        pre_process_hunting_results(result_file, final_hunt_result)
        begin_out = subprocess.run(
            f"grep BEGIN-TS {final_hunt_result} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        end_out = subprocess.run(
            f"grep END-TS {final_hunt_result} | awk '{{print $2}}'",
            shell=True, capture_output=True, text=True
        ).stdout.strip()
        times = {
            "begin": float(begin_out) * SEC_TO_MSEC,
            "end": float(end_out) * SEC_TO_MSEC,
        }
        process_proto(final_hunt_result, protocol, times, names, omit_val, metrics)


if __name__ == "__main__":
    main()
