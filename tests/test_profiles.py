"""The MHSeals profiles load with astro_dock's loader and resolve as the fleet expects.

ASTRO_DOCK is an astro_dock checkout (default: ../astro_dock, or the parent when this repo is its
profiles/ submodule). Nothing is contacted: hostnames and NICs are faked.
"""
import importlib
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

import yaml

from resolved import FACTS, MACHINES

try:
    from mhseals_hardware import configuration, vessel     # the thruster driver's own reader (ROS-free)
except ImportError:
    configuration = None
TESTING = importlib.util.find_spec('mhseals_testing') is not None    # the TUI's hull: reader (needs numpy)

HERE = Path(__file__).resolve().parents[1]
CANDIDATES = [os.environ.get('ASTRO_DOCK'), HERE.parent, HERE.parent / 'astro_dock']
ASTRO = next((Path(p) for p in CANDIDATES if p and (Path(p) / 'devices/profile.py').exists()), None)
LOADER = ASTRO is not None and "command == 'resolve'" in (ASTRO / 'devices/profile.py').read_text()
MACS = {name: machine[2] for name, machine in MACHINES.items() if machine[2]}   # the boat computers


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
        for kind in set(shown) - {'profile', 'network_env', 'registry'} - FACTS:
            with self.subTest(kind=kind):
                run = self.hw('--vessel', 'blastoise', kind, '--help')
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_boat_computers_and_the_station_resolve_to_blastoise(self):
        for host, mac in MACS.items():
            with self.subTest(host=host):
                run = self.hw('whoami', hostname='roboboat', mac=mac)      # the container hostname is ignored
                self.assertIn('host      %s (MAC %s' % (host, mac), run.stdout, run.stderr)
                self.assertIn('vessel    blastoise ', run.stdout)
        shutil.rmtree(self.sys / 'eth0')
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
        """The one subnet key is LAN_SUBNET; the ROS domain is the profile's alone."""
        self.assertNotIn('BOAT_SUBNET=', (HERE / 'blastoise/network.env').read_text())
        run = self.hw('whoami', hostname='somelaptop', HW_ADDRS='192.168.0.57/24')
        self.assertIn('station   control_station\n', run.stdout, run.stderr)
        self.assertIn('vessel    blastoise (boat LAN 192.168.0.57 in 192.168.0.0/24)', run.stdout)
        run = self.hw('env', 'ROS_DOMAIN_ID', hostname='somelaptop', HW_ADDRS='192.168.0.57/24')
        self.assertEqual((run.returncode, run.stdout.strip()), (0, '42'), run.stderr)

    def test_roles_name_the_time_server_operators_and_lidar_data_host(self):
        for key, value in (('TIME_SERVER_IP', '192.168.0.152'), ('BOAT_COMPUTER_IP', '192.168.0.152'),
                           ('OPERATOR_IP', '192.168.0.2'), ('CAMERA_HOST_IP', '192.168.0.153')):
            with self.subTest(key=key):
                run = self.hw('--vessel', 'blastoise', 'env', key)
                self.assertEqual((run.returncode, run.stdout.strip()), (0, value), run.stderr)

    def test_repos_per_host(self):
        def repos(host):
            run = self.hw('--host', host, 'hosts', 'repos')
            self.assertEqual(run.returncode, 0, run.stderr)
            return [line.split()[0] for line in run.stdout.splitlines()]
        everyone = ['mhseals_hardware', 'mhseals_nav', 'mhseals_testing']
        self.assertEqual(repos('blastoise-odroid'), everyone)
        self.assertEqual(repos('blastoise-jetson'), everyone + ['direct_lidar_inertial_odometry'])
        station = everyone + ['rviz-plugin-zed-od', 'zed-ros2-wrapper', 'mhseals_agents']
        self.assertEqual(repos('tranquility'), station)
        self.assertEqual(repos('somelaptop'), station)   # generic

    def test_the_gateway_role_is_tranquility(self):
        """The Jetson's ssh hop is the `gateway` role (resolved-baseline.json); here that is tranquility."""
        hop = subprocess.run(['python3', '-I', str(ASTRO / 'devices/profile.py'), 'hosts',
                              str(HERE / 'blastoise/devices.yaml'), 'gateway'], capture_output=True, text=True)
        self.assertEqual(hop.stdout, 'tranquility\n', hop.stderr)


@unittest.skipUnless(configuration or os.environ.get('REQUIRE_HARDWARE'), 'needs mhseals_hardware on PYTHONPATH')
class ThrusterWiring(unittest.TestCase):
    def test_every_vessel_loads_with_the_thruster_drivers_strict_reader(self):
        """Duplicate keys, a non-wiring key or a bad channel_map refuse here, not first on the boat."""
        self.assertIsNotNone(configuration, 'REQUIRE_HARDWARE is set but mhseals_hardware does not import')
        with tempfile.NamedTemporaryFile('w', suffix='.yaml') as empty:
            empty.write('{}\n')
            empty.flush()
            for env in sorted(HERE.glob('*/network.env')):
                with self.subTest(vessel=env.parent.name), unittest.mock.patch.dict(
                        os.environ, {'ASTRO_NETWORK_ENV': str(env)}):
                    vessel.section.cache_clear()
                    config = configuration.load_config(empty.name)
                    wiring = yaml.safe_load((env.parent / 'profile.yaml').read_text()).get('thrusters')
                    if wiring and 'channel_map' in wiring:
                        self.assertEqual(config['channel_map'], wiring['channel_map'])
        vessel.section.cache_clear()


@unittest.skipUnless(TESTING or os.environ.get('REQUIRE_TESTING'), 'needs mhseals_testing on PYTHONPATH')
class HullShape(unittest.TestCase):
    def test_every_vessels_hull_loads_with_the_tuis_reader(self):
        """A bad hull: (unknown shape or key) stops the testing TUI at import, so it refuses here first."""
        self.assertTrue(TESTING, 'REQUIRE_TESTING is set but mhseals_testing is not on PYTHONPATH')
        from mhseals_hardware import vessel
        hull = None
        for env in sorted(HERE.glob('*/network.env')):
            with self.subTest(vessel=env.parent.name), unittest.mock.patch.dict(
                    os.environ, {'ASTRO_NETWORK_ENV': str(env)}):
                vessel.section.cache_clear()
                hull = hull or importlib.import_module('mhseals_testing.ui.minimap.hull')
                shape = (yaml.safe_load((env.parent / 'profile.yaml').read_text()).get('hull') or {}).get('shape')
                if shape:
                    self.assertEqual(hull._settings()['shape'], shape)
                else:
                    hull._settings()
        vessel.section.cache_clear()


if __name__ == '__main__':
    unittest.main()
