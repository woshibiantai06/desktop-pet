"""Tiny synthetic archives exercise extraction boundaries without the release."""
import hashlib
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from prepare_assets import AssetError, prepare_assets


def npy_bytes(shape=(2, 3, 4, 4), dtype='|u1'):
    header = repr({'descr': dtype, 'fortran_order': False, 'shape': shape})
    raw = (header + ' ' * ((-10 - len(header) - 1) % 64) + '\n').encode('ascii')
    payload_size = 1
    for n in shape:
        payload_size *= n
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(raw)) + raw + bytes(payload_size)


class PrepareAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dandan-extraction-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'
        self.archive = self.root / 'release.zip'
        self.lock_path = self.root / 'lock.json'
        self.prefix = 'pet/_internal/'
        self.config = {'manifest': {'idle': {'file': 'runtime/asset_001.npy', 'start': 'stand_front',
                       'end': 'stand_front', 'frames': 2, 'fps': 30,
                       'storage_shape': [3, 4, 4], 'canvas_size': 10,
                       'crop_rect': [1, 2, 4, 3]}},
                       'demo': ['idle'], 'action_groups': [['idle']]}

    def build(self, *, extra=None, array=None, missing=False, bad_asset_hash=False):
        config_bytes = json.dumps(self.config).encode('utf-8')
        array = npy_bytes() if array is None else array
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(self.prefix + 'config.json', config_bytes)
            if not missing:
                z.writestr(self.prefix + 'runtime/asset_001.npy', array)
            z.writestr('pet/do_not_run.py', "raise RuntimeError('must never execute')")
            if extra:
                z.writestr(extra, b'ignored')
        self.lock = {'version': 'test', 'zip_bytes': self.archive.stat().st_size,
                     'zip_sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                     'archive_prefix': self.prefix,
                     'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
                     'files': [{'path': 'runtime/asset_001.npy', 'bytes': len(array),
                                'sha256': '0' * 64 if bad_asset_hash else hashlib.sha256(array).hexdigest()}]}
        self.lock_path.write_text(json.dumps(self.lock), encoding='utf-8')

    def prepare(self, **kwargs):
        return prepare_assets(self.archive, self.output, lock_path=self.lock_path, **kwargs)

    def test_extracts_only_verified_whitelist_and_is_idempotent(self):
        self.build()
        report = self.prepare()
        self.assertEqual((report['arrays'], report['clips']), (1, 1))
        self.assertEqual((self.output / 'runtime/asset_001.npy').read_bytes(), npy_bytes())
        self.assertEqual(json.loads((self.output / 'config.json').read_bytes()), self.config)
        self.assertFalse((self.output / 'do_not_run.py').exists())
        self.assertEqual(self.prepare()['missing'], 0)

    def test_check_only_does_not_create_output(self):
        self.build()
        self.assertEqual(self.prepare(check_only=True)['missing'], 1)
        self.assertFalse(self.output.exists())

    def test_changed_zip_rejected_before_output(self):
        self.build()
        with self.archive.open('ab') as out:
            out.write(b'changed')
        with self.assertRaisesRegex(AssetError, 'SHA256'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_traversal_in_any_member_rejected(self):
        self.build(extra='../outside.txt')
        with self.assertRaisesRegex(AssetError, 'Unsafe'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_windows_drive_and_backslash_paths_rejected(self):
        for name in ('C:/outside.txt', 'pet/..\\outside.txt', 'pet/CON.txt', 'pet/trailing. '):
            with self.subTest(name=name):
                self.build(extra=name)
                with self.assertRaisesRegex(AssetError, 'Unsafe'):
                    self.prepare()

    def test_case_duplicate_members_rejected(self):
        self.build(extra=self.prefix + 'runtime/ASSET_001.npy')
        with self.assertRaisesRegex(AssetError, 'duplicate'):
            self.prepare()

    def test_archive_symlink_rejected(self):
        link = zipfile.ZipInfo('pet/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.build(extra=link)
        with self.assertRaisesRegex(AssetError, 'symbolic link'):
            self.prepare()

    def test_missing_required_array_rejected(self):
        self.build(missing=True)
        with self.assertRaisesRegex(AssetError, 'missing'):
            self.prepare()

    def test_config_frame_range_checked(self):
        self.config['manifest']['idle']['frame_start'] = 1
        self.build()
        with self.assertRaisesRegex(AssetError, 'frame range'):
            self.prepare()

    def test_crop_restoration_geometry_checked(self):
        self.config['manifest']['idle']['crop_rect'][0] = 9
        self.build()
        with self.assertRaisesRegex(AssetError, 'crop restoration'):
            self.prepare()

    def test_object_array_header_rejected_without_loading(self):
        self.build(array=npy_bytes(dtype='|O'))
        with self.assertRaisesRegex(AssetError, 'uint8'):
            self.prepare()

    def test_illegal_posture_route_rejected(self):
        self.config['manifest']['idle']['start'] = 'sit_front'
        self.build()
        with self.assertRaisesRegex(AssetError, 'Illegal animation route'):
            self.prepare()

    def test_existing_modified_asset_is_preserved(self):
        self.build()
        target = self.output / 'runtime/asset_001.npy'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'local changes')
        with self.assertRaisesRegex(AssetError, 'refusing to overwrite'):
            self.prepare()
        self.assertEqual(target.read_bytes(), b'local changes')

    def test_modified_config_is_preserved(self):
        self.build()
        self.output.mkdir()
        target = self.output / 'config.json'
        target.write_text('{"custom": true}', encoding='utf-8')
        with self.assertRaisesRegex(AssetError, 'config.json was modified'):
            self.prepare()
        self.assertEqual(target.read_text(encoding='utf-8'), '{"custom": true}')

    def test_failed_resource_hash_leaves_no_partial_file(self):
        self.build(bad_asset_hash=True)
        with self.assertRaisesRegex(AssetError, 'resource SHA256'):
            self.prepare()
        self.assertFalse((self.output / 'runtime/asset_001.npy').exists())
        self.assertEqual(list(self.output.rglob('*.part')), [])

    def test_existing_symlink_destination_rejected(self):
        self.build()
        with patch('prepare_assets.Path.is_symlink', return_value=True):
            with self.assertRaisesRegex(AssetError, 'link or junction'):
                self.prepare()


if __name__ == '__main__':
    unittest.main()
