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

### Overview
Hunter mode runs multiple iperf tests with different bitrates to find the optimal throughput without packet loss.

### Client-Side Hunter Logic

**Input**: `iperf-client-result.txt` with multiple test runs
```
BEGIN-TS-1 1779449520.123456789
[iperf output for run 1]
END-TS-1 1779449530.234567890

BEGIN-TS-2 1779449540.345678901
[iperf output for run 2]
END-TS-2 1779449550.456789012
...
```

**Selection Algorithm** (lines 89-129):
```python
highest_bitrate = 0
highest_run_number = 0
cur_run_number = 0

for line in fh:
    if "BEGIN-TS" in line:
        cur_run_number += 1
    if "PASS" in line:
        columns = line.split()
        bitrate = float(columns[8])  # e.g., "149987" from "149987 Kbits/sec"
        if bitrate > highest_bitrate:
            highest_bitrate = bitrate
            highest_run_number = cur_run_number
```

**PASS Line Format**:
```
PASS: [  5]   0.00-10.00  sec   179 MBytes  149987 Kbits/sec  0.007 ms  0/207293 (0%)  receiver
```
- Column 8 contains the bitrate value
- PASS indicates zero packet loss
- Selects run with highest bitrate among all PASS runs

**Output**: `hunt-temp-result.txt` containing only the selected run
```
BEGIN-TS-7 1779449620.867662429
[iperf output for run 7 only]
END-TS-7 1779449633.875724949
```

### Server-Side Hunter Logic

**Challenge**: Server result file contains continuous output from all tests:
```
Server listening on 30001 (test #1)
[run 1 output]
Server listening on 30002 (test #2)
[run 2 output]
...
Server listening on 30007 (test #7)
[run 7 output]
...
```

**Solution** (lines 64-87, 132-185):

1. **Read client's selection**:
```python
client_hunt_result = "../../client/1/hunt-temp-result.txt"
with open(client_hunt_result, 'r') as fh:
    first_line = fh.readline()
    # Extract: "BEGIN-TS-7" → run number = 7
    match = re.search(r'BEGIN-TS-(\d+)', first_line)
    selected_run = int(match.group(1))
```

2. **Extract matching test from server results**:
```python
target_marker = f"(test #{test_number})"
next_test_marker = f"(test #{test_number + 1})"
in_target_test = False

for line in fh:
    if target_marker in line:
        in_target_test = True
        continue  # Skip "Server listening" line

    if in_target_test:
        if next_test_marker in line or "END-TS" in line:
            break
        ofh.write(line)
```

3. **Add timestamps from client**:
```python
# Read BEGIN-TS-N and END-TS-N from client hunt-temp-result.txt
client_hunt = "../../client/1/hunt-temp-result.txt"
with open(client_hunt, 'r') as cfh:
    for line in cfh:
        if f"BEGIN-TS-{test_number}" in line:
            begin_ts = line.strip()
        elif f"END-TS-{test_number}" in line:
            end_ts = line.strip()

# Prepend/append to extracted server output
ofh.write(begin_ts + "\n")
[server output for test N]
ofh.write(end_ts + "\n")
```

**Critical**: Server must extract the SAME run number that client selected, ensuring metric alignment.

### Hunter Mode Directory Structure

```
<run-dir>/
├── client/1/
│   ├── iperf-client-result.txt       # All runs with BEGIN-TS-N/END-TS-N markers
│   ├── hunt-temp-result.txt          # Selected run only (created by pre_process_hunting_results)
│   └── metric-data-0.{json,csv}.xz   # Metrics from selected run
└── server/1/
    ├── iperf-server-result.txt       # All tests concatenated
    ├── hunt-temp-result.txt          # Extracted test matching client selection
    └── metric-data-0.{json,csv}.xz   # Metrics from matching test
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

### Issue 2: Race Conditions in Hunter Mode Post-Processing

There are TWO race conditions when client and server post-processing run in parallel:

#### Race Condition 2a: File Not Created Yet

**Symptom**: Server post-processing fails with "No PASS found", server hunt-temp-result.txt contains all tests instead of selected test

**Root Cause**: Server post-processing started before client created hunt-temp-result.txt

**What happens**:
1. Rickshaw may run client and server post-processing in parallel
2. Server checks for `../../client/1/hunt-temp-result.txt` (line 80-91)
3. File doesn't exist yet, so check fails
4. Falls through to client-side hunting logic (lines 109-149)
5. Client logic looks for PASS lines - but server output has no PASS lines
6. Defaults to "last run" which is actually run 0 (the entire server result file)
7. Calls `dup_one_run()` which copies from BEGIN-TS-0 to END-TS-0 (all tests)

**Solution**: Server waits up to 30 seconds for client hunt-temp-result.txt to appear (implemented in lines 80-91). If file doesn't appear, post-processing exits with error instead of producing incorrect output.

**Error message if timeout**:
```
ERROR: Server hunting mode requires ../../client/1/hunt-temp-result.txt to exist
Waited 30 seconds but file was not created
Client post-processing must complete first to create this file
```

#### Race Condition 2b: Partial File Read

**Symptom**: Server reads incomplete hunt-temp-result.txt, may fail to extract run number or get truncated test data

**Root Cause**: Server passes `os.path.exists()` check while client is still writing the file

**What happens**:
1. Client opens hunt-temp-result.txt for writing
2. Client writes first few lines
3. File exists on disk but isn't fully written yet
4. Server's `os.path.exists()` check returns True
5. Server opens and reads the incomplete file
6. Regex might not find `BEGIN-TS-(\d+)` pattern
7. Or server gets partial test data missing END-TS marker

**Solution**: Client uses atomic write-then-rename pattern (implemented in `dup_one_run()` at lines 55-67 and `extract_server_test()` at lines 191-219):
```python
# Write to temp file first
temp_file = outfile + ".tmp"
with open(temp_file, "w") as ofh:
    # ... write all data ...

# Atomic rename - file appears complete or not at all
os.rename(temp_file, outfile)
```

**How this prevents the race**:
- Client writes to `hunt-temp-result.txt.tmp`
- Server's `os.path.exists("hunt-temp-result.txt")` returns False during writing
- Client finishes writing, calls atomic `os.rename()`
- File instantly appears complete (filesystems guarantee rename atomicity)
- Server sees file only after it's fully written

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
