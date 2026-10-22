"""Resource paths are read-only; frozen app output belongs to this Windows user."""
import os
from pathlib import Path
import sys

FROZEN=bool(getattr(sys,'frozen',False))
RESOURCE_ROOT=Path(__file__).resolve().parent


def user_data_root():
    override=os.environ.get('DOGPET_DATA_DIR')
    if override:return Path(override)
    if not FROZEN:return RESOURCE_ROOT
    base=os.environ.get('LOCALAPPDATA')
    return (Path(base) if base else Path.home()/'AppData/Local')/'DandanDesktopPet'


def writable_output(name):
    root=user_data_root();root.mkdir(parents=True,exist_ok=True)
    return root/name
