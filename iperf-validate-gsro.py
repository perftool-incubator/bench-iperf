#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import json
from pathlib import Path
import re
import sys


RUN_CONFIG = Path("../config/rickshaw-run.json")


def parameter_values(parameter):
    value = parameter.get("val")
    if value is not None:
        return [value]
    return parameter.get("vals", [])


def is_client_parameter(parameter):
    return parameter.get("role", "client") in ("client", "all")


def parameter_id_scope(parameter):
    value = parameter.get("id")
    if value is None:
        return None
    return set(str(value).split("+"))


def expand_id_ranges(ids):
    expanded = set()
    for segment in str(ids).split(","):
        for identifier in segment.split("+"):
            match = re.fullmatch(r"(\d+)-(\d+)", identifier)
            if match:
                expanded.update(
                    str(value)
                    for value in range(int(match.group(1)), int(match.group(2)) + 1)
                )
            elif identifier.isdigit():
                expanded.add(identifier)
    return expanded


def iperf_client_ids(run):
    client_ids = set()
    for benchmark_and_ids in run.get("bench-ids", "").split(","):
        benchmark, separator, ids = benchmark_and_ids.partition(":")
        if separator and benchmark == "iperf":
            client_ids.update(expand_id_ranges(ids))
    return client_ids


def parameter_applies_to_client(parameter, client_id):
    if parameter.get("benchmark", "iperf") != "iperf":
        return False
    parameter_ids = parameter_id_scope(parameter)
    if client_id is None:
        return parameter_ids is None
    return parameter_ids is None or str(client_id) in parameter_ids


def protocol_values_for_client(parameters, client_id):
    return [
        str(value)
        for parameter in parameters
        if parameter.get("arg") == "protocol"
        and is_client_parameter(parameter)
        and parameter_applies_to_client(parameter, client_id)
        for value in parameter_values(parameter)
    ]


def validate_parameters(parameters, client_ids=None):
    errors = []
    messages = []
    if client_ids is not None:
        client_ids = {str(client_id) for client_id in client_ids}

    for index, parameter in enumerate(parameters, 1):
        if parameter.get("arg") != "gsro":
            continue
        if parameter.get("benchmark", "iperf") != "iperf":
            continue

        role = parameter.get("role", "client")
        if role == "server":
            errors.append(f"parameter {index}: gsro has role 'server'; use client or all")
            continue
        if role not in ("client", "all"):
            errors.append(f"parameter {index}: gsro has unsupported role '{role}'")
            continue

        if role == "all":
            messages.append(
                "parameter "
                f"{index}: gsro uses role 'all'; the server ignores it and the client negotiates "
                "GSO/GRO through the control connection"
            )

        values = parameter_values(parameter)
        if not values:
            errors.append(f"parameter {index}: gsro has no value")
        for value in values:
            if value not in ("true", "false"):
                errors.append(f"parameter {index}: invalid gsro value '{value}': expected true or false")
                continue
            if value == "true":
                gsro_ids = parameter_id_scope(parameter)
                if gsro_ids is None:
                    if client_ids is not None:
                        affected_ids = client_ids
                    else:
                        affected_ids = {
                            identifier
                            for candidate in parameters
                            for identifier in (parameter_id_scope(candidate) or ())
                        }
                        affected_ids.add(None)
                else:
                    affected_ids = gsro_ids
                    if client_ids is not None:
                        affected_ids &= client_ids
                if not affected_ids:
                    continue
                for client_id in affected_ids:
                    protocols = protocol_values_for_client(parameters, client_id)
                    if not protocols or any(protocol != "udp" for protocol in protocols):
                        errors.append(f"parameter {index}: gsro=true requires protocol=udp")
                        break

    return errors, messages


def validate_run(run):
    errors = []
    messages = []
    iterations = run.get("iterations", [])
    client_ids = iperf_client_ids(run) if "bench-ids" in run else None
    if client_ids is not None and not client_ids:
        client_ids = None
    if not isinstance(iterations, list):
        return ["run iterations must be a list"], messages

    for iteration_index, iteration in enumerate(iterations, 1):
        parameters = iteration.get("params", []) if isinstance(iteration, dict) else []
        if not isinstance(parameters, list):
            errors.append(f"iteration {iteration_index}: params must be a list")
            continue
        iteration_errors, iteration_messages = validate_parameters(parameters, client_ids)
        errors.extend(f"iteration {iteration_index}: {error}" for error in iteration_errors)
        messages.extend(f"iteration {iteration_index}: {message}" for message in iteration_messages)

    return errors, messages


def main():
    try:
        with RUN_CONFIG.open(encoding="utf-8") as config_file:
            run = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"ERROR: could not read {RUN_CONFIG}: {error}", file=sys.stderr)
        return 2

    errors, messages = validate_run(run)
    for message in messages:
        print(message)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
