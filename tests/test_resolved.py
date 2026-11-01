"""astro_dock configured with this repository as its profile source resolves what the fleet deployed.

A `git clone` of ASTRO_DOCK (see test_profiles) gets a profiles.env naming this repository at its HEAD,
runs `hw.sh profile sync` and must resolve exactly tests/data/resolved-baseline.json, recorded with the
submodule-era astro_dock (tests/resolved.py). Nothing is contacted: the clones are local.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from resolved import resolved
from test_profiles import ASTRO, HERE

SYNC = ASTRO is not None and 'profile sync' in (ASTRO / 'hw.sh').read_text()
BASELINE = HERE / 'tests/data/resolved-baseline.json'


@unittest.skipUnless(SYNC, 'needs an astro_dock checkout with hw.sh profile sync (set ASTRO_DOCK)')
class ConfiguredSource(unittest.TestCase):
    def test_a_clone_configured_with_this_repository_resolves_the_baseline(self):
        head = subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], check=True, capture_output=True,
                              text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            clone, config = Path(tmp, 'astro_dock'), Path(tmp, 'config')
            subprocess.run(['git', 'clone', '-q', str(ASTRO), str(clone)], check=True, capture_output=True)
            (config / 'astro').mkdir(parents=True)
            (config / 'astro/profiles.env').write_text('ASTRO_PROFILES_URL=%s\nASTRO_PROFILES_REF=%s\n' % (HERE, head))
            env = {k: v for k, v in os.environ.items() if not k.startswith(('ASTRO_', 'HW_'))}
            run = subprocess.run(['bash', str(clone / 'hw.sh'), 'profile', 'sync'], capture_output=True, text=True,
                                 env={**env, 'XDG_CONFIG_HOME': str(config), 'ASTRO_SECRETS': '/nonexistent',
                                      'ASTRO_ENV_FILE': '/dev/null'}, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(subprocess.run(['git', '-C', str(clone / 'profiles'), 'rev-parse', 'HEAD'],
                                            capture_output=True, text=True).stdout.strip(), head)
            got = resolved(clone, {'XDG_CONFIG_HOME': str(config)})
        want = json.loads(BASELINE.read_text())
        self.maxDiff = None
        self.assertEqual(sorted(got), sorted(want))
        for key in sorted(want):
            with self.subTest(key=key):
                self.assertEqual(got[key], want[key])


if __name__ == '__main__':
    unittest.main()
