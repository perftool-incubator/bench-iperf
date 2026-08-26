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

The test suite includes 10 test cases with real production data:

1. **missing-timestamps** - Validates failure detection (missing BEGIN-TS/END-TS)
2. **tcp-unidirectional-client** - TCP sender with tx-Gbps and tx-retry/sec
3. **tcp-unidirectional-server** - TCP receiver with rx-Gbps (catches the bug we fixed!)
4. **tcp-bidirectional-client** - TCP bidir client with tx-Gbps and rx-Gbps
5. **tcp-bidirectional-server** - TCP bidir server with tx-Gbps and rx-Gbps
6. **udp-sender** - UDP sender with tx-Gbps
7. **udp-receiver** - UDP receiver with rx-Gbps, rx-lost/sec, rx-pps
8. **tcp-hunter-mode** - Multiple bitrate runs with PASS/FAIL selection
9. **tcp-parallel-streams-client** - TCP sender with `--parallel 4` (nthreads=4, iperf3 3.16+); each stream logged as its own `stream` breakout
10. **tcp-parallel-streams-server** - TCP receiver side of the same 4-stream run

Each case runs in a `sample-1/<role>/1/` directory (matching how rickshaw
actually lays out engine working directories) so client-mode and
server-mode are never ambiguous, even when a case ships both result files
(server mode needs the client file only to read its BEGIN-TS/END-TS).
Set `"role": "server"` in `expected-metrics.json` for server-side cases;
it defaults to `"client"`.

## What the Tests Validate

- ✅ Script runs without Python errors
- ✅ Success marker "POST-PROCESS-STATUS: success" appears in output
- ✅ Failure detection works (missing-timestamps test)
- ✅ All protocol/direction/mode combinations covered
- ✅ The exact set of metric types logged matches `expected_metrics` (not just that *something* was logged)
- ✅ Optional `expected_stream_count` asserts how many distinct `stream` breakout values were logged (guards the per-thread/`nthreads` breakout against silently collapsing back into one series)
- ✅ Sample timestamps never extend past the actual test window (guards against timestamps advancing per output row instead of per real interval, which would stretch the series out by a factor of `nthreads`)

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

# Lay out a sample-1/server/1/ dir (server mode also needs the client
# file alongside, purely to read its BEGIN-TS/END-TS timestamps)
mkdir -p /tmp/iperf-debug/sample-1/server/1 /tmp/iperf-debug/sample-1/client/1
cp test-data/tcp-unidirectional-server/iperf-server-result.txt /tmp/iperf-debug/sample-1/server/1/
cp test-data/tcp-unidirectional-server/iperf-client-result.txt /tmp/iperf-debug/sample-1/client/1/

# Run post-processor directly using the venv Python, from the role dir
cd /tmp/iperf-debug/sample-1/server/1
/opt/crucible/subprojects/benchmarks/iperf/unit-test/.venv/bin/python3 \
  /opt/crucible/subprojects/benchmarks/iperf/iperf-post-process.py --protocol=tcp --ipv=4 --ifname=eth0
```

### 3. Iterate and fix

- Make fixes to `iperf-post-process.py`
- Re-run `./run-tests.py` from unit-test directory
- All 10 tests should pass before committing changes

### 4. Add new test cases for bug fixes

If you fix a bug, add a test case to prevent regression:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test

# Create new test case directory
mkdir test-data/my-bug-fix-case

# Copy relevant iperf result files from a real run
cp /path/to/iperf-client-result.txt test-data/my-bug-fix-case/

# Create expected-metrics.json (role defaults to "client"; add
# "expected_stream_count": N to assert a specific number of distinct
# per-thread "stream" breakout series)
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
- ✅ Tests 10 different scenarios automatically
- ✅ Catches regressions before they reach production
- ✅ Consistent local test environment

## Refreshing Test Samples

To update test samples from latest regulus runs:

```bash
cd /opt/crucible/subprojects/benchmarks/iperf/unit-test
./extract-test-samples.sh
```

This extracts fresh samples from `/home/hnhan/JPMC/jpmc-regulus/2_GROUP/NO-PAO/4IP/` runs into `unit-test/test-data/`.
