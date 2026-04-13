# bench-iperf
[![CI Actions Status](https://github.com/perftool-incubator/bench-iperf/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/bench-iperf/actions)

Scripts and configuration to run the [iperf3](https://github.com/esnet/iperf) network throughput benchmark within the [crucible](https://github.com/perftool-incubator/crucible) performance testing framework.

See the [crucible-examples iperf documentation](https://github.com/perftool-incubator/crucible-examples/blob/main/iperf/README.md) for usage examples.

## Features

- **Bidirectional traffic** - Simultaneous TX/RX measurement with `--bidir` flag
- **Multi-process per pod** - IRQ load sharing across multiple iperf engines (rickshaw creates engines, `num-peers` coordinates port allocation)
- **IRQ/CPU pinning** - Pin iperf engine and NIC interrupts to specific CPUs for consistent performance
- **Hunter mode** - Automatically find optimal bitrate with `--bitrate-range`
- **Protocol support** - TCP and UDP with automatic detection in post-processing
- **Comprehensive metrics** - tx-Gbps, rx-Gbps, tx-retry/sec, rx-lost/sec, rx-pps

## Configuration

### Bidirectional Mode

Enable simultaneous TX and RX traffic measurement by passing `--bidir` to iperf3:

```json
{
  "benchmarks": [
    {
      "name": "iperf",
      "ids": [1],
      "mv-params": {
        "common-params": [
          {
            "arg": "passthru",
            "vals": ["--bidir"]
          }
        ]
      }
    }
  ]
}
```

In bidirectional mode:
- Both client and server simultaneously transmit and receive
- Post-processor aggregates TX and RX flows separately
- Metrics generated: `tx-Gbps` and `rx-Gbps` from both endpoints
- Role markers (`[TX-C]`, `[RX-C]`, `[TX-S]`, `[RX-S]`) identify each flow

### Multi-Process with IRQ Load Sharing (SR-IOV)

**Note**: This feature applies to SR-IOV mode only. In OVN-Kubernetes CNI, the pod interface `eth0` is a veth endpoint with no real IRQs to distribute.

Distribute network interrupt load across multiple iperf processes by combining rickshaw's multi-engine configuration with the `num-peers` parameter.

**Step 1: Configure multiple engines per pod (rickshaw endpoints configuration):**

```json
{
  "endpoints": [
    {
      "type": "remotehosts",
      "remotehost": "client-host",
      "engines": {
        "client": "1-4"
      },
      "pods": [
        {
          "name": "client-pod",
          "engines": [
            {
              "role": "client",
              "ids": "1+2+3+4"
            }
          ]
        }
      ]
    },
    {
      "type": "remotehosts",
      "remotehost": "server-host",
      "engines": {
        "server": "1-4"
      },
      "pods": [
        {
          "name": "server-pod",
          "engines": [
            {
              "role": "server",
              "ids": "1+2+3+4"
            }
          ]
        }
      ]
    }
  ]
}
```

**Step 2: Set `num-peers` to inform each engine about its siblings:**

```json
{
  "benchmarks": [
    {
      "name": "iperf",
      "ids": [1],
      "mv-params": {
        "common-params": [
          {
            "arg": "num-peers",
            "vals": ["4"]
          }
        ]
      }
    }
  ]
}
```

How this works:
- Rickshaw creates 4 iperf engine processes per pod
- `num-peers: 4` tells each engine "you have 3 siblings sharing the NIC"
- NIC IRQs are pinned to engine CPUs in round-robin fashion
- Example: With 8 IRQs (8 queues) and 4 engines, each engine CPU handles 2 IRQs
- Improves scalability on high-speed networks by avoiding single-core bottlenecks

### IRQ/CPU Pinning

Pin iperf processes and NIC interrupts to specific CPUs for consistent performance:

```json
{
  "benchmarks": [
    {
      "name": "iperf",
      "ids": [1],
      "mv-params": {
        "common-params": [
          {
            "arg": "cpu-pin",
            "vals": ["4,5,6,7"]
          }
        ]
      }
    }
  ]
}
```

CPU pinning:
- Pins iperf engine processes to specified CPUs
- Pins NIC IRQs to the same CPU set for NUMA locality
- Reduces jitter and improves measurement consistency

## Key Files

| File | Purpose |
|------|---------|
| `rickshaw.json` | Rickshaw integration: defines client/server scripts and parameter transformations |
| `iperf-client` | Client-side benchmark execution with multi-process and IRQ pinning support |
| `iperf-server-start` / `iperf-server-stop` | Server lifecycle scripts with IRQ pinning |
| `iperf-post-process` | Post-processing: parses iperf output into crucible metrics (supports bidirectional mode) |
| `workshop.json` | Engine image build requirements |
| `unit-test/` | Test infrastructure for post-processor validation |
