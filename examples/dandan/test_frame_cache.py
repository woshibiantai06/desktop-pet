"""Cache safety and responsiveness without Qt or real desktop assets."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

import frame_cache


class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dog-pet-cache-tests-')
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        self.override = patch.object(frame_cache, 'CACHE_ROOT', self.cache)
        self.override.start()
        self.frames = np.arange(2 * 5 * 7 * 4, dtype='uint8').reshape(2, 5, 7, 4)
        self.source = self.root / 'pose.npz'
        np.savez_compressed(self.source, frames=self.frames)
        self.maps = []

    def tearDown(self):
        for frames in self.maps:
            frame_cache._close_mapping(frames)
        self.override.stop()
        self.temp.cleanup()

    def load(self, source=None, **kwargs):
        frames = frame_cache.load_cached_frames(source or self.source, **kwargs)
        if isinstance(frames, np.memmap):
            self.maps.append(frames)
        return frames

    def test_cold_npz_becomes_read_only_mmap_without_touching_source(self):
        original = self.source.read_bytes()
        loaded = self.load()
        self.assertIsInstance(loaded, np.memmap)
        self.assertFalse(loaded.flags.writeable)
        np.testing.assert_array_equal(loaded, self.frames)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(Path(loaded.filename), frame_cache.cache_path_for(self.source))

    def test_cache_hit_never_decodes_npz_body(self):
        first = self.load()
        frame_cache._close_mapping(first)
        with patch.object(frame_cache, '_load_archive', side_effect=AssertionError('decoded twice')):
            loaded = self.load()
        np.testing.assert_array_equal(loaded, self.frames)

    def test_cache_hit_does_not_pass_archive_to_numpy_load(self):
        self.load()
        actual_load = np.load
        def checked_load(path, *args, **kwargs):
            self.assertNotEqual(Path(path).suffix.lower(), '.npz')
            return actual_load(path, *args, **kwargs)
        with patch.object(frame_cache.np, 'load', side_effect=checked_load):
            loaded = self.load()
        self.assertIsInstance(loaded, np.memmap)

    def test_source_mtime_change_creates_new_fingerprint(self):
        self.load()
        old_path = frame_cache.cache_path_for(self.source)
        stat = self.source.stat()
        os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        new_path = frame_cache.cache_path_for(self.source)
        self.assertNotEqual(old_path, new_path)
        loaded = self.load()
        self.assertEqual(Path(loaded.filename), new_path)
        self.assertTrue(old_path.exists())

    def test_same_basename_in_other_folder_has_separate_cache(self):
        other = self.root / 'other' / self.source.name
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        stat = self.source.stat()
        os.utime(other, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(frame_cache.cache_path_for(self.source), frame_cache.cache_path_for(other))

    def test_npy_always_uses_read_only_memory_map_without_copy(self):
        source = self.root / 'stand.npy'
        np.save(source, self.frames)
        loaded = self.load(source, mmap_mode=None)
        self.assertIsInstance(loaded, np.memmap)
        self.assertFalse(loaded.flags.writeable)
        self.assertEqual(Path(loaded.filename), source)
        self.assertFalse(self.cache.exists())

    def test_invalid_drive_or_directory_falls_back_to_original_array(self):
        blocker = self.root / 'not-a-folder'
        blocker.write_bytes(b'file')
        with patch.object(frame_cache, 'CACHE_ROOT', blocker / 'cache'):
            loaded = self.load()
        self.assertNotIsInstance(loaded, np.memmap)
        np.testing.assert_array_equal(loaded, self.frames)

    def test_unwritable_cache_falls_back(self):
        with patch.object(frame_cache.Path, 'mkdir', side_effect=PermissionError('read-only drive')):
            loaded = self.load()
        self.assertNotIsInstance(loaded, np.memmap)
        np.testing.assert_array_equal(loaded, self.frames)

    def test_disk_reserve_is_preserved(self):
        usage = type('Usage', (), {'free': frame_cache.RESERVE_BYTES + self.frames.nbytes})()
        with patch.object(frame_cache.shutil, 'disk_usage', return_value=usage):
            loaded = self.load()
        self.assertNotIsInstance(loaded, np.memmap)
        self.assertFalse(frame_cache.cache_path_for(self.source).exists())

    def test_atomic_write_failure_does_not_prevent_playback(self):
        with patch.object(frame_cache.os, 'replace', side_effect=OSError('disk removed')):
            loaded = self.load()
        self.assertNotIsInstance(loaded, np.memmap)
        np.testing.assert_array_equal(loaded, self.frames)
        self.assertEqual(list(self.cache.glob('*.tmp.npy')), [])

    def test_corrupt_cache_is_rebuilt(self):
        path = frame_cache.cache_path_for(self.source)
        path.parent.mkdir()
        path.write_bytes(b'partial-npy')
        loaded = self.load()
        self.assertIsInstance(loaded, np.memmap)
        np.testing.assert_array_equal(loaded, self.frames)

    def test_truncated_zip_disguised_as_cache_is_rebuilt(self):
        path = frame_cache.cache_path_for(self.source)
        path.parent.mkdir()
        path.write_bytes(b'PK\x03\x04not-a-complete-zip')
        loaded = self.load()
        self.assertIsInstance(loaded, np.memmap)
        np.testing.assert_array_equal(loaded, self.frames)

    def test_shape_mismatched_cache_is_rebuilt(self):
        path = frame_cache.cache_path_for(self.source)
        path.parent.mkdir()
        np.save(path, np.zeros((3, 5, 7, 4), dtype='uint8'))
        loaded = self.load()
        self.assertEqual(loaded.shape, self.frames.shape)

    def test_dtype_mismatched_cache_is_rebuilt(self):
        path = frame_cache.cache_path_for(self.source)
        path.parent.mkdir()
        np.save(path, self.frames.astype('float32'))
        loaded = self.load()
        self.assertEqual(loaded.dtype, self.frames.dtype)

    def test_source_changed_mid_decode_is_not_written_with_old_fingerprint(self):
        actual_decode = frame_cache._load_archive
        old_path = frame_cache.cache_path_for(self.source)
        def changed(source):
            frames = actual_decode(source)
            stat = self.source.stat()
            os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            return frames
        with patch.object(frame_cache, '_load_archive', side_effect=changed):
            loaded = self.load()
        self.assertNotIsInstance(loaded, np.memmap)
        self.assertFalse(old_path.exists())

    def test_same_key_concurrent_requests_decode_once(self):
        actual_decode = frame_cache._load_archive
        count = []
        def counted(source):
            count.append(source)
            return actual_decode(source)
        with patch.object(frame_cache, '_load_archive', side_effect=counted):
            with ThreadPoolExecutor(max_workers=4) as executor:
                loaded = list(executor.map(frame_cache.load_cached_frames, [self.source] * 4))
        self.maps.extend(x for x in loaded if isinstance(x, np.memmap))
        self.assertEqual(len(count), 1)
        for frames in loaded:
            np.testing.assert_array_equal(frames, self.frames)

    def test_different_keys_are_not_serialized_by_global_lock(self):
        other = self.root / 'other.npz'
        np.savez_compressed(other, frames=self.frames)
        actual_decode = frame_cache._load_archive
        both_decoding = threading.Barrier(2)
        def joined(source):
            both_decoding.wait(timeout=5)
            return actual_decode(source)
        with patch.object(frame_cache, '_load_archive', side_effect=joined):
            with ThreadPoolExecutor(max_workers=2) as executor:
                loaded = list(executor.map(frame_cache.load_cached_frames, [self.source, other]))
        self.maps.extend(x for x in loaded if isinstance(x, np.memmap))
        self.assertEqual(len(loaded), 2)

    def test_invalid_source_is_an_error_not_a_silent_stale_cache(self):
        source = self.root / 'invalid.npz'
        np.savez_compressed(source, frames=np.zeros((2, 5, 7, 3), dtype='uint8'))
        with self.assertRaises(ValueError):
            self.load(source)


if __name__ == '__main__':
    unittest.main()
