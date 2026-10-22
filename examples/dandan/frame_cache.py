"""Optional, disposable disk-backed decoded frames for responsive playback.

Original assets remain the only required files.  NPZ data is decoded once into
an atomic NPY cache in user data, then opened read-only through a memory map. An absent
drive, insufficient space, invalid cache, or write failure never makes a valid
source asset unplayable.  Only this module's uniquely named temporary files are
removed; original files and older fingerprinted caches are never deleted here.
"""
import hashlib
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import zipfile

import numpy as np
from app_paths import FROZEN,user_data_root


CACHE_ROOT = Path(os.environ['DOGPET_CACHE_DIR']) if os.environ.get('DOGPET_CACHE_DIR') else (user_data_root()/'frame_cache')
RESERVE_BYTES = 2 * 1024 ** 3
_KEY_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def cache_path_for(path):
    """Compute the cache name without creating directories or reading pixels."""
    source = Path(path).resolve()
    stat = source.stat()
    identity = f'{source}\0{stat.st_size}\0{stat.st_mtime_ns}'.encode('utf-8')
    fingerprint = hashlib.sha256(identity).hexdigest()[:20]
    return Path(CACHE_ROOT) / f'{source.stem[:100]}-{fingerprint}.npy'


def _key_lock(path):
    # The registry lock is held only while looking up a particular source lock.
    # Large decodes for different assets are never serialized by this registry.
    with _LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(str(path), threading.Lock())


def _archive_spec(source):
    """Read just the small NPY header inside a ZIP, not the image-frame body."""
    with zipfile.ZipFile(source) as archive:
        with archive.open('frames.npy') as header:
            version = np.lib.format.read_magic(header)
            if version == (1, 0):
                shape, _, dtype = np.lib.format.read_array_header_1_0(header)
            elif version in ((2, 0), (3, 0)):
                # Frame arrays have a simple ASCII uint8 dtype; their header is
                # equally readable by the public v2 reader for the v3 encoding.
                shape, _, dtype = np.lib.format.read_array_header_2_0(header)
            else:
                raise ValueError(f'Unsupported NPY header version {version}')
    return tuple(shape), np.dtype(dtype)


def _validate_frames(frames, expected=None):
    if not isinstance(frames, np.ndarray):
        raise ValueError('Frame asset is not an ndarray')
    if frames.ndim != 4 or frames.shape[-1] != 4 or any(n <= 0 for n in frames.shape):
        raise ValueError(f'Invalid RGBA frame-array shape: {frames.shape}')
    if frames.dtype != np.dtype('uint8'):
        raise ValueError(f'Invalid RGBA frame-array dtype: {frames.dtype}')
    if expected is not None and (frames.shape != expected[0] or frames.dtype != expected[1]):
        raise ValueError('Decoded cache does not match the source shape/dtype')
    return frames


def _close_mapping(frames):
    mapping = getattr(frames, '_mmap', None)
    if mapping is not None:
        mapping.close()


def _read_cache(path, expected):
    frames = None
    try:
        # Reject non-NPY data before NumPy can mistake a truncated ZIP for an
        # archive and leak an internal file handle while opening that archive.
        with path.open('rb') as header:
            if header.read(6) != b'\x93NUMPY':
                return None
        frames = np.load(path, mmap_mode='r', allow_pickle=False)
        return _validate_frames(frames, expected)
    except (OSError, ValueError, EOFError, TypeError, OverflowError, zipfile.BadZipFile):
        if isinstance(frames, np.lib.npyio.NpzFile):
            frames.close()
        elif frames is not None:
            _close_mapping(frames)
        return None


def _load_archive(source):
    with np.load(source, allow_pickle=False) as archive:
        return _validate_frames(archive['frames'])


def _can_store(directory, nbytes):
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(directory).free > nbytes + RESERVE_BYTES
    except (OSError, ValueError):
        return False


def _store_cache(path, frames, expected):
    """Best effort only: atomic replace plus validation, or return None."""
    if not _can_store(path.parent, frames.nbytes):
        return None
    temporary = None
    try:
        # A process-unique temporary file also prevents different app processes
        # from exposing half-written arrays to one another.
        with tempfile.NamedTemporaryFile(
                mode='wb', prefix=f'.{path.stem}.{os.getpid()}.', suffix='.tmp.npy',
                dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            np.save(handle, frames, allow_pickle=False)
            handle.flush()
        os.replace(temporary, path)
        return _read_cache(path, expected)
    except (OSError, ValueError, EOFError, TypeError, OverflowError, zipfile.BadZipFile):
        # Another process may have won a replace while its mmap holds the file
        # open on Windows.  Use that complete cache if it is valid.
        return _read_cache(path, expected)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_cached_frames(path, mmap_mode=None):
    """Load RGBA frames, using an optional read-only decoded cache for NPZ.

    ``mmap_mode`` remains accepted for compatibility with the old loader; NPY
    and cache files deliberately always use ``'r'`` to keep assets immutable.
    A cache hit reads only the source archive header and mapped cache header.
    On a cold/cacheless run the source array is returned if caching is unsafe.
    A corrupt *source* remains an error rather than being silently hidden.
    """
    source = Path(path)
    if source.suffix.lower() != '.npz':
        return _validate_frames(np.load(source, mmap_mode='r', allow_pickle=False))

    try:
        cache = cache_path_for(source)
        expected = _archive_spec(source)
    except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile):
        # A readable but uncommon archive can still be decoded by NumPy; do
        # not let optional cache introspection become a new asset dependency.
        return _load_archive(source)

    with _key_lock(cache):
        frames = _read_cache(cache, expected)
        if frames is not None:
            return frames
        frames = _load_archive(source)
        # Do not label data with a stale fingerprint if the source was replaced
        # while decoding.  Playback is still safe with the just-read array.
        try:
            if cache_path_for(source) != cache:
                return frames
        except OSError:
            return frames
        cached = _store_cache(cache, frames, expected)
        return frames if cached is None else cached
