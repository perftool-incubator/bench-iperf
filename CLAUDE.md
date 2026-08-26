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
| `benchmark-metadata.json` | Machine-readable description and CDM-indexed source/type list (consumed by `crucible benchmarks list`) |
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

### Per-Stream Breakout (multi-thread iperf3)
Since iperf3 3.16, `-P`/`--parallel` spawns one OS thread per stream, and each interval's output has one row per stream ID (e.g. `[  5]`) plus a `[SUM]` aggregate row. The post-processor logs **each stream's row individually**, tagged with a `stream` breakout (the raw iperf3-assigned stream ID) in the metric's `names`, and skips `[SUM]` rows entirely — it does not pre-sum across streams. CDM's `default-aggregation: sum` combines the per-stream series back into a total at query time via the `stream` breakout, the same way tool-sysstat breaks out per-cpu/per-node metrics. This applies uniformly whether or not `nthreads` > 1: single-stream runs still get a `stream` tag (just with one distinct value), so there's no behavior branch and no discontinuity in how single- vs multi-stream results are shaped.

## Threading and CPU Pinning
- `nthreads` (client-only): sets iperf3 `--parallel`. Omitted from the command line when `nthreads == 1` (the default), so single-stream behavior/output is unchanged. The server never receives `-P` — it just services however many streams the client opens; `--nthreads` is still accepted on the server side (as a no-op) because rickshaw sends every multiplex-derived flag to both client and server scripts.
- `cpu-pin` supports: `numa` (whole numa-node CPU pool), `cpu-numa` (legacy single-CPU pin), `cpu-numa:<N>` (auto-pick N CPUs from the interface's numa node, clamped with a warning if the node has fewer than N), and `cpu:<list>` (explicit CPU list/range, matching bench-uperf's convention).
- Pinning is pool-only, never per-thread: `taskset --cpu-list` confines the whole iperf3 process to a CPU range and leaves the kernel scheduler to spread `nthreads` streams across it. iperf3 has no native per-thread affinity even in 3.16+ (only `-A`, which pins the whole process to a single CPU) — genuine 1:1 thread-to-CPU pinning is an open upstream feature request ([esnet/iperf#1738](https://github.com/esnet/iperf/issues/1738)), not something this benchmark implements itself.

## Conventions
- Primary branch is `main`
- Standard Bash modelines and 4-space indentation
- Python code follows 4-space indentation with standard modelines
