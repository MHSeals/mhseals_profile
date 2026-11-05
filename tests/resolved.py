"""What astro_dock resolves from these profiles, per machine: whoami, the vessel's network env path and
ROS domain, the repositories, every member's merged profile (without FACTS) and every vessel kind's driver values.

`python3 tests/resolved.py ASTRO tests/data/resolved-baseline.json` records them with the astro_dock
checkout ASTRO, which finds the profiles itself (its profiles/ checkout); test_resolved compares against the
record. Regenerate it when a profile changes on purpose and review the diff.
Nothing is contacted: hostnames, NICs and addresses are faked.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

import yaml

# Vessel facts that the team's code reads but no hw.sh driver owns (hull: mhseals_testing's boat glyph;
# thrusters: mhseals_hardware's load_config). astro_dock only echoes them, so the record leaves them out;
# test_profiles' ThrusterWiring and HullShape load them with those readers.
FACTS = {'hull', 'thrusters'}

# The boat computers' permanent MACs, from their reservations (STATIC_LEASES: MAC=hostname=address ...).
LEASES = re.search(r'^STATIC_LEASES="([^"]*)"', (Path(__file__).resolve().parents[1] / 'blastoise/network.env')
                   .read_text(), re.M).group(1)
MAC = {host: mac for mac, host, _ in (lease.split('=') for lease in LEASES.split())}

# name: (hw.sh global flags, fake hostname, permanent MAC or None, local addresses)
MACHINES = {
    'blastoise-odroid': ((), 'roboboat', MAC['blastoise-odroid'], ''),     # in its container: MAC only
    'blastoise-jetson': ((), 'roboboat', MAC['blastoise-jetson'], ''),
    'tranquility': ((), 'tranquility', None, ''),
    'laptop-on-lan': ((), 'somelaptop', None, '192.168.0.57/24'),
    'laptop-off-lan': ((), 'somelaptop', None, ''),
    'laptop-squirtle': (('--vessel', 'squirtle'), 'somelaptop', None, ''),
    'laptop-host-blastoise': (('--vessel', 'blastoise', '--host', 'somelaptop'), 'somelaptop', None, ''),
}


def run_hw(astro, flags, args, hostname, mac, addrs, sysfs, extra):
    nic = Path(sysfs, 'eth0')
    if mac:
        nic.mkdir(exist_ok=True)
        (nic / 'address').write_text(mac + '\n')
        (nic / 'addr_assign_type').write_text('0\n')
    elif nic.exists():
        for f in nic.iterdir():
            f.unlink()
        nic.rmdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ASTRO_', 'HW_'))}
    env.update(ASTRO_SECRETS='/nonexistent', ASTRO_ENV_FILE='/dev/null', HW_HOSTNAME=hostname,
               HW_SYSFS_NET=str(sysfs), HW_ADDRS=addrs, HW_DT_MODEL=str(Path(sysfs, 'no-model')))
    env.update(extra)
    run = subprocess.run(['bash', str(Path(astro, 'hw.sh')), *flags, *args], capture_output=True, text=True,
                         env=env, timeout=60)
    return run.returncode, run.stdout


def resolved(astro, extra=None):
    """{machine: {...}, 'members': {...}, 'kinds': {...}} with the profile root written as <root>."""
    extra = dict(extra or {})
    out = {}
    with tempfile.TemporaryDirectory() as sysfs:
        def hw(machine, *args):
            flags, hostname, mac, addrs = MACHINES[machine]
            return run_hw(astro, flags, args, hostname, mac, addrs, sysfs, extra)

        rc, root = hw('laptop-off-lan', 'whoami', '--root')
        root = root.strip()
        if rc or not root:
            raise RuntimeError('%s finds no profile root' % astro)

        def norm(text):
            return text.replace(root, '<root>')

        for machine in MACHINES:
            rc, who = hw(machine, 'whoami')
            fields = dict(line.split(None, 1) for line in who.splitlines() if line.strip())
            fields.pop('profiles', None)                      # where the root is, and how it was supplied
            entry = {'whoami': {k: norm(v) for k, v in fields.items()}}
            rc, path = hw(machine, 'env')
            entry['env'] = norm(path.strip()) if rc == 0 else None
            rc, domain = hw(machine, 'env', 'ROS_DOMAIN_ID')
            entry['ros_domain'] = domain.strip() if rc == 0 else None
            rc, repos = hw(machine, 'hosts', 'repos')
            entry['repos'] = repos.split('\n') if rc == 0 else None
            out[machine] = entry
        members = sorted(p.parent.name for p in Path(root).glob('*/profile.yaml'))
        out['members'] = {}
        for m in members:
            shown = yaml.safe_load(norm(hw('laptop-off-lan', 'profile', 'show', m)[1]))
            out['members'][m] = yaml.safe_dump({k: v for k, v in shown.items() if k not in FACTS}, sort_keys=False)
        kinds = {}
        for vessel in members:
            if 'kind: vessel' not in Path(root, vessel, 'profile.yaml').read_text():
                continue
            netenv = Path(root, vessel, 'network.env')
            for kind in sorted(p.stem for p in Path(root).glob('*/*.yaml') if p.stem not in ('profile', 'topology', 'devices')):
                run = subprocess.run(['python3', '-I', str(Path(astro, 'devices/profile.py')), 'kind',
                                      str(Path(root, vessel)), kind, str(netenv) if netenv.exists() else ''],
                                     capture_output=True, text=True, env={'PATH': os.environ['PATH']})
                if run.returncode:
                    continue                                  # the vessel has no such kind
                values = {}
                for line in run.stdout.splitlines():
                    name, _, value = line.split(' || ', 1)[-1].partition('=')
                    values[name] = (shlex.split(value) or [''])[0]
                kinds['%s/%s' % (vessel, kind)] = {k: norm(v) for k, v in values.items()}
        out['kinds'] = kinds
    return out


if __name__ == '__main__':
    astro, target = sys.argv[1:3]
    Path(target).write_text(json.dumps(resolved(astro), indent=1, sort_keys=True) + '\n')
