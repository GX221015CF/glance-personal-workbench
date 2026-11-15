"""One school-data location shared by normal Windows and packaged dev launches.

The existing services remain the owners of their DPAPI files. Legacy AppData
files are read only until the next successful query commits the shared file.
"""
import os
import json
import secrets
from pathlib import Path


def save_encrypted_json(path, value, *, create_only=False):
    """Commit DPAPI JSON in the destination directory, preserving a valid old file."""
    from gmail_bridge import protect
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(protect(json.dumps(value, ensure_ascii=False).encode('utf8')))
            output.flush()
            os.fsync(output.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def private_root(system):
    if system not in ('SUFE', 'Canvas', 'Academic'):
        raise ValueError('Unknown school system')
    from workbench_config import DATA_DIR
    return DATA_DIR / 'SchoolData' / system


def read_path(root, default_root, legacy_root, name):
    path = root / name
    # Tests and explicit callers with an isolated root must stay isolated.
    # Existing but corrupt shared files must never fall back to an old version.
    if not path.exists() and root == default_root:
        return legacy_root / name
    return path
