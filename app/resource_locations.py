"""Shared program-relative resource location; legacy path is migration-only."""
import os
from pathlib import Path


def installed_resource_root(program_root):
    return Path(program_root) / 'resources'


def legacy_resource_root():
    """Return the former location; callers validate it before reading it."""
    value = os.environ.get('LOCALAPPDATA')
    if not value or '\0' in value or not Path(value).is_absolute():
        return None
    return Path(value) / 'Xingtu' / 'resources'
