# iperf Post-Processor Testing

## Setup

First-time setup - create the Python virtual environment:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test
./bootstrap.sh
```

Or using the resolved path:

```bash
cd /opt/crucible/repos/https:github.com:perftool-incubator/bench-iperf/unit-test
./bootstrap.sh
```

This creates a local `.venv` directory with Python 3.11.

## Running Tests

After bootstrap, run the test suite:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test
./run-tests.py
```

## Test Cases

The test suite includes 8 test cases with real production data:

1. **missing-timestamps** - Validates failure detection (missing BEGIN-TS/END-TS)
2. **tcp-unidirectional-client** - TCP sender with tx-Gbps and tx-retry/sec
3. **tcp-unidirectional-server** - TCP receiver with rx-Gbps (catches the bug we fixed!)
4. **tcp-bidirectional-client** - TCP bidir client with tx-Gbps and rx-Gbps
5. **tcp-bidirectional-server** - TCP bidir server with tx-Gbps and rx-Gbps
6. **udp-sender** - UDP sender with tx-Gbps
7. **udp-receiver** - UDP receiver with rx-Gbps, rx-lost/sec, rx-pps
8. **tcp-hunter-mode** - Multiple bitrate runs with PASS/FAIL selection

## What the Tests Validate

- ✅ Script runs without Python errors
- ✅ Success marker "POST-PROCESS-STATUS: success" appears in output
- ✅ Failure detection works (missing-timestamps test)
- ✅ All protocol/direction/mode combinations covered

## Debugging Post-Processor Changes

When you modify `iperf-post-process.py` and introduce a bug or want to test changes:

### 1. Run the test suite to identify failures

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test
./run-tests.py
```

The test output shows which scenarios fail and displays STDOUT/STDERR from the post-processor.

### 2. Debug a specific test case manually

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test

# Copy the failing test's data to parent directory
cp test-data/tcp-unidirectional-server/iperf-server-result.txt ..
cp test-data/tcp-unidirectional-server/iperf-client-result.txt ..

# Run post-processor directly using the venv Python
cd ..
unit-test/.venv/bin/python3 iperf-post-process.py --protocol=tcp --ipv=4 --ifname=eth0
```

### 3. Iterate and fix

- Make fixes to `iperf-post-process.py`
- Re-run `./run-tests.py` from unit-test directory
- All 8 tests should pass before committing changes

### 4. Add new test cases for bug fixes

If you fix a bug, add a test case to prevent regression:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test

# Create new test case directory
mkdir test-data/my-bug-fix-case

# Copy relevant iperf result files from a real run
cp /path/to/iperf-client-result.txt test-data/my-bug-fix-case/

# Create expected-metrics.json
cat > test-data/my-bug-fix-case/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "expected_metrics": ["rx-Gbps"],
  "description": "Description of what this test validates"
}
EOF
```

**Benefits:**
- ✅ Fast feedback loop (no need for full crucible runs)
- ✅ Tests 8 different scenarios automatically
- ✅ Catches regressions before they reach production
- ✅ Consistent local test environment

## Refreshing Test Samples

To update test samples from latest regulus runs:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test
./extract-test-samples.sh
```

This extracts fresh samples from `/home/hnhan/JPMC/jpmc-regulus/2_GROUP/NO-PAO/4IP/` runs into `unit-test/test-data/`.
