#!/bin/bash
# -*- mode: sh; indent-tabs-mode: nil; sh-basic-offset: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=bash

# Bootstrap script for iperf post-processor test infrastructure
# Creates a Python virtual environment with required dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_VERSION="python3.11"

echo "========================================"
echo "iperf Post-Processor Test Bootstrap"
echo "========================================"
echo

# Check if Python 3.11 is available
if ! command -v ${PYTHON_VERSION} &> /dev/null; then
    echo "ERROR: ${PYTHON_VERSION} not found"
    echo "Please install Python 3.11 or higher"
    exit 1
fi

# Create venv if it doesn't exist
if [ -d "${VENV_DIR}" ]; then
    echo "I am using existing venv at ${VENV_DIR}"
    echo "To recreate, delete it first: rm -rf ${VENV_DIR}"
else
    echo "Creating Python virtual environment at ${VENV_DIR}"

    # Python venv refuses to create in paths with colons (like GitHub URLs)
    # Workaround: Use virtualenv if available, otherwise use symlink-free path via /opt/crucible/subprojects
    if command -v virtualenv &> /dev/null; then
        virtualenv -p ${PYTHON_VERSION} "${VENV_DIR}"
    else
        # Try to find symlink-free path
        SYMLINK_FREE_DIR="/opt/crucible/subprojects/benchmarks/iperf/unit-test/.venv"
        if [ -d "/opt/crucible/subprojects/benchmarks/iperf" ]; then
            echo "Using symlink-free path for venv creation"
            ${PYTHON_VERSION} -m venv "${SYMLINK_FREE_DIR}"
            # Create symlink from repos path to subprojects path if different
            if [ "${VENV_DIR}" != "${SYMLINK_FREE_DIR}" ]; then
                ln -s "${SYMLINK_FREE_DIR}" "${VENV_DIR}"
            fi
        else
            echo "ERROR: Cannot create venv - path contains ':' (PATH separator)"
            echo "Install virtualenv: pip install virtualenv"
            echo "Or use the symlink path: /opt/crucible/subprojects/benchmarks/iperf/unit-test/"
            exit 1
        fi
    fi
    echo "Virtual environment created successfully"
fi

echo
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo
echo "To run tests:"
echo "  cd ${SCRIPT_DIR}"
echo "  ./run-tests.py"
echo
