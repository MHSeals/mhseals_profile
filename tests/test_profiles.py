"""The MHSeals profiles load with astro_dock's loader and resolve as the fleet expects.

ASTRO_DOCK is an astro_dock checkout (default: ../astro_dock, or the parent when this repo is its
profiles/ submodule). Nothing is contacted: hostnames and NICs are faked.
"""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

HERE = Path(__file__).resolve().parents[1]
CANDIDATES = [os.environ.get('ASTRO_DOCK'), HERE.parent, HERE.parent / 'astro_dock']
ASTRO = next((Path(p) for p in CANDIDATES if p and (Path(p) / 'devices/profile.py').exists()), None)
LOADER = ASTRO is not None and "command == 'resolve'" in (ASTRO / 'devices/profile.py').read_text()
# Permanent MACs of the boat computers (blastoise/network.env STATIC_LEASES).
MACS = {'blastoise-odroid': '56:e2:03:5c:60:22', 'blastoise-jetson': '3c:6d:66:bf:6b:e2'}


@unittest.skipUnless(LOADER, 'needs an astro_dock checkout with the member loader (set ASTRO_DOCK)')
class ShippedProfiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sys = Path(tmp.name)

    def hw(self, *args, hostname='ci', mac=None, **extra):
        if mac:
            (self.sys / 'eth0').mkdir(exist_ok=True)
            (self.sys / 'eth0/address').write_text(mac + '\n')
            (self.sys / 'eth0/addr_assign_type').write_text('0\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ASTRO_', 'HW_'))}
        env.update(ASTRO_PROFILES=str(HERE), ASTRO_SECRETS='/nonexistent', ASTRO_ENV_FILE='/dev/null',
                   HW_HOSTNAME=hostname, HW_SYSFS_NET=str(self.sys), HW_ADDRS='',
                   HW_DT_MODEL=str(self.sys / 'no-model'))
        env.update(extra)
        return subprocess.run(['bash', str(ASTRO / 'hw.sh'), *args], capture_output=True, text=True, env=env,
                              timeout=60)

    def test_every_member_loads_and_each_vessel_kind_has_its_driver(self):
        run = self.hw('whoami')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('members blastoise:vessel control_station:station squirtle:vessel', run.stdout)
        shown = yaml.safe_load(self.hw('profile', 'show', 'blastoise').stdout)
        for kind in set(shown) - {'profile', 'network_env', 'registry'}:
            with self.subTest(kind=kind):
                run = self.hw('--vessel', 'blastoise', kind, '--help')
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_boat_computers_and_the_station_resolve_to_blastoise(self):
        for host, mac in MACS.items():
            with self.subTest(host=host):
                run = self.hw('whoami', hostname='roboboat', mac=mac)      # the container hostname is ignored
                self.assertIn('host      %s (MAC %s' % (host, mac), run.stdout, run.stderr)
                self.assertIn('vessel    blastoise ', run.stdout)
        __import__('shutil').rmtree(self.sys / 'eth0')
        run = self.hw('whoami', hostname='tranquility')
        self.assertIn('host      tranquility (hostname tranquility)', run.stdout, run.stderr)
        self.assertIn('station   control_station\n', run.stdout)
        self.assertIn('vessel    blastoise ', run.stdout)

    def test_any_operator_laptop_on_the_boat_lan_is_a_control_station_and_a_boat_computer_never(self):
        run = self.hw('whoami', hostname='somelaptop', HW_ADDRS='192.168.0.57/24')
        self.assertIn('station   control_station\n', run.stdout, run.stderr)
        self.assertIn('vessel    blastoise (boat LAN 192.168.0.57 in 192.168.0.0/24)', run.stdout)
        for address in ('192.168.0.152/24', '192.168.0.153/24'):          # the ODROID's and Jetson's reservations
            run = self.hw('whoami', hostname='somelaptop', HW_ADDRS=address)
            self.assertNotIn('\nstation ', run.stdout, run.stderr)
            self.assertIn('a boat computer (address %s belongs to' % address.split('/')[0], run.stdout)
            self.assertIn('vessel    none', run.stdout)

    def test_an_unlisted_laptop_attaches_to_blastoise_and_gets_domain_42_by_lan_subnet_alone(self):
        """astro_dock reads LAN_SUBNET (BOAT_SUBNET only as a fallback), so dropping BOAT_SUBNET, once
        mhseals_testing reads LAN_SUBNET, keeps operator laptops on blastoise."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'profiles')
            shutil.copytree(HERE, root, ignore=shutil.ignore_patterns('.git', 'tests'))
            env = root / 'blastoise/network.env'
            env.write_text(''.join(line for line in env.read_text().splitlines(True)
                                   if not line.startswith('BOAT_SUBNET=')))
            for profiles in (str(HERE), str(root)):
                with self.subTest(profiles=profiles):
                    run = self.hw('whoami', hostname='somelaptop', HW_ADDRS='192.168.0.57/24', ASTRO_PROFILES=profiles)
                    self.assertIn('station   control_station\n', run.stdout, run.stderr)
                    self.assertIn('vessel    blastoise (boat LAN 192.168.0.57 in 192.168.0.0/24)', run.stdout)
                    run = self.hw('env', 'ROS_DOMAIN_ID', hostname='somelaptop', HW_ADDRS='192.168.0.57/24',
                                  ASTRO_PROFILES=profiles)
                    self.assertEqual((run.returncode, run.stdout.strip()), (0, '42'), run.stderr)

    def test_repos_per_host(self):
        def repos(host):
            run = self.hw('--host', host, 'hosts', 'repos')
            self.assertEqual(run.returncode, 0, run.stderr)
            return [line.split()[0] for line in run.stdout.splitlines()]
        everyone = ['mhseals_hardware', 'mhseals_nav', 'mhseals_testing']
        self.assertEqual(repos('blastoise-odroid'), everyone)
        self.assertEqual(repos('blastoise-jetson'), everyone + ['direct_lidar_inertial_odometry'])
        self.assertEqual(repos('tranquility'), everyone + ['rviz-plugin-zed-od', 'zed-ros2-wrapper'])
        self.assertEqual(repos('somelaptop'), everyone + ['rviz-plugin-zed-od', 'zed-ros2-wrapper'])   # generic

    def test_blastoise_resolves_to_the_values_before_layering(self):
        """Blastoise's kinds equal the pre-layering values (astro_dock tests/data, 2026-10-26), except the
        repos list, which became repos.all plus per-role lists."""
        baseline = json.loads((HERE / 'tests/data/blastoise-baseline.json').read_text())
        for kind, expected in baseline.items():
            with self.subTest(kind=kind):
                run = subprocess.run(['python3', '-I', str(ASTRO / 'devices/profile.py'), 'kind',
                                      str(HERE / 'blastoise'), kind, str(HERE / 'blastoise/network.env')],
                                     capture_output=True, text=True, env={'PATH': os.environ['PATH']})
                self.assertEqual(run.returncode, 0, run.stderr)
                values = {}
                for line in run.stdout.splitlines():
                    name, _, value = line.split(' || ', 1)[1].partition('=')
                    values[name] = (__import__('shlex').split(value) or [''])[0]
                if kind == 'hosts':
                    added = set(values) - set(expected)
                    self.assertTrue(all(k.startswith(('HW_SOURCES', 'HW_REPOS_')) for k in added), added)
                    self.assertEqual(values['HW_REPOS_ALL'], expected.pop('HW_REPOS'))
                    values = {k: v for k, v in values.items() if k not in added}
                    expected = {**expected, 'HW__KEYS': expected['HW__KEYS'].replace('repos', 'repos sources')}
                self.assertEqual(values, expected)


if __name__ == '__main__':
    unittest.main()
