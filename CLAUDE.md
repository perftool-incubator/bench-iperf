# Bench-iperf

## Purpose
Scripts and configuration to run the iperf3 network throughput benchmark within the crucible framework. Measures TCP/UDP bandwidth between client and server endpoints.

## Language
- Bash for benchmark execution scripts
- Python for post-processing (`iperf-post-process.py`)

## Key Files
| File | Purpose |
|------|---------|
| `rickshaw.json` | Rickshaw integration: client/server scripts, parameter transformations |
| `iperf-client` | Client-side benchmark execution |
| `iperf-server-start` / `iperf-server-stop` | Server lifecycle management |
| `iperf-post-process.py` | Parses iperf text output into crucible metrics |
| `workshop.json` | Engine image build requirements |

## Post-Processing Architecture

### Protocol Detection
`iperf-post-process.py` automatically detects TCP vs UDP mode from iperf header output:
- **TCP mode**: Header contains "Retr" column (`has_retr_column = True`)
- **UDP receiver**: Header contains "Lost/Total" column (`has_lost_total_column = True`)
- **UDP sender**: Header has neither special column

The `--protocol` parameter is accepted for backward compatibility but ignored — protocol is always auto-detected.

### Pattern-Based Parsing
The post-processor uses pattern-based parsing instead of fixed column indices to handle varying iperf output formats:
- **Intervals**: Searches for `X.XX-Y.YY` pattern in any column
- **Bitrate**: Finds number immediately before `bits/sec` marker
- **Retries** (TCP): Finds first integer after `bits/sec` marker
- **Lost/Total** (UDP): Searches for `X/Y` pattern (not containing `bits/sec`)

This approach is robust against:
- Multi-word column headers that split differently than data
- Different iperf versions with varying output formats
- Localized output with different column spacing

### Metrics Collected
**TCP:**
- `tx-Gbps` (transmit throughput)
- `rx-Gbps` (receive throughput, for bidirectional)
- `tx-retry/sec` (retransmissions per second, when present)

**UDP:**
- `tx-Gbps` (sender throughput)
- `rx-Gbps` (receiver throughput)
- `rx-lost/sec` (receiver packet loss)
- `rx-pps` (receiver packets per second)

### Bidirectional Mode
Detected automatically when iperf output contains role markers like `[TX-C]`, `[RX-C]`, `[TX-S]`, `[RX-S]`. TX and RX samples are accumulated separately and logged with appropriate metric types.

## Conventions
- Primary branch is `main`
- Standard Bash modelines and 4-space indentation
- Python code follows 4-space indentation with standard modelines
