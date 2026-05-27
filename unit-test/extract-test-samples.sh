#!/bin/bash
# Extract test samples from regulus runs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTDIR="$SCRIPT_DIR/test-data"
REGULUS_ROOT=/home/hnhan/JPMC/jpmc-regulus/2_GROUP

echo "Extracting test samples to $TESTDIR"

# 1. TCP Unidirectional Server (TCP receiver - no special columns)
echo "1. TCP unidirectional server..."
mkdir -p $TESTDIR/tcp-unidirectional-server
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/TCP/2-POD/run-DRY-2026-05-23-12:41:14/iperf--2026-05-23_16:41:21_UTC--6e7581ee-5edb-4b1f-aea7-8ef92d053f03/run/iterations/iteration-1/sample-1/server/1/iperf-server-result.txt \
   $TESTDIR/tcp-unidirectional-server/
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/TCP/2-POD/run-DRY-2026-05-23-12:41:14/iperf--2026-05-23_16:41:21_UTC--6e7581ee-5edb-4b1f-aea7-8ef92d053f03/run/iterations/iteration-1/sample-1/client/1/iperf-client-result.txt \
   $TESTDIR/tcp-unidirectional-server/
cat > $TESTDIR/tcp-unidirectional-server/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "expected_metrics": ["rx-Gbps"],
  "description": "TCP receiver - should generate rx-Gbps only"
}
EOF

# 2. TCP Unidirectional Client (TCP sender - has Retr column)
echo "2. TCP unidirectional client..."
mkdir -p $TESTDIR/tcp-unidirectional-client
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/TCP/2-POD/run-DRY-2026-05-23-12:41:14/iperf--2026-05-23_16:41:21_UTC--6e7581ee-5edb-4b1f-aea7-8ef92d053f03/run/iterations/iteration-1/sample-1/client/1/iperf-client-result.txt \
   $TESTDIR/tcp-unidirectional-client/
cat > $TESTDIR/tcp-unidirectional-client/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "expected_metrics": ["tx-Gbps", "tx-retry/sec"],
  "description": "TCP sender - should generate tx-Gbps and tx-retry/sec"
}
EOF

# 3. UDP Sender (has Datagrams column)
echo "3. UDP sender..."
mkdir -p $TESTDIR/udp-sender
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/UDP/2-POD/run-DRY-ourchecking-2026-05-21-20:32:49/iperf--2026-05-22_00:33:02_UTC--1195f1f7-b07e-4fda-b17a-8bb7d3326fb5/run/iterations/iteration-1/sample-1/client/1/iperf-client-result.txt \
   $TESTDIR/udp-sender/
cat > $TESTDIR/udp-sender/expected-metrics.json << 'EOF'
{
  "protocol": "udp",
  "expected_metrics": ["tx-Gbps"],
  "description": "UDP sender - should generate tx-Gbps only"
}
EOF

# 4. UDP Receiver (has Lost/Total column)
echo "4. UDP receiver..."
mkdir -p $TESTDIR/udp-receiver
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/UDP/2-POD/run-DRY-ourchecking-2026-05-21-20:32:49/iperf--2026-05-22_00:33:02_UTC--1195f1f7-b07e-4fda-b17a-8bb7d3326fb5/run/iterations/iteration-1/sample-1/server/1/iperf-server-result.txt \
   $TESTDIR/udp-receiver/ 2>/dev/null || echo "  (UDP server result not found - may not collect server data)"
cp $REGULUS_ROOT/NO-PAO/4IP/INTER-NODE/UDP/2-POD/run-DRY-ourchecking-2026-05-21-20:32:49/iperf--2026-05-22_00:33:02_UTC--1195f1f7-b07e-4fda-b17a-8bb7d3326fb5/run/iterations/iteration-1/sample-1/client/1/iperf-client-result.txt \
   $TESTDIR/udp-receiver/
cat > $TESTDIR/udp-receiver/expected-metrics.json << 'EOF'
{
  "protocol": "udp",
  "expected_metrics": ["rx-Gbps", "rx-lost/sec", "rx-pps"],
  "description": "UDP receiver - should generate rx-Gbps, rx-lost/sec, and rx-pps"
}
EOF

# 5. TCP Bidirectional (from N-POD-STEPS/run-bidir)
echo "5. TCP bidirectional..."
BIDIR_RUN=$(find $REGULUS_ROOT -path "*/N-POD-STEPS/run-bidir-*" -type d | head -1)
if [ -n "$BIDIR_RUN" ]; then
    BIDIR_CLIENT=$(find "$BIDIR_RUN" -path "*/client/1/iperf-client-result.txt" | head -1)
    BIDIR_SERVER=$(find "$BIDIR_RUN" -path "*/server/1/iperf-server-result.txt" | head -1)

    if [ -f "$BIDIR_CLIENT" ] && grep -q "\[TX-C\]" "$BIDIR_CLIENT"; then
        echo "  Found bidirectional client!"
        mkdir -p $TESTDIR/tcp-bidirectional-client
        cp "$BIDIR_CLIENT" $TESTDIR/tcp-bidirectional-client/
        cat > $TESTDIR/tcp-bidirectional-client/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "bidir": true,
  "expected_metrics": ["tx-Gbps", "rx-Gbps", "tx-retry/sec"],
  "description": "TCP bidirectional client - should aggregate tx-Gbps and rx-Gbps from [TX-C] and [RX-C] flows"
}
EOF
    fi

    if [ -f "$BIDIR_SERVER" ] && grep -q "\[TX-S\]" "$BIDIR_SERVER"; then
        echo "  Found bidirectional server!"
        mkdir -p $TESTDIR/tcp-bidirectional-server
        cp "$BIDIR_SERVER" $TESTDIR/tcp-bidirectional-server/
        cp "$BIDIR_CLIENT" $TESTDIR/tcp-bidirectional-server/iperf-client-result.txt
        cat > $TESTDIR/tcp-bidirectional-server/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "bidir": true,
  "expected_metrics": ["tx-Gbps", "rx-Gbps", "tx-retry/sec"],
  "description": "TCP bidirectional server - should aggregate tx-Gbps and rx-Gbps from [TX-S] and [RX-S] flows"
}
EOF
    fi
fi

# 6. Find hunter mode run
echo "6. Looking for hunter mode run..."
HUNTER_RUN=$(find $REGULUS_ROOT -path "*HUNTER*/client/1/iperf-client-result.txt" | head -1)
if [ -f "$HUNTER_RUN" ]; then
    echo "  Found hunter mode run!"
    mkdir -p $TESTDIR/tcp-hunter-mode
    cp "$HUNTER_RUN" $TESTDIR/tcp-hunter-mode/iperf-client-result.txt
    cat > $TESTDIR/tcp-hunter-mode/expected-metrics.json << 'EOF'
{
  "protocol": "tcp",
  "hunter": true,
  "expected_metrics": ["tx-Gbps", "tx-retry/sec"],
  "description": "TCP hunter mode - should select highest passing run and generate tx-Gbps"
}
EOF
fi

echo ""
echo "Test sample extraction complete!"
echo "Test cases created:"
ls -1 $TESTDIR/ | grep -v README
