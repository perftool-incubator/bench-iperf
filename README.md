# bench-iperf
[![CI Actions Status](https://github.com/perftool-incubator/bench-iperf/workflows/crucible-ci/badge.svg)](https://github.com/perftool-incubator/bench-iperf/actions)

Scripts and configuration to run the [iperf3](https://github.com/esnet/iperf) network throughput benchmark within the [crucible](https://github.com/perftool-incubator/crucible) performance testing framework.

See the [crucible-examples iperf documentation](https://github.com/perftool-incubator/crucible-examples/blob/main/iperf/README.md) for usage examples.

## Features

- **Bidirectional traffic** - Simultaneous TX/RX measurement with `--bidir` flag
- **Multi-process per pod** - IRQ load sharing across multiple iperf engines (rickshaw creates engines, `num-peers` coordinates port allocation)
- **IRQ/CPU pinning** - Pin iperf engine and NIC interrupts to specific CPUs for consistent performance
- **Hunter mode** - Automatically find the max bitrate under a loss threshold with `--bitrate-range` (fast short-probe search + full-duration confirm)
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

### Hunter Mode (automatic bitrate discovery)

Hunter mode binary-searches for the **highest UDP bitrate that stays under a loss
threshold**. Enable it by passing a range with `--bitrate-range` (instead of a
fixed `--bitrate`) plus a `--max-loss-pct` limit:

```json
{
  "benchmarks": [
    {
      "name": "iperf",
      "ids": [1],
      "mv-params": {
        "common-params": [
          { "arg": "protocol",        "vals": ["udp"] },
          { "arg": "time",            "vals": ["30"] },
          { "arg": "bitrate-range",   "vals": ["100M-2500M"] },
          { "arg": "max-loss-pct",    "vals": ["0.1"] },
          { "arg": "hunt-probe-time", "vals": ["8"] },
          { "arg": "hunt-confirm-attempts", "vals": ["1"] }
        ]
      }
    }
  ]
}
```

#### How it works: two phases

Hunter mode runs in two phases to be fast without sacrificing a valid
full-duration result:

1. **Probe (search) phase** — a binary search over `bitrate-range` runs each
   trial at a **short** duration (`hunt-probe-time`, default 8s) instead of the
   full `time`. This is where the ~2–3× speedup comes from: the search spends
   `log2(range)` short probes narrowing the rate, rather than
   `log2(range)` full-duration runs. The search step count is fixed by the
   range alone (each step halves the interval regardless of pass/fail).

2. **Confirm phase** — the found rate is re-run at the **full** `time` so the
   post-processor has a genuine full-duration sample to publish. `hunt-confirm-attempts`
   (K, default 1) controls how many full-duration confirm runs are allowed.

Result lines are marked so the post-processor can tell the two phases apart:

| Marker | Phase | Meaning |
|--------|-------|---------|
| `PROBE-OK:`   | probe   | short trial under the loss limit |
| `PROBE-DROP:` | probe   | short trial over the loss limit |
| `PASS:`       | confirm | full-duration run under the loss limit |
| `FAIL:`       | confirm | full-duration run over the loss limit |

The post-processor selects the **highest-bitrate `PASS`** as the winner. Probe
lines deliberately avoid the substring `PASS`, so they are never selected — only
full-duration confirm runs can win.

#### Confirm attempts (K) and the K=1 default

- **`hunt-confirm-attempts=1` (default, recommended):** one full-duration
  confirm at the found rate. Fast and deterministic.
- **K>1 (bidirectional reclaim):** additional confirm runs bracket around the
  found rate — climbing to reclaim headroom a noisy short-probe false-DROP may
  have wrongly rejected, or stepping down from a false-PASS. This is only worth
  its cost when hunting a **sharp, real loss cliff** where short probes are noisy
  right at the edge.

> **Important — K must be 1 for multi-pair / scale-out runs.** Each pod pair
> hunts independently. With K>1 the number of confirm runs is variable and
> per-pair jitter can make different pairs select **different** confirm runs at
> **non-overlapping** wall-clock windows. The benchmark's shared measurement
> period is the intersection of all pairs' winning windows, so misaligned
> winners collapse it to zero and the aggregate throughput reports **0**. With
> K=1 every pair runs the same fixed probe count plus exactly one confirm, so all
> winning windows coincide and aggregation is correct.

#### Caveat: generator-bound ceilings

Hunter mode keys off **loss**, and it assumes iperf3 can actually generate the
requested rate. If the sender is CPU/pps-bound below the range (e.g. small
`--length` single-stream UDP tops out near ~1 Gbps per core), *achieved* never
reaches *requested*, loss never crosses the threshold, and the search simply
climbs to the top of the range. The published metric is the true **achieved**
receiver rate (not the requested `-b`), so it remains correct — but it reflects
the **generator** limit, not a network loss boundary. To hunt a real network
ceiling, first lift the generator: larger `--length` (e.g. 1472, or jumbo), or
scale out with multiple pod pairs.

#### Caveat: single stream only (no `-P` / `nthreads`)

Hunter mode does **not** support multiple parallel streams. With `nthreads > 1`
(`-P`/`--parallel`), iperf3 emits a `[SUM]` receiver line that shifts the loss
columns the drop analyzer parses, so the search misreads loss. Keep
`nthreads=1` (the default) when hunting; to raise the generator ceiling, use a
larger `--length` or scale out with multiple pod pairs instead.

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
            "vals": ["cpu:4-7"]
          }
        ]
      }
    }
  ]
}
```

`cpu-pin` accepts:
- `numa` / `cpu-numa` - pin to the interface's whole NUMA-node CPU pool (or a single CPU, respectively)
- `cpu-numa:<N>` - auto-pick `N` CPUs from the interface's NUMA node, clamped (with a warning) if the node has fewer than `N`
- `cpu:<list>` - an explicit CPU list/range (e.g. `cpu:4-7` or `cpu:4,5,6,7`)

CPU pinning:
- Pins the whole iperf3 process (all `nthreads` streams share the pool - iperf3 has no native per-thread CPU affinity, see [esnet/iperf#1738](https://github.com/esnet/iperf/issues/1738))
- Pins NIC IRQs to the same CPU set for NUMA locality
- Reduces jitter and improves measurement consistency

### Multiple Streams (nthreads)

Since iperf3 3.16, `-P`/`--parallel` runs each stream on its own OS thread. Set `nthreads` to run multiple parallel streams:

```json
{
  "benchmarks": [
    {
      "name": "iperf",
      "ids": [1],
      "mv-params": {
        "common-params": [
          {
            "arg": "nthreads",
            "vals": ["4"]
          }
        ]
      }
    }
  ]
}
```

Each stream's throughput is logged as its own `stream` breakout series (tagged with iperf3's own stream ID); CDM's `default-aggregation: sum` combines them into a total at query time.

## Key Files

| File | Purpose |
|------|---------|
| `rickshaw.json` | Rickshaw integration: defines client/server scripts and parameter transformations |
| `iperf-client` | Client-side benchmark execution with multi-process and IRQ pinning support; drives hunter mode (probe search + confirm phase) |
| `iperf-hunter` | Hunter-mode library: binary search, drop analyzer, and per-connection run bookkeeping |
| `iperf-get-runtime` | Estimates benchmark runtime (accounts for `log2(range)` probes + K confirm runs in hunter mode) |
| `iperf-server-start` / `iperf-server-stop` | Server lifecycle scripts with IRQ pinning |
| `iperf-post-process` | Post-processing: parses iperf output into crucible metrics (supports bidirectional mode) |
| `workshop.json` | Engine image build requirements |
| `unit-test/` | Test infrastructure for post-processor validation |
