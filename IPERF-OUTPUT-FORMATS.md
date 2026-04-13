# iperf3 Output Formats

This document describes the different output formats produced by iperf3 that the post-processor must handle.

## 1. Unidirectional TCP (Single Direction)

### Raw Output Format
```
BEGIN-TS-0 1779369780.139730093
Connecting to host 172.30.62.103, port 30002
[  5] local 10.131.0.14 port 46352 connected to 172.30.62.103 port 30002
[ ID] Interval           Transfer     Bitrate         Retr  Cwnd
[  5]   0.00-1.00   sec  59.6 MBytes  499614 Kbits/sec    0    165 KBytes       (omitted)
[  5]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec    0    165 KBytes       (omitted)
[  5]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec    0    165 KBytes       (omitted)
[  5]   0.00-1.00   sec  59.6 MBytes  499905 Kbits/sec    0    165 KBytes
[  5]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec    0    165 KBytes
[  5]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec    0    165 KBytes
[  5]   3.00-4.00   sec  59.6 MBytes  499908 Kbits/sec    0    165 KBytes
[  5]   4.00-5.00   sec  59.6 MBytes  500171 Kbits/sec    0    165 KBytes
[  5]   5.00-6.00   sec  59.6 MBytes  499902 Kbits/sec    0    165 KBytes
[  5]   6.00-7.00   sec  59.6 MBytes  499915 Kbits/sec    0    165 KBytes
[  5]   7.00-8.00   sec  59.6 MBytes  500171 Kbits/sec    0    165 KBytes
[  5]   8.00-9.00   sec  59.6 MBytes  499909 Kbits/sec    0    165 KBytes
[  5]   9.00-10.00  sec  59.6 MBytes  499903 Kbits/sec    0    165 KBytes
- - - - - - - - - - - - - - - - - - - - - - - - -
[ ID] Interval           Transfer     Bitrate         Retr
[  5]   0.00-10.00  sec   596 MBytes  499989 Kbits/sec    0             sender
[  5]   0.00-10.00  sec   596 MBytes  499980 Kbits/sec                  receiver

iperf Done.
END-TS-0 1779369793.147085601
```

### Format Characteristics
- **Header**: `[ ID] Interval           Transfer     Bitrate         Retr  Cwnd`
- **NO** `[Role]` column
- **Column Positions**:
  - `columns[0]`: ID (e.g., `[  5]`)
  - `columns[1]`: (empty/whitespace)
  - `columns[2]`: Interval (e.g., `0.00-1.00`)
  - `columns[3]`: "sec"
  - `columns[4]`: Transfer amount (e.g., `59.6`)
  - `columns[5]`: Transfer unit (e.g., `MBytes`)
  - `columns[6]`: Bitrate value (e.g., `499614`)
  - `columns[7]`: Bitrate unit (e.g., `Kbits/sec`)
  - `columns[8]`: Retry count (e.g., `0`) - TX only
  - `columns[9]`: Cwnd value - TX only
  - `columns[10]`: Cwnd unit - TX only

### Data Line Types
1. **TX (Transmit) lines**: Have retry and Cwnd fields (num_fields > 8)
2. **RX (Receive) lines**: No retry/Cwnd fields (num_fields <= 8)
3. **Omitted lines**: Contain `(omitted)` - should be skipped
4. **Summary lines**: Contain `sender` or `receiver` - should be skipped

### Processing Requirements
- Skip lines with `(omitted)`
- Skip lines with `sender` or `receiver`
- Skip lines with `SUM`
- Parse interval from `columns[2]` (e.g., "0.00-1.00" → start=0.00, end=1.00, interval=1000ms)
- Parse bitrate from `columns[6]` and unit from `columns[7]`
- If num_fields > 8: it's TX, parse retry from `columns[8]`
- Use running timestamp that increments by interval duration
- Reset timestamp when hitting summary lines (for server output section)

### Expected Output
- For 10-second test with --omit=3:
  - 3 omitted samples (skipped)
  - 7 actual samples logged
- Each sample logged immediately as separate metric
- Metric types: `tx-Gbps` (for TX lines), `rx-Gbps` (for RX lines), `tx-retry/sec`

---

## 2. Bidirectional TCP (--bidir flag)

### Raw Output Format
```
BEGIN-TS-0 1779210504.645399704
Connecting to host 172.30.144.224, port 30002
[  5] local 10.131.0.125 port 46792 connected to 172.30.144.224 port 30002
[  7] local 10.131.0.125 port 46798 connected to 172.30.144.224 port 30002
[ ID][Role] Interval           Transfer     Bitrate         Retr  Cwnd
[  5][TX-C]   0.00-1.00   sec  59.6 MBytes  499619 Kbits/sec    1    147 KBytes       (omitted)
[  7][RX-C]   0.00-1.00   sec  59.6 MBytes  499613 Kbits/sec                  (omitted)
[  5][TX-C]   1.00-2.00   sec  59.6 MBytes  499908 Kbits/sec    0    147 KBytes       (omitted)
[  7][RX-C]   1.00-2.00   sec  59.6 MBytes  499912 Kbits/sec                  (omitted)
[  5][TX-C]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec    0    147 KBytes       (omitted)
[  7][RX-C]   2.00-3.00   sec  59.6 MBytes  500170 Kbits/sec                  (omitted)
[  5][TX-C]   0.00-1.00   sec  59.6 MBytes  499906 Kbits/sec    0    151 KBytes
[  7][RX-C]   0.00-1.00   sec  59.6 MBytes  499906 Kbits/sec
[  5][TX-C]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec    0    151 KBytes
[  7][RX-C]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec
[  5][TX-C]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec    0    155 KBytes
[  7][RX-C]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec
[  5][TX-C]   3.00-4.00   sec  59.5 MBytes  498764 Kbits/sec    0    155 KBytes
[  7][RX-C]   3.00-4.00   sec  59.5 MBytes  499026 Kbits/sec
[  5][TX-C]   4.00-5.00   sec  59.8 MBytes  501316 Kbits/sec    0    158 KBytes
[  7][RX-C]   4.00-5.00   sec  59.7 MBytes  501053 Kbits/sec
[  5][TX-C]   5.00-6.00   sec  59.6 MBytes  499908 Kbits/sec    0    158 KBytes
[  7][RX-C]   5.00-6.00   sec  59.6 MBytes  499908 Kbits/sec
[  5][TX-C]   6.00-7.00   sec  59.6 MBytes  499909 Kbits/sec    0    158 KBytes
[  7][RX-C]   6.00-7.00   sec  59.6 MBytes  499910 Kbits/sec
[  5][TX-C]   7.00-8.00   sec  59.6 MBytes  500171 Kbits/sec    0    162 KBytes
[  7][RX-C]   7.00-8.00   sec  59.6 MBytes  500171 Kbits/sec
[  5][TX-C]   8.00-9.00   sec  59.6 MBytes  499909 Kbits/sec    0    162 KBytes
[  7][RX-C]   8.00-9.00   sec  59.6 MBytes  499909 Kbits/sec
[  5][TX-C]   9.00-10.00  sec  59.6 MBytes  499907 Kbits/sec    0    162 KBytes
[  7][RX-C]   9.00-10.00  sec  59.6 MBytes  499907 Kbits/sec
- - - - - - - - - - - - - - - - - - - - - - - - -
[ ID][Role] Interval           Transfer     Bitrate         Retr
[  5][TX-C]   0.00-10.00  sec   596 MBytes  499988 Kbits/sec    0             sender
[  5][TX-C]   0.00-10.00  sec   596 MBytes  499977 Kbits/sec                  receiver

Server output:
Accepted connection from 10.131.0.125, port 46786
[  5] local 10.128.2.173 port 30002 connected to 10.131.0.125 port 46792
[  8] local 10.128.2.173 port 30002 connected to 10.131.0.125 port 46798
[ ID][Role] Interval           Transfer     Bitrate         Retr  Cwnd
[  5][RX-S]   0.00-1.00   sec  59.6 MBytes  499618 Kbits/sec                  (omitted)
[  8][TX-S]   0.00-1.00   sec  59.6 MBytes  499612 Kbits/sec    1    158 KBytes       (omitted)
[  5][RX-S]   1.00-2.00   sec  59.6 MBytes  499908 Kbits/sec                  (omitted)
[  8][TX-S]   1.00-2.00   sec  59.6 MBytes  499913 Kbits/sec    0    158 KBytes       (omitted)
[  5][RX-S]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec                  (omitted)
[  8][TX-S]   2.00-3.00   sec  59.6 MBytes  500171 Kbits/sec    0    162 KBytes       (omitted)
[  5][RX-S]   0.00-1.00   sec  59.6 MBytes  499904 Kbits/sec
[  8][TX-S]   0.00-1.00   sec  59.6 MBytes  499904 Kbits/sec    0    162 KBytes
[  5][RX-S]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec
[  8][TX-S]   1.00-2.00   sec  59.6 MBytes  499909 Kbits/sec    0    165 KBytes
...
```

### Format Characteristics
- **Header**: `[ ID][Role] Interval           Transfer     Bitrate         Retr  Cwnd`
- **HAS** `[Role]` column with values like `[TX-C]`, `[RX-C]`, `[TX-S]`, `[RX-S]`
- **Column Positions** (shifted by 1 compared to unidirectional):
  - `columns[0]`: ID (e.g., `[  5]`)
  - `columns[1]`: Role (e.g., `[TX-C]`)
  - `columns[2]`: (empty/whitespace)
  - `columns[3]`: Interval (e.g., `0.00-1.00`)
  - `columns[4]`: "sec"
  - `columns[5]`: Transfer amount
  - `columns[6]`: Transfer unit
  - `columns[7]`: Bitrate value
  - `columns[8]`: Bitrate unit
  - `columns[9]`: Retry count - TX only
  - `columns[10]`: Cwnd value - TX only

### Role Markers
- **TX-C**: Client transmitting (client → server)
- **RX-C**: Client receiving (server → client)
- **TX-S**: Server transmitting (server → client, same flow as RX-C)
- **RX-S**: Server receiving (client → server, same flow as TX-C)

### Data Line Detection
Look for pattern: `\[\s*\d+\]\[([A-Z]{2}-[CS])\]` in the line
- If found → bidirectional mode
- Extract role from capture group

### Processing Requirements
- Detect bidirectional mode by finding `[TX-C]`, `[RX-C]`, `[TX-S]`, or `[RX-S]` in data lines
- Parse interval and bitrate using dynamic column search (can't use hardcoded indices)
- Aggregate samples with matching timestamps:
  - TX-C and TX-S both contribute to `tx-Gbps`
  - RX-C and RX-S both contribute to `rx-Gbps`
  - Accumulate values for same timestamp across different flows
- Log aggregated samples at end of processing

### Expected Output
- For 10-second test with --omit=3:
  - 7 aggregated time intervals
  - Each interval has both `tx-Gbps` and `rx-Gbps` metrics
  - Values are sums of client and server contributions for that interval
- Metric types: `tx-Gbps` (aggregated), `rx-Gbps` (aggregated), `tx-retry/sec`

---

## 3. UDP Unidirectional

### Format Differences from TCP
- **TX lines**: `[ ID] Interval Transfer Bitrate Total Datagrams`
- **RX lines**: `[ ID] Interval Transfer Bitrate Jitter Lost/Total Datagrams`
- Loss pattern: `8/38128` (lost/total) followed by percentage `(0.021%)`

### Processing Requirements
- Detect RX by presence of `Lost/Total` pattern
- Parse lost/total values
- Apply omit filter to lost values (set to 0 if within omit period)
- Log `rx-lost/sec` and `rx-pps` metrics for RX lines

---

## Summary of Key Differences

| Feature | Unidirectional | Bidirectional |
|---------|---------------|---------------|
| Role column | NO | YES |
| Column indices | Fixed (col 2, 6, 8) | Shifted (+1) |
| Detection | No role markers in data | `[TX-C]`, `[RX-C]`, etc. in data |
| Processing | Log each line immediately | Accumulate then aggregate |
| Sample count | ~10 individual samples | ~10 aggregated intervals |
| Metric types | tx-Gbps OR rx-Gbps per line | tx-Gbps AND rx-Gbps per interval |
