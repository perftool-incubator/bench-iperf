# iperf Post-Processing Implementation

## Overview

The iperf post-processing pipeline parses iperf3 output and converts it into CDM (Common Data Model) metrics for indexing in OpenSearch. It supports both unidirectional and bidirectional traffic modes, TCP and UDP protocols, and hunter mode for automatic bitrate optimization.

## Key Components

### Main Scripts

- **iperf-post-process.py** - Primary post-processor, handles all iperf output formats
- **iperf-post-process-with-validation.py** - Wrapper that validates new code against upstream version
- **upstream-iperf-post-process.py** - Original implementation for comparison/validation

### File Locations

Post-processing runs in these directories:
```
<run-dir>/run/iterations/iteration-N/sample-N/client/1/
<run-dir>/run/iterations/iteration-N/sample-N/server/1/
```

## Architecture

### Client vs Server Processing

#### Client Side
- Processes: `iperf-client-result.txt`
- Contains: Client TX metrics, embedded server RX metrics (if using `--get-server-output`)
- Generates: TX-focused metrics (tx-Gbps, tx-retry/sec for TCP)
- Timestamps: BEGIN-TS and END-TS directly in client output

#### Server Side
- Processes: `iperf-server-result.txt`
- Contains: Server RX metrics
- Generates: RX-focused metrics (rx-Gbps, rx-pps, rx-lost/sec for UDP)
- Timestamps: Read from client result file at `../../client/1/iperf-client-result.txt`

**Critical Design Decision**: Server ALWAYS reads timestamps from the client result file because:
1. Client and server clocks may not be synchronized
2. Metrics must have aligned timestamps for proper correlation
3. CDM requires consistent time references across client/server pairs

### Metric Flow

```
iperf raw output
    ↓
parse headers to identify columns dynamically
    ↓
extract interval data (bitrate, Lost/Total, Retr, etc.)
    ↓
CDMMetrics.log_sample() for each metric type
    ↓
CDMMetrics.finish_samples(dont_delete=True)
    ↓
metric-data-0.json.xz (metric descriptors)
metric-data-0.csv.xz (sample values)
    ↓
rickshaw-gen-docs.py (indexing)
    ↓
OpenSearch
```

## Header-Based Column Parsing

### Problem
iperf3 output format varies by protocol and mode:
- UDP sender: `[ ID] Interval Transfer Bitrate Total Datagrams`
- UDP receiver: `[ ID] Interval Transfer Bitrate Jitter Lost/Total Datagrams`
- TCP sender: `[ ID] Interval Transfer Bitrate Retr`
- TCP receiver: `[ ID] Interval Transfer Bitrate`
- Bidirectional: `[ ID][Role] Interval Transfer Bitrate ...`

Hardcoded column indices fail when format changes.

### Solution
**Two-phase parsing:**

1. **Header Detection** (lines 217-230):
```python
if "[ ID]" in line and "Interval" in line:
    columns = line.split()
    for i, col in enumerate(columns):
        if "Interval" in col:
            col_interval = i
        elif "Bitrate" in col:
            col_bitrate = i
        elif "Lost/Total" in col:
            col_lost_total = i
        elif "Retr" in col:
            col_retr = i
```

2. **Dynamic Data Extraction** (lines 268-278 for bitrate example):
```python
# Find bitrate value by searching for "bits/sec" pattern
for i in range(len(columns) - 1):
    if 'bits/sec' in columns[i + 1]:
        bitrate = float(columns[i])
        bitrate_unit_idx = i + 1
        break
```

**Why two-phase?**
- Header columns don't match data column positions
- Header: `Bitrate` is one word at index N
- Data: `199045 Kbits/sec` is two words spanning multiple indices
- Solution: Use header to understand structure, then search data for patterns

### Column Finding Strategies

| Metric | Header Hint | Data Search Pattern | Notes |
|--------|-------------|---------------------|-------|
| Interval | `col_interval` | Use header index directly | Reliable position |
| Bitrate | `col_bitrate` | Find `'bits/sec'` in next column | Value before unit |
| Lost/Total | `col_lost_total` | Find `/` pattern (excluding `bits/sec`) | Format: `0/20735` |
| Retr | `col_retr` | Use header index if available | TCP sender only |

## iperf Output Formats

This section shows actual iperf3 output examples for different modes to help understand what the post-processor parses.

### TCP Unidirectional - TX Side (Client)

**File**: `iperf-client-result.txt`

**Header format**: `[ ID] Interval Transfer Bitrate Retr Cwnd`

**Key characteristics**:
- `Retr` column present (identifies TCP sender)
- Generates `tx-Gbps` and `tx-retry/sec` metrics

**Example output**:
```
BEGIN-TS-0 1779554823.073021327
Connecting to host 172.30.39.93, port 30002
[  5] local 10.131.0.80 port 53526 connected to 172.30.39.93 port 30002
[ ID] Interval           Transfer     Bitrate         Retr  Cwnd
[  5]   0.00-1.00   sec   560 MBytes  4696000 Kbits/sec   64    924 KBytes
[  5]   1.00-2.00   sec   537 MBytes  4506472 Kbits/sec    0    968 KBytes
[  5]   2.00-3.00   sec   537 MBytes  4503413 Kbits/sec    0    982 KBytes
...
END-TS-0 1779554883.072978496
```

### TCP Unidirectional - RX Side (Server)

**File**: `iperf-server-result.txt`

**Header format**: `[ ID] Interval Transfer Bitrate`

**Key characteristics**:
- No `Retr` column (identifies TCP receiver)
- Generates `rx-Gbps` metric
- Timestamps read from client file at `../../client/1/iperf-client-result.txt`

**Example output**:
```
BEGIN-TS-0 1779554780.163774594
-----------------------------------------------------------
Server listening on 30002 (test #1)
-----------------------------------------------------------
Accepted connection from 10.131.0.80, port 53522
[  5] local 10.128.2.58 port 30002 connected to 10.131.0.80 port 53526
[ ID] Interval           Transfer     Bitrate
[  5]   0.00-1.00   sec   558 MBytes  4676843 Kbits/sec
[  5]   1.00-2.00   sec   537 MBytes  4504919 Kbits/sec
[  5]   2.00-3.00   sec   537 MBytes  4504719 Kbits/sec
...
```

**Note**: Server output does NOT contain BEGIN-TS/END-TS markers - timestamps are always read from the client result file for clock synchronization.

### TCP Bidirectional Mode

**File**: `iperf-client-result.txt` (using `--bidir` flag)

**Header format**: `[ ID][Role] Interval Transfer Bitrate Retr Cwnd`

**Key characteristics**:
- Role markers in brackets: `[TX-C]`, `[RX-C]`, `[TX-S]`, `[RX-S]`
- Multiple connection IDs (one for TX, one for RX)
- Post-processor detects bidirectional mode from role markers
- Generates both `tx-Gbps` and `rx-Gbps` metrics

**Example output**:
```
BEGIN-TS-0 1779563482.253489588
Connecting to host 172.30.144.25, port 30002
[  5] local 10.131.0.84 port 46620 connected to 172.30.144.25 port 30002
[  7] local 10.131.0.84 port 46632 connected to 172.30.144.25 port 30002
[ ID][Role] Interval           Transfer     Bitrate         Retr  Cwnd
[  5][TX-C]   0.00-1.00   sec  59.6 MBytes  499619 Kbits/sec    0    207 KBytes       (omitted)
[  7][RX-C]   0.00-1.00   sec  59.6 MBytes  499614 Kbits/sec                  (omitted)
[  5][TX-C]   1.00-2.00   sec  59.6 MBytes  499904 Kbits/sec    0    207 KBytes       (omitted)
[  7][RX-C]   1.00-2.00   sec  59.6 MBytes  499904 Kbits/sec                  (omitted)
[  5][TX-C]   0.00-1.00   sec  59.6 MBytes  499905 Kbits/sec    0    207 KBytes
[  7][RX-C]   0.00-1.00   sec  59.6 MBytes  499829 Kbits/sec
[  5][TX-C]   1.00-2.00   sec  59.6 MBytes  500175 Kbits/sec    0    207 KBytes
[  7][RX-C]   1.00-2.00   sec  59.6 MBytes  499988 Kbits/sec
...
END-TS-0 1779563542.253489588
```

**Role marker meanings**:
- `TX-C` - Client transmitting (generates tx-Gbps, tx-retry/sec)
- `RX-C` - Client receiving (generates rx-Gbps)
- `TX-S` - Server transmitting (in server output file)
- `RX-S` - Server receiving (in server output file)

**Processing details**:
- Post-processor aggregates TX and RX samples separately by timestamp
- Samples with same timestamp are grouped: `(begin_ts, end_ts) -> {tx: [values], rx: [values]}`
- Both TX and RX metrics are logged for the same time intervals

### UDP Sender

**Header format**: `[ ID] Interval Transfer Bitrate Total Datagrams`

**Key characteristics**:
- `Datagrams` column present
- No `Lost/Total` column (identifies sender)
- Generates `tx-Gbps` metric

### UDP Receiver

**Header format**: `[ ID] Interval Transfer Bitrate Jitter Lost/Total Datagrams`

**Key characteristics**:
- `Lost/Total` column present (identifies receiver)
- `Datagrams` column present
- Generates `rx-Gbps`, `rx-lost/sec`, and `rx-pps` metrics

**Example Lost/Total parsing**:
```
[  5]   0.00-1.00   sec  59.6 MBytes  499904 Kbits/sec  0.012 ms  0/42850 (0%)
```
- Lost: `0` packets
- Total: `42850` packets
- Loss percentage: `0%`

## Hunter Mode Processing

### Trial detection and selection

The client result contains one `BEGIN-TS-N`/`END-TS-N` block per sequential hunt
trial. The post-processor splits those blocks, records their timestamps, and
captures the `PASS:`/`FAIL:` result line. Hunting is detected from multiple trial
markers even when the server post-processor is invoked without
`--bitrate-range`.

The primary trial is the highest-bitrate `PASS`; a bitrate tie selects the later
trial. If no confirm trial passes, the sample fails rather than publishing an
over-threshold result.

### Client/server alignment

Server output contains `Server listening on <port> (test #N)` markers. The
post-processor splits each numbered server test and pairs it with client trial
`N`. Both sides derive their period timestamps from that client trial's
`BEGIN-TS-N` and `END-TS-N`, eliminating client/server timestamp drift.

### Periods and metric files

Every trial is emitted as a CDM period:

- The selected primary trial is named `measurement` and uses metric file prefix
  `metric-data-0`.
- Every other trial is named `hunt-trial-N` and uses a matching metric file
  prefix.
- `post-process-data.json` lists the periods in trial order.
- System metrics collected during the run can be queried against any hunt
  period.

### Hunter Mode Directory Structure

```text
<run-dir>/
├── client/1/
│   ├── iperf-client-result.txt
│   └── postprocess/
│       ├── client-trial-N.txt
│       ├── metric-data-0.{json,csv}.xz
│       └── metric-data-hunt-trial-N.{json,csv}.xz
└── server/1/
    ├── iperf-server-result.txt
    └── postprocess/
        ├── server-test-N.txt
        ├── metric-data-0.{json,csv}.xz
        └── metric-data-hunt-trial-N.{json,csv}.xz
```
## Metric Types Collected

### UDP Metrics

| Metric Type | Source | Description | When Available |
|-------------|--------|-------------|----------------|
| tx-Gbps | Client | Transmit throughput | Sender side |
| rx-Gbps | Server | Receive throughput | Receiver side |
| rx-pps | Server | Packets per second | Receiver side (from Total) |
| rx-lost/sec | Server | Dropped packets per second | Receiver side (from Lost) |

**UDP Lost/Total Parsing** (lines 290-342):
```python
# Find Lost/Total column by searching for X/Y pattern
for i, col in enumerate(columns):
    if "/" in col and "bits/sec" not in col:
        lost_total_idx = i
        break

# Example: "0/20735(0%)" or "0/20735 (0%)"
lost_total = columns[lost_total_idx]
lost_total_clean = lost_total.split("(")[0]  # Remove "(0%)" part
parts = lost_total_clean.split("/")
lost = int(parts[0])   # 0
total = int(parts[1])  # 20735
```

**Logging order matters** for idx assignment:
```python
# rx-pps logged FIRST → idx=0 (if rx-lost/sec filtered)
metrics.log_sample("0", {"type": "rx-pps"}, names, s)

# rx-lost/sec logged SECOND → idx=0 (if kept), idx=1 (if rx-pps exists)
metrics.log_sample("0", {"type": "rx-lost/sec"}, names, s)

# rx-Gbps logged THIRD
metrics.log_sample("0", {"type": primary_metric}, names, s)
```

### TCP Metrics

| Metric Type | Source | Description | When Available |
|-------------|--------|-------------|----------------|
| tx-Gbps | Client | Transmit throughput | Sender side |
| rx-Gbps | Server | Receive throughput | Receiver side |
| tx-retry/sec | Client | TCP retransmits | Sender side (Retr column) |

**TCP Retry Parsing** (lines 400-412):
```python
# Check for retry field using header-based index
if col_retr is not None and col_retr < len(columns):
    try:
        retry = int(columns[col_retr])
        is_tx = True

        desc = {"source": "iperf", "class": "count", "type": "tx-retry/sec"}
        s = {"begin": int(ts), "end": int(ts_end), "value": retry}
        metrics.log_sample("0", desc, names, s)
    except (ValueError, IndexError):
        pass
```

### Bidirectional Mode

**Detection** (lines 356-362):
```python
role = extract_role(line)  # Extract from "[ ID][TX-C]" pattern
if role and not bidir_mode:
    bidir_mode = True
    print("BIDIRECTIONAL MODE DETECTED from role markers in output")
```

**Role Markers**:
- `TX-C` / `RX-C` - Client transmit/receive
- `TX-S` / `RX-S` - Server transmit/receive

**Aggregation** (lines 415-442):
```python
# Determine metric type from role
metric_type = 'tx-Gbps' if role.startswith('TX') else 'rx-Gbps'

# Accumulate samples by timestamp
key = (int(ts), int(ts_end))
if key not in throughput_samples:
    throughput_samples[key] = {}
throughput_samples[key][metric_type] += throughput_value
```

**Logging** (lines 462-470):
```python
# After processing all lines, log accumulated samples
for (begin_ts, end_ts), metrics_dict in sorted(throughput_samples.items()):
    for metric_type, value in metrics_dict.items():
        desc = {"source": "iperf", "class": "throughput", "type": metric_type}
        s = {"begin": begin_ts, "end": end_ts, "value": value}
        metrics.log_sample("0", desc, names, s)
```

## CDMMetrics Integration

### Zero-Value Handling

**Flag**: `dont_delete=True` (line 464)
```python
metric_data_name = metrics.finish_samples(dont_delete=True)
```

**Why needed**:
- CDMMetrics default behavior: Filter out metrics where ALL values are 0
- Problem: rx-lost/sec = 0 means "no packet loss" (good), not "no data" (missing)
- Solution: `dont_delete=True` preserves zero-value metrics
- Benefit: Consistent metric schema regardless of packet loss/retransmits

**Without dont_delete=True**:
```
# Zero packet loss run
source: iperf
  types: rx-Gbps rx-pps tx-Gbps
  # rx-lost/sec MISSING!
```

**With dont_delete=True**:
```
# Zero packet loss run
source: iperf
  types: rx-Gbps rx-lost/sec rx-pps tx-Gbps
  # rx-lost/sec present with value 0.00
```

### Index Assignment

CDMMetrics assigns indices sequentially based on first log_sample() call order:

```python
# First unique metric logged → idx=0
metrics.log_sample("0", {"type": "rx-lost/sec"}, ...)  # idx=0

# Second unique metric logged → idx=1
metrics.log_sample("0", {"type": "rx-pps"}, ...)       # idx=1

# Third unique metric logged → idx=2
metrics.log_sample("0", {"type": "rx-Gbps"}, ...)      # idx=2
```

**Output**: `metric-data-0.json.xz`
```json
[
  {"idx": 0, "desc": {"type": "rx-lost/sec"}, ...},
  {"idx": 1, "desc": {"type": "rx-pps"}, ...},
  {"idx": 2, "desc": {"type": "rx-Gbps"}, ...}
]
```

**Output**: `metric-data-0.csv.xz`
```
0,1779449620867,1779449621866,0
1,1779449620867,1779449621866,20735
2,1779449620867,1779449621866,0.199045
0,1779449621867,1779449622866,0
1,1779449621867,1779449622866,20729
2,1779449621867,1779449622866,0.198997
```

Format: `idx,begin_timestamp,end_timestamp,value`

### Sample Aggregation

CDMMetrics deduplicates consecutive samples with identical values:

```python
# Input: 10 samples of rx-lost/sec, all with value=0
log_sample("0", {"type": "rx-lost/sec"}, {"begin": 1000, "end": 1999, "value": 0})
log_sample("0", {"type": "rx-lost/sec"}, {"begin": 2000, "end": 2999, "value": 0})
log_sample("0", {"type": "rx-lost/sec"}, {"begin": 3000, "end": 3999, "value": 0})
# ... 7 more samples with value=0

# Output: 1 aggregated sample spanning entire period
# CSV: 0,1000,9999,0
```

**When aggregation happens**:
- Same idx
- Same value
- Consecutive timestamps

**Why this matters**:
- Reduces storage for stable metrics
- Maintains time coverage
- Query results show aggregated value over longer period

## Primary Metric Selection

The `primary-metric` field in `post-process-data.json` determines which metric appears in summary reports.

**Selection Logic** (lines 326-335, 418-430, 474-476):

1. **UDP Receiver** (has Lost/Total):
   ```python
   if primary_metric is None:
       primary_metric = "rx-Gbps"
   ```

2. **UDP Sender** (no Lost/Total):
   ```python
   primary_metric = "tx-Gbps"
   ```

3. **TCP Receiver** (first RX sample):
   ```python
   if primary_metric is None and metric_type == 'rx-Gbps':
       primary_metric = 'rx-Gbps'
   ```

4. **TCP Sender** (has Retr):
   ```python
   metric_type = "tx-Gbps"
   ```

5. **Default fallback**:
   ```python
   if primary_metric is None:
       primary_metric = "rx-Gbps"
   ```

**Output**: `post-process-data.json`
```json
{
  "benchmark": "iperf",
  "primary-period": "measurement",
  "primary-metric": "rx-Gbps",
  "periods": [
    {
      "name": "measurement",
      "metric-files": ["metric-data-0"]
    }
  ]
}
```

## Validation Wrapper

**Purpose**: `iperf-post-process-with-validation.py` validates new code against upstream

**Workflow**:
```
1. Run NEW post-process (iperf-post-process.py)
2. Save outputs as *.new
3. Run UPSTREAM post-process (upstream-iperf-post-process.py)
4. Compare outputs
5. Restore NEW outputs for rickshaw indexing
```

**Validation Report** (lines 79-210):
```
POST-PROCESS VALIDATION REPORT
Role: SERVER

Metric Types:
  NEW:      ['rx-Gbps', 'rx-lost/sec', 'rx-pps']
  UPSTREAM: ['rx-Gbps', 'rx-pps']

Server Comparison:
  rx-Gbps: PASS (7 samples match)
  rx-lost/sec: SKIP (not in upstream output)
  rx-pps: PASS (7 samples match)
```

**When to remove validation**:
After sufficient testing, update `rickshaw.json`:
```json
"post-script": "%bench-dir%iperf-post-process.py"
```
Instead of:
```json
"post-script": "%bench-dir%iperf-post-process-with-validation.py"
```

## Common Issues and Solutions

### Issue 1: Stale Uncompressed JSON Files

**Symptom**: `KeyError: 0` during indexing, metrics missing from OpenSearch

**Root Cause**: rickshaw-gen-docs.py creates uncompressed `metric-data-0.json` when adding UUIDs. If post-processing reruns, it reads the stale uncompressed file instead of the updated compressed file.

**Solution**:
```bash
rm -f <run-dir>/run/iterations/*/sample-*/*/1/metric-data-0.json
crucible index <run-dir>
```

**Prevention**: This is a known rickshaw issue being tracked upstream.

### Issue 2: Hunter Client/Server Timestamp Drift

**Symptom**: A result summary reports a negative primary-period duration or a
zero primary metric even though iperf produced data.

**Root cause**: The server post-processor previously relied on
`--bitrate-range` to detect hunting, but Rickshaw commonly invokes it with only
`--ifname`. It then based all server intervals on the first client trial while
the client selected a later trial.

**Solution**: Detect hunting from multiple client trial markers, split all server
tests, and pair server test `N` with client trial `N`. Both roles then use that
client trial's timestamps and emit the same period names.

### Issue 3: Header Column Indices Don't Match Data

**Symptom**: Wrong values extracted, or ValueError when parsing

**Root Cause**: Header columns are single words, data has value+unit pairs

**Example**:
```
Header: [ ID] Interval Transfer Bitrate Jitter Lost/Total
Index:    0      1         2        3       4        5

Data:   [  5] 0.00-1.00 sec  23.7 MBytes 199045 Kbits/sec  0.011 ms 0/20735 (0%)
Index:    0      1      2    3     4       5       6        7    8    9      10
```

Header index 5 = "Lost/Total", but data index 9 = "0/20735"

**Solution**: Use header for structure understanding, search data for patterns (implemented in lines 217-342)

### Issue 4: Embedded Server Output in Client File

**Symptom**: Server metrics appear in client output when using `--get-server-output`

**Detection** (lines 233-236):
```python
if line == "Server output:":
    print("Detected embedded server output - stopping client processing")
    break
```

**Why**: Prevent double-counting metrics. Server data processed separately from server-result.txt.

### Issue 5: Omitted Samples Inclusion

**Symptom**: More samples than expected, metrics include warm-up period

**Detection** (lines 247-248):
```python
if "omitted" in line:
    continue
```

**Why**: iperf uses `--omit N` to exclude first N seconds from statistics. Lines marked `(omitted)` should not generate metrics.

## Testing Guidelines

### Test Hunter Mode
```bash
# Run hunter test
crucible run hunter-test.json

# Find result directory
crucible ls | grep iperf | head -1

# Check post-process output
cat <run-dir>/run/iterations/iteration-1/sample-1/server/1/post-process-output.txt

# Verify:
# 1. "Client selected run N" message
# 2. "Extracted test #N from server results"
# 3. Same N on client and server
```

### Test Zero-Value Metrics
```bash
# Run clean UDP test (no packet loss)
crucible run clean-udp.json

# Get result
crucible get result --run <run-id>

# Verify rx-lost/sec appears in metrics list
# Query it
crucible get metric --run <run-id> --source iperf --type rx-lost/sec --period <period-id>

# Should show 0.00 value, not missing
```

### Test Bidirectional Mode
```bash
# Run bidir test
crucible run bidir.json

# Check post-process output for:
# "BIDIRECTIONAL MODE DETECTED from role markers in output"

# Verify result has both tx-Gbps and rx-Gbps
crucible get result --run <run-id>
```

### Validate Against Upstream
```bash
# Ensure rickshaw.json uses validation wrapper
# Run test
crucible run test.json

# Check validation report in post-process-output.txt
cat <run-dir>/run/iterations/iteration-1/sample-1/*/1/post-process-output.txt

# Look for "POST-PROCESS VALIDATION REPORT"
# Verify PASS for common metrics
```

## Performance Considerations

### Metric Storage
- Each sample: ~40 bytes in CSV (idx,begin,end,value)
- 10-second test @ 1 sample/sec × 3 metrics = 30 samples = ~1.2 KB
- Aggregation reduces storage for stable values
- Zero-filtering saved ~33% storage (now disabled with dont_delete=True)

### Indexing Time
- Primary bottleneck: OpenSearch document generation
- ~0.1 sec per metric descriptor (UUID assignment)
- ~0.01 sec per metric data sample
- Hunter mode: Only selected run indexed, not all attempts

### Memory Usage
- iperf output: ~10 KB per test
- Hunter mode: ~10 KB × 10 runs = ~100 KB
- Post-processing peak: ~1 MB (includes CDMMetrics buffers)
- No special optimization needed for typical tests

## Future Enhancements

### Potential Improvements
1. **Multi-stream aggregation**: Sum bitrates across parallel streams
2. **Jitter metrics**: Parse and index jitter values from UDP receiver
3. **Congestion window tracking**: Extract cwnd from TCP output
4. **Per-stream breakouts**: Index individual stream performance
5. **Packet size distribution**: Histogram of packet sizes if iperf provides it

### Backward Compatibility
When adding new metrics:
1. Keep existing metric types unchanged
2. Add new types with distinct names
3. Preserve idx assignment order
4. Test with validation wrapper
5. Ensure dont_delete=True applies to new metrics

### Known Limitations
1. **Assumes single iperf instance per endpoint**: Multiple parallel iperf processes not supported
2. **No partial failure handling**: If one sample parsing fails, entire run may be affected
3. **Fixed timestamp source**: Always uses client timestamps, no fallback if unavailable
4. **No schema evolution**: Metric descriptors are static once logged

## References

- **CDMMetrics**: `/opt/crucible/repos/https:github.com:perftool-incubator/toolbox/python/toolbox/cdm_metrics.py`
- **rickshaw-gen-docs**: `/opt/crucible/subprojects/core/rickshaw/rickshaw-gen-docs.py`
- **iperf3 documentation**: https://iperf.fr/iperf-doc.php
- **CDM schema**: `/opt/crucible/repos/https:github.com:perftool-incubator/CommonDataModel/`

## Document Version

- **Created**: 2026-05-22
- **Last Updated**: 2026-05-22
- **Code Version**: Post header-based parsing implementation, hunter mode server sync, zero-value preservation
- **Author**: Claude (AI assistant session summary)
