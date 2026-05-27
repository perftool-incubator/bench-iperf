# iperf Post-Processing Test Data

This directory contains real iperf output samples extracted from regulus runs for manual and automated testing.

## Test Cases

### tcp-unidirectional-server
- **File**: `iperf-server-result.txt`
- **Header**: `[ ID] Interval Transfer Bitrate`
- **Detection**: TCP receiver (no special columns)
- **Expected metrics**: `rx-Gbps`
- **Description**: Tests that TCP receiver (no "Retr", no "Lost/Total", no "Datagrams") correctly generates rx-Gbps

### tcp-unidirectional-client
- **File**: `iperf-client-result.txt`
- **Header**: `[ ID] Interval Transfer Bitrate Retr Cwnd`
- **Detection**: TCP sender (has "Retr")
- **Expected metrics**: `tx-Gbps`, `tx-retry/sec`
- **Description**: Tests that TCP sender with Retr column generates tx metrics

### udp-sender
- **File**: `iperf-client-result.txt`
- **Header**: `[ ID] Interval Transfer Bitrate Total Datagrams`
- **Detection**: UDP sender (has "Datagrams")
- **Expected metrics**: `tx-Gbps`
- **Description**: Tests that UDP sender with Datagrams column generates tx metrics

### udp-receiver
- **File**: Server data (if available)
- **Header**: `[ ID] Interval Transfer Bitrate Jitter Lost/Total Datagrams`
- **Detection**: UDP receiver (has "Lost/Total")
- **Expected metrics**: `rx-Gbps`, `rx-lost/sec`, `rx-pps`
- **Description**: Tests that UDP receiver with Lost/Total column generates rx metrics

### tcp-bidirectional-client
- **File**: `iperf-client-result.txt`
- **Header**: `[ ID][Role] Interval Transfer Bitrate Retr Cwnd`
- **Detection**: Bidirectional mode (has `[TX-C]` and `[RX-C]` role markers)
- **Expected metrics**: `tx-Gbps`, `rx-Gbps`, `tx-retry/sec`
- **Description**: Tests that bidirectional client aggregates [TX-C] and [RX-C] flows separately

### tcp-bidirectional-server
- **File**: `iperf-server-result.txt`
- **Header**: `[ ID][Role] Interval Transfer Bitrate Retr Cwnd`
- **Detection**: Bidirectional mode (has `[TX-S]` and `[RX-S]` role markers)
- **Expected metrics**: `tx-Gbps`, `rx-Gbps`, `tx-retry/sec`
- **Description**: Tests that bidirectional server aggregates [TX-S] and [RX-S] flows separately

### tcp-hunter-mode
- **File**: `iperf-client-result.txt`
- **Description**: Tests hunter mode (multiple runs at different bitrates, selects highest passing)
- **Expected**: Correctly selects highest passing bitrate run

## Extracting Samples

Run `./extract-test-samples.sh` to extract fresh samples from regulus runs.

## Manual Testing

To manually test a sample:

```bash
cd test-data/tcp-unidirectional-server
crucible wrapper python3 ../../iperf-post-process.py --protocol=tcp --ipv=4 --ifname=eth0
# Check post-process-output.txt for detection messages (written to container, may not appear on host)
# Check metric-data-0.json for generated metrics (written to container, may not appear on host)
```

Note: Due to container file system isolation, output files may not be visible on the host. The test is successful if the command exits with code 0 and no errors are printed.

## Regression Detection

These samples caught the bug where TCP receiver (no special columns) was misidentified as UDP sender, causing server to generate `tx-Gbps` instead of `rx-Gbps`.

The fix: Changed protocol detection from `not has_retr_column` to `has_lost_total_column or has_datagrams_column`.
