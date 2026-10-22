"""Keep a fresh checkout compatible with the exact checked release assets."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import unittest

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/dandan'


class ExampleConfigTests(unittest.TestCase):
    def test_checked_out_config_keeps_release_bytes(self):
        lock = json.loads((EXAMPLE / 'assets.lock.json').read_text(encoding='utf-8'))
        raw = (EXAMPLE / 'config.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), lock['config_sha256'])

    def test_runtime_paths_are_relative_and_local(self):
        config = json.loads((EXAMPLE / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(len(config['manifest']), 57)
        for clip in config['manifest'].values():
            path = clip['file']
            self.assertFalse(PureWindowsPath(path).drive)
            self.assertFalse(Path(path).is_absolute())
            self.assertNotIn('..', Path(path).parts)
            self.assertTrue(path.startswith('runtime/'))


if __name__ == '__main__':
    unittest.main()
