"""Run the published snapshot's offline regression and file checks."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse

from check_public import check_public

ROOT = Path(__file__).resolve().parent.parent


def static_checks():
    count = 0
    for path in ROOT.rglob('*.md'):
        if any(part in ('.git', '.local', 'node_modules') for part in path.parts):
            continue
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text('utf8')):
            if urllib.parse.urlsplit(target).scheme or target.startswith('#'):
                continue
            resolved = (path.parent / urllib.parse.unquote(target.split('#')[0])).resolve()
            assert resolved.is_relative_to(ROOT) and resolved.is_file(), (path.name, target)
            count += 1
    for path in (ROOT / 'app').rglob('*.py'):
        ast.parse(path.read_text('utf8'), filename=path.name)
    for path in (ROOT / 'app' / 'config').glob('*.json'):
        json.loads(path.read_text('utf8'))
    for path in (ROOT / 'config').glob('*.json'):
        json.loads(path.read_text('utf8'))
    yaml = (ROOT / 'app/config/glance.yml').read_text('utf8')
    includes = re.findall(r'\$include:\s*([^\s]+)', yaml)
    for include in includes:
        assert (ROOT / 'app/config' / include).is_file(), include
    for asset in re.findall(r'/assets/([\w/.-]+)', yaml):
        assert (ROOT / 'app/assets' / asset).is_file(), asset
    scripts = []
    for folder in ('app/assets', 'app/extensions'):
        for path in (ROOT / folder).rglob('*'):
            if path.suffix not in ('.js', '.mjs', '.cjs'):
                continue
            scripts.append(path)
            for reference in re.findall(r'''(?:from\s+|import\s*\()\s*["'](\.[^"']+)["']''', path.read_text('utf8')):
                target = urllib.parse.urlsplit(reference).path
                assert (path.parent / target).is_file(), (path.name, reference)
            subprocess.run(['node', '--check', str(path)], check=True, capture_output=True)
    return {'relativeLinks': count, 'configIncludes': len(includes),
            'pythonSyntax': True, 'javascriptSyntaxFiles': len(scripts),
            'relativeModuleReferences': 'passed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http', action='store_true', help='追加独立进程的HTTP启动检查')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'docs/source-manifest.json').read_text('utf8'))
    output = ROOT / '.local/verification'
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['WORKBENCH_DATA_DIR'] = str(ROOT / '.local/test-data')
    env['WORKBENCH_OFFLINE'] = '1'
    env['PYTHONUTF8'] = '1'
    # Assert every default store stays inside this copy before running fixtures.
    code = '''
import bridge, gmail_bridge, calendar_bridge, school_storage, ima_bridge
from school_connector.bridge_auth import auth_path
from workbench_config import DATA_DIR
paths = [bridge.CACHE, gmail_bridge.PRIVATE_ROOT, gmail_bridge.LEGACY_PRIVATE_ROOT,
         calendar_bridge.PRIVATE, school_storage.private_root('Canvas'),
         ima_bridge.ROOT, auth_path(), bridge.WECHAT.data_dir,
         bridge.WECHAT_ANALYSIS.output_root, bridge.WECHAT_ANALYSIS.config_path]
assert all(p.is_relative_to(DATA_DIR) for p in paths)
assert bridge.GMAIL.snapshot()['accounts'] == []
print('Default stores and empty accounts verified')
'''
    subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=ROOT / 'app', env=env, check=True)
    results = {'static': static_checks(), 'publicFiles': check_public()}
    commands = [
        ('python', [sys.executable, '-X', 'utf8', '-m', 'unittest', '-v', *manifest['pythonTests']]),
        ('node', ['node', '--test', *['tools/' + name for name in manifest['nodeTests']]]),
    ]
    for name, command in commands:
        log_path = output / (name + '-tests.log')
        with log_path.open('w', encoding='utf8') as log:
            result = subprocess.run(command, cwd=ROOT / 'app', env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
        text = log_path.read_text('utf8')
        if result.returncode:
            print(text[-6000:])
            raise SystemExit(f'{name} tests failed. See .local/verification/{log_path.name}')
        pattern = r'Ran (\d+) tests' if name == 'python' else r'(?:#|ℹ) tests (\d+)'
        match = re.search(pattern, text)
        results[name] = {'passed': True, 'tests': int(match.group(1)) if match else None}
        print(name, results[name])
    if args.http:
        subprocess.run([sys.executable, '-X', 'utf8', 'run.py', '--offline', '--check',
                        '--data-dir', str(ROOT / '.local/http-check')], cwd=ROOT, env=env, check=True)
        results['http'] = json.loads((output / 'http.json').read_text('utf8'))
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf8')
    print('Verification passed', results)


if __name__ == '__main__':
    main()
