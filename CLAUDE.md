# Bench-iperf

## Purpose
Scripts and configuration to run the iperf3 network throughput benchmark within the crucible framework. Measures TCP/UDP bandwidth between client and server endpoints.

## Language
Bash — all scripts

## Key Files
| File | Purpose |
|------|---------|
| `rickshaw.json` | Rickshaw integration: client/server scripts, parameter transformations |
| `iperf-client` | Client-side benchmark execution |
| `iperf-server-start` / `iperf-server-stop` | Server lifecycle management |
| `iperf-post-process` | Parses iperf JSON output into crucible metrics |
| `workshop.json` | Engine image build requirements |

## Conventions
- Primary branch is `main`
- Standard Bash modelines and 4-space indentation
