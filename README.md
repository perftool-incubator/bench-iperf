# bench-iperf
[![CI Actions Status](https://github.com/perftool-incubator/bench-iperf/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/bench-iperf/actions)

Scripts and configuration to run the [iperf3](https://github.com/esnet/iperf) network throughput benchmark within the [crucible](https://github.com/perftool-incubator/crucible) performance testing framework.

See the [crucible-examples iperf documentation](https://github.com/perftool-incubator/crucible-examples/blob/main/iperf/README.md) for usage examples.

## Key Files

| File | Purpose |
|------|---------|
| `rickshaw.json` | Rickshaw integration: defines client/server scripts and parameter transformations |
| `iperf-client` | Client-side benchmark execution |
| `iperf-server-start` / `iperf-server-stop` | Server lifecycle scripts |
| `iperf-post-process` | Post-processing: parses iperf output into crucible metrics |
| `workshop.json` | Engine image build requirements |
