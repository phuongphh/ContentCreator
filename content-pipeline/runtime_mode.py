"""Explicit Dev data roots and a publishing barrier shared by all entry points."""

import json
import os
from pathlib import Path

_MODE = os.environ.get('CONTENT_RUNTIME_MODE', 'production')
if _MODE not in ('production', 'development'):
    raise ValueError('CONTENT_RUNTIME_MODE must be production or development')
DEVELOPMENT = _MODE == 'development'
_MARKER = '.fleet-dev-root.json'


def initialize_dev_state(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('A new absolute non-symlink Dev state directory is required')
    if path.exists() and any(path.iterdir()) and not (path/_MARKER).is_file():
        raise ValueError('Do not adopt a nonempty directory as Dev state')
    if path.resolve() != path:
        raise ValueError('Dev state cannot traverse a symlink')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = path/_MARKER
    if marker.is_symlink():
        raise ValueError('Dev state marker cannot be a symlink')
    expected = {'project':'ContentCreator','environment':'dev'}
    if marker.exists() and json.loads(marker.read_text()) != expected:
        raise ValueError('Dev state belongs to a different project')
    marker.write_text(json.dumps(expected)+'\n')
    marker.chmod(0o600)
    return path


def state_path(name, production_path):
    if not DEVELOPMENT:
        return production_path
    value = os.environ.get('CONTENT_STATE_DIR', '')
    root = Path(value)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('Development requires a separate absolute CONTENT_STATE_DIR')
    if root.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('Dev state must be outside the immutable source artifact')
    marker = root/_MARKER
    if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != {'project':'ContentCreator','environment':'dev'}:
        raise ValueError('Initialize the separate Dev state directory before starting')
    return str(root/name)


def assert_publishing_allowed():
    if DEVELOPMENT:
        raise RuntimeError('Publishing is disabled in ContentCreator Dev')
