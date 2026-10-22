"""Verify a user-supplied complete release ZIP; extract only animation resources.

Uses the standard library. Never downloads files or runs anything from the ZIP.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 4 * 1024 ** 3


class AssetError(ValueError):
    """The archive, destination, or resource configuration failed validation."""


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or path.is_absolute()
            or any(part in ('', '.', '..') for part in name.rstrip('/').split('/'))):
        raise AssetError(f'Unsafe archive/resource path: {name!r}')
    # Windows normalizes trailing dots/spaces and reserves these device names.
    devices = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
               *(f'LPT{i}' for i in range(1, 10))}
    for part in path.parts:
        if part.endswith((' ', '.')) or part.split('.')[0].upper() in devices:
            raise AssetError(f'Unsafe Windows path: {name!r}')
    return path


def reject_links(path):
    """Do not write through symlinks or Windows directory junctions."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(os.path, 'isjunction', lambda p: False)(part):
            raise AssetError(f'Destination contains a link or junction: {part}')


def target_path(destination, relative):
    parts = safe_name(relative).parts
    target = destination.joinpath(*parts)
    reject_links(target)
    if not target.resolve().is_relative_to(destination.resolve()):
        raise AssetError(f'Resource escapes destination: {relative}')
    return target


def read_npy_header(stream, total_size):
    """Read shape/dtype without NumPy, pickle, or loading the pixel payload."""
    if stream.read(6) != b'\x93NUMPY':
        raise AssetError('Resource is not an NPY array')
    version = stream.read(2)
    if version == b'\x01\x00':
        length_bytes = stream.read(2)
        if len(length_bytes) != 2:
            raise AssetError('Truncated NPY header')
        header_length = struct.unpack('<H', length_bytes)[0]
        prefix_size = 10
    elif version in (b'\x02\x00', b'\x03\x00'):
        length_bytes = stream.read(4)
        if len(length_bytes) != 4:
            raise AssetError('Truncated NPY header')
        header_length = struct.unpack('<I', length_bytes)[0]
        prefix_size = 12
    else:
        raise AssetError('Unsupported NPY format')
    if header_length > 65536:
        raise AssetError('NPY header is too large')
    raw = stream.read(header_length)
    if len(raw) != header_length:
        raise AssetError('Truncated NPY header')
    try:
        header = ast.literal_eval(raw.decode('utf-8' if version[0] == 3 else 'latin1'))
    except (ValueError, SyntaxError, UnicodeError) as exc:
        raise AssetError('Invalid NPY header') from exc
    if not isinstance(header, dict):
        raise AssetError('Invalid NPY header dictionary')
    shape = header.get('shape')
    if (header.get('descr') != '|u1' or header.get('fortran_order') is not False
            or not isinstance(shape, tuple) or len(shape) != 4 or shape[-1] != 4
            or any(type(n) is not int or n <= 0 for n in shape)):
        raise AssetError('Expected a C-order uint8 array shaped (frames, height, width, 4)')
    if prefix_size + header_length + math.prod(shape) != total_size:
        raise AssetError('NPY dimensions do not match the resource length')
    return shape


def validate_config(config, shapes):
    manifest = config.get('manifest')
    if not isinstance(manifest, dict) or not manifest:
        raise AssetError('Missing animation manifest')
    for key, clip in manifest.items():
        name = clip.get('file', '')
        safe_name(name)
        if name not in shapes:
            raise AssetError(f'{key}: missing NPY resource {name}')
        n, height, width, channels = shapes[name]
        frames = clip.get('frames')
        if type(frames) is not int or frames <= 0 or not isinstance(clip.get('fps'), (float, int)) or clip['fps'] <= 0:
            raise AssetError(f'{key}: invalid frame count or rate')
        if 'hold_index' in clip:
            hold = clip['hold_index']
            if type(hold) is not int or not -n <= hold < n:
                raise AssetError(f'{key}: hold frame is outside the array')
        else:
            start = clip.get('frame_start', 0)
            if type(start) is not int or start < 0 or start + frames > n:
                raise AssetError(f'{key}: frame range is outside the array')
        if 'storage_shape' in clip and clip['storage_shape'] != [height, width, channels]:
            raise AssetError(f'{key}: stored shape differs from config')
        if 'crop_rect' in clip:
            rect = clip['crop_rect']
            size = clip.get('canvas_size')
            if (not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect)
                    or type(size) is not int or rect[0] < 0 or rect[1] < 0
                    or rect[2:] != [width, height] or rect[0] + width > size or rect[1] + height > size):
                raise AssetError(f'{key}: invalid crop restoration geometry')
        if not isinstance(clip.get('start'), str) or not isinstance(clip.get('end'), str):
            raise AssetError(f'{key}: missing posture endpoints')

    def route(keys, start, expected_end=None):
        state = start
        if not isinstance(keys, list):
            raise AssetError('Animation route must be a list')
        for key in keys:
            if key not in manifest or manifest[key]['start'] != state:
                raise AssetError(f'Illegal animation route: {state} -> {key}')
            state = manifest[key]['end']
        if expected_end is not None and state != expected_end:
            raise AssetError(f'Animation route ends at {state}, expected {expected_end}')

    route(config.get('demo', []), 'stand_front')
    if config.get('startup'):
        route(config['startup'], 'offscreen', 'stand_front')
    for keys in config.get('action_groups', []):
        route(keys, 'stand_front', 'stand_front')
    for state, keys in config.get('exit_routes', {}).items():
        route(keys, state, 'offscreen')


def prepare_assets(zip_path, destination=ROOT, *, lock_path=None, check_only=False):
    """Return resource counts. Test fixtures may supply their own explicit lock."""
    lock_path = Path(lock_path) if lock_path is not None else ROOT / 'assets.lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    archive = Path(zip_path)
    destination = Path(os.path.abspath(destination))
    reject_links(destination)
    if archive.stat().st_size != lock['zip_bytes'] or sha256_file(archive) != lock['zip_sha256']:
        raise AssetError('Release ZIP size/SHA256 differs from assets.lock.json; download the exact 0.3.2 release')
    entries = lock['files']
    prefix = lock['archive_prefix']
    safe_name(prefix)
    required = {}
    for entry in entries:
        name = entry['path']
        safe_name(name)
        if not name.startswith('runtime/') or Path(name).suffix.lower() not in ('.npy', '.png', '.ico'):
            raise AssetError(f'Unexpected resource type: {name}')
        if name.casefold() in {n.casefold() for n in required}:
            raise AssetError(f'Duplicate resource: {name}')
        if type(entry['bytes']) is not int or entry['bytes'] <= 0:
            raise AssetError(f'Invalid resource size: {name}')
        required[name] = entry
    total = sum(entry['bytes'] for entry in entries)
    if total > MAX_ASSET_BYTES:
        raise AssetError('Resource payload exceeds the 4 GiB limit')

    with zipfile.ZipFile(archive) as z:
        names = {}
        for info in z.infolist():
            safe_name(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise AssetError(f'Archive contains a symbolic link: {info.filename}')
            normalized = info.filename.casefold()
            if normalized in names:
                raise AssetError(f'Archive contains duplicate paths: {info.filename}')
            names[normalized] = info
        config_name = prefix + 'config.json'
        if config_name.casefold() not in names or names[config_name.casefold()].file_size > MAX_CONFIG_BYTES:
            raise AssetError('Missing or oversized release config.json')
        config_bytes = z.read(config_name)
        if hashlib.sha256(config_bytes).hexdigest() != lock['config_sha256']:
            raise AssetError('Release config hash differs from the source example')
        config = json.loads(config_bytes)
        shapes = {}
        for name, entry in required.items():
            member = prefix + name
            if member.casefold() not in names or names[member.casefold()].filename != member:
                raise AssetError(f'Required resource missing or differently cased: {name}')
            info = names[member.casefold()]
            if info.file_size != entry['bytes']:
                raise AssetError(f'Resource length differs: {name}')
            if name.endswith('.npy'):
                with z.open(member) as stream:
                    shapes[name] = read_npy_header(stream, info.file_size)
        validate_config(config, shapes)
        referenced = {clip['file'] for clip in config['manifest'].values()}
        if referenced != set(shapes):
            raise AssetError('Config references and locked NPY assets differ')

        config_target = target_path(destination, 'config.json')
        if config_target.exists() and config_target.read_bytes() != config_bytes:
            raise AssetError('Destination config.json was modified; choose an empty output directory')
        missing = []
        for name, entry in required.items():
            target = target_path(destination, name)
            if target.exists():
                if not target.is_file() or target.stat().st_size != entry['bytes'] or sha256_file(target) != entry['sha256']:
                    raise AssetError(f'Existing resource differs; refusing to overwrite: {name}')
            else:
                missing.append((name, entry, target))
        report = {'version': lock['version'], 'assets': len(required), 'arrays': len(shapes),
                  'clips': len(config['manifest']), 'bytes': total, 'missing': len(missing),
                  'zip_sha256': lock['zip_sha256'], 'check_only': check_only}
        if check_only:
            return report
        ancestor = destination
        while not ancestor.exists():
            ancestor = ancestor.parent
        needed = sum(entry['bytes'] for _, entry, _ in missing)
        if shutil.disk_usage(ancestor).free < needed + 64 * 1024 ** 2:
            raise AssetError('Not enough free space for the extracted runtime assets')
        for name, entry, target in missing:
            target.parent.mkdir(parents=True, exist_ok=True)
            reject_links(target)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.dandan-', suffix='.part', delete=False) as out:
                    temporary = Path(out.name)
                    digest = hashlib.sha256()
                    with z.open(prefix + name) as source:
                        while block := source.read(1024 * 1024):
                            out.write(block)
                            digest.update(block)
                if digest.hexdigest() != entry['sha256']:
                    raise AssetError(f'Extracted resource SHA256 differs: {name}')
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if not config_target.exists():
            config_target.write_bytes(config_bytes)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release_zip', type=Path, help='Downloaded dandan-0.3.2-windows-x64.zip')
    parser.add_argument('--output-dir', type=Path, default=ROOT, help='Default: this example directory')
    parser.add_argument('--check-only', action='store_true', help='Verify the archive and existing assets without extracting')
    args = parser.parse_args(argv)
    try:
        report = prepare_assets(args.release_zip, args.output_dir, check_only=args.check_only)
    except (AssetError, OSError, KeyError, TypeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        parser.exit(1, f'Asset preparation failed: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
