#!/usr/bin/env python3
"""Exercise both shell entry points against a fake sysfs and toolbox checkout.

Run with TOOLBOX_HOME=/path/to/toolbox python3 -m unittest discover -s unit-test -p 'test_cpu_pin.py'
"""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

BENCH = Path(__file__).resolve().parents[1]
TOOLBOX = Path(os.environ['TOOLBOX_HOME'])


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class CpuPinTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.system = root / 'system'
        self.net = root / 'net'
        write(self.system / 'node/node0/cpulist', '0-6\n')
        write(self.net / 'nic0/device/numa_node', '0\n')
        (self.net / 'vlan0').mkdir(parents=True)
        (self.net / 'vlan0/lower_nic0').symlink_to(self.net / 'nic0')
        for cpu, domain in ((0, '0-1'), (1, '0-1'), (2, '2-3'),
                            (3, '2-3'), (4, '4-6'), (5, '4-6'), (6, '4-6')):
            base = self.system / f'cpu/cpu{cpu}/cache/index3'
            write(base / 'level', '3\n')
            write(base / 'type', 'Unified\n')
            write(base / 'shared_cpu_list', domain + '\n')
        write(self.system / 'cpu/cpu3/online', '0\n')
        self.env = dict(os.environ, TOOLBOX_HOME=str(TOOLBOX),
                        IPERF_SYSFS_SYSTEM_ROOT=str(self.system),
                        IPERF_SYSFS_NET_ROOT=str(self.net))

    def tearDown(self):
        self.temp.cleanup()

    def run_pin(self, role, pin):
        script = (BENCH / ('iperf-client' if role == 'client' else 'iperf-server-start')).read_text()
        # Exercise the actual NUMA lookup and taskset-construction blocks,
        # avoiding network setup, service startup, and IRQ writes.
        start = script.index('ifname_numa_node=\n')
        end = script.index('\nesac\n', script.index('cpu_pin_cmd=""', start)) + len('\nesac\n')
        block = script[start:end]
        command = '''
source "$BENCH_DIR/iperf-base"
exit_error() { echo "ERROR: $1" >&2; exit 1; }
cpu_pin="$1"
iperf_parse_cpu_pin "$cpu_pin" || exit_error "unsupported cpu-pin value '$cpu_pin'"
ifname=vlan0
control_port=30000 data_port=30001 id=1 remotehost=host time=10 length='' protocol=''
''' + block + '''
echo "RESULT=$cpu_pin_cmd"
'''
        env = dict(self.env, BENCH_DIR=str(BENCH))
        return subprocess.run(['bash', '-c', command, 'test', pin], env=env,
                              text=True, capture_output=True)

    def test_l3_forms_for_both_roles(self):
        forms = {
            'l3': '4,5,6', 'L3': '4,5,6',
            'l3:2': '4,5', 'L3:2': '4,5',
            'l3::2': '4,5', 'L3::2': '4,5',
            'l3:0:': '0,1', 'L3:0:': '0,1',
            'l3:1:1': '2', 'L3:1:1': '2',
            'l3:2:2': '4,5',
        }
        for role in ('client', 'server'):
            for pin, cpus in forms.items():
                with self.subTest(role=role, pin=pin):
                    result = self.run_pin(role, pin)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('RESULT=taskset --cpu-list ' + cpus + '\n', result.stdout)
                    self.assertIn('L3 backing interface=nic0 NUMA node=0', result.stdout)
                    self.assertIn('L3 domain 0: 0,1 (2 CPUs)', result.stderr)
                    self.assertIn('L3 domain 1: 2 (1 CPUs)', result.stderr)
                    self.assertIn('L3 domain 2: 4,5,6 (3 CPUs)', result.stderr)

    def test_multiplex_and_parser_rejection(self):
        pattern = json.loads((BENCH / 'multiplex.json').read_text())['validations']['cpu-pinning']['vals']
        good = ('l3', 'L3', 'l3:2', 'L3:2', 'l3::2', 'L3::2', 'l3:0:',
                'L3:0:', 'l3:2:2', 'L3:2:2')
        bad = ('l3:', 'l3::', 'l3:::', 'l3:0', 'l3::0', 'l3:0:0',
               'l3:-1:', 'l3:1:-1', 'l3:a:', 'l3:1:2:3', 'l3:01', 'l3::01', 'L3:foo')
        for pin in good + bad:
            with self.subTest(pin=pin):
                accepted = pin in good
                self.assertEqual(bool(re.search(pattern, pin)), accepted)
                for role in ('client', 'server'):
                    result = self.run_pin(role, pin)
                    self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)

    def test_legacy_modes(self):
        for role in ('client', 'server'):
            expected = {'numa': '0,1,2,3,4,5,6', 'cpu-numa': '6' if role == 'client' else '5',
                        'cpu-numa:2': '0,1', 'cpu-numa:9': '0,1,2,3,4,5,6',
                        'cpu:2-4': '2-4'}
            for pin, cpus in expected.items():
                with self.subTest(role=role, pin=pin):
                    result = self.run_pin(role, pin)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('RESULT=taskset --cpu-list ' + cpus + '\n', result.stdout)
                    if pin == 'cpu-numa:9':
                        self.assertIn('clamping to 7', result.stdout)

    def test_normalization_and_missing_node(self):
        write(self.net / 'nic0/device/numa_node', '-1\n')
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3:0:')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('normalizing to node 0', result.stdout)
            self.assertIn('RESULT=taskset --cpu-list 0,1', result.stdout)
        (self.system / 'node/node0/cpulist').unlink()
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot read NUMA node 0 CPU list', result.stderr)

    def test_nonzero_node_and_empty_node(self):
        write(self.net / 'nic0/device/numa_node', '1\n')
        write(self.system / 'node/node1/cpulist', '4-6\n')
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3:0:2')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('NUMA node=1', result.stdout)
            self.assertIn('L3 domain 0: 4,5,6 (3 CPUs)', result.stderr)
            self.assertIn('RESULT=taskset --cpu-list 4,5', result.stdout)
        write(self.system / 'node/node1/cpulist', '\n')
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('empty or invalid CPU list', result.stderr)

    def test_failures(self):
        for role in ('client', 'server'):
            for pin, message in (('l3:3:', 'out of range'), ('l3:1:2', 'fewer than requested 2'),
                                 ('l3:9', 'no L3 domain has 9 CPUs')):
                result = self.run_pin(role, pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertIn('L3 domain 0:', result.stderr)
        write(self.system / 'node/node0/cpulist', '0-1\n')
        for cpu in (0, 1):
            write(self.system / f'cpu/cpu{cpu}/cache/index3/type', 'Data\n')
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('no online CPUs with L3 Unified', result.stderr)
        (self.net / 'nic0/device/numa_node').unlink()
        for role in ('client', 'server'):
            result = self.run_pin(role, 'l3')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot determine numa node', result.stderr)


if __name__ == '__main__':
    unittest.main()
