"""Review an explicit allowlist and its fingerprints before a local commit."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'docs/public-files.json'
EXTRA_FILES = [
    '.gitignore', '.gitattributes', 'README.md', 'THIRD_PARTY_NOTICES.md',
    'requirements.txt', 'package.json', 'package-lock.json', 'run.py',
    'app/workbench_config.py',
    'config/workbuddy-mcp.example.json', 'config/workbuddy-preferences.example.json',
    'tools/install_glance.py', 'tools/verify.py', 'tools/check_http.py', 'tools/check_public.py',
    'docs/RUNNING.md', 'docs/CASE-STUDIES.md', 'docs/SOURCE.md', 'docs/MEDIA.md',
    'docs/VERIFICATION.md', 'docs/verification-summary.json',
    'docs/source-manifest.json', 'docs/media-manifest.json', 'docs/public-files.json',
    '.github/workflows/pages.yml', 'watch/index.html',
    'THIRD_PARTY_LICENSES/Glance-AGPL-3.0.txt',
]
SECRET = re.compile(
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|'
    r'sk-[A-Za-z0-9_-]{30,}|AIza[A-Za-z0-9_-]{30,})\b'
)
PERSONAL = re.compile(r'[CG]:[\\/]Users[\\/]|G:[\\/]ChatGPT[\\/]|'
                      r'@(?:qq\.com|gmail\.com|outlook\.com)', re.I)


def public_paths():
    source = json.loads((ROOT / 'docs/source-manifest.json').read_text('utf8'))
    media = json.loads((ROOT / 'docs/media-manifest.json').read_text('utf8'))
    return sorted(set(EXTRA_FILES + [row['path'] for row in source['files']]
                      + [row['path'] for row in media['files']]))


def check_public(write=False):
    rows = []
    for name in public_paths():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT), name
        if path == MANIFEST:
            rows.append({'path': name, 'bytes': None, 'sha256': None})
            continue
        assert path.is_file(), name
        assert not path.is_symlink(), name
        assert path.suffix not in ('.dpapi', '.db', '.sqlite3', '.log', '.exe', '.env'), name
        raw = path.read_bytes()
        if path.suffix not in ('.png', '.mp4'):
            content = raw.decode('utf8')
            assert not SECRET.search(content), f'Review possible credential in {name}'
            # Test-only identities are separately identified as synthetic fixtures.
            if not path.name.startswith('test_') and not path.name.startswith('test-'):
                assert not PERSONAL.search(content), f'Review possible private identifier in {name}'
        rows.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    value = {'date': '2026-11-15', 'scope': 'public-showcase-v0.1.0', 'files': rows}
    if write:
        MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')
    else:
        assert json.loads(MANIFEST.read_text('utf8')) == value, 'Refresh reviewed public-files.json'
    return {'files': len(rows), 'bytes': sum(row['bytes'] or 0 for row in rows),
            'credentialPatternScan': 'passed', 'privateIdentifierScan': 'passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='保存已审查清单')
    print(check_public(parser.parse_args().write))
