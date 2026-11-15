"""Run only this copy's foreground processes. Ctrl+C stops owned children."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def wait_ready(process, url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('子进程启动失败，请查看当前终端输出')
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError('启动超时，请检查当前终端输出')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='暂停后台信源轮询')
    parser.add_argument('--bridge-only', action='store_true', help='仅启动8182数据桥接')
    parser.add_argument('--check', action='store_true', help='启动后运行HTTP检查并退出')
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.local')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('本展示版本使用Windows DPAPI，请在Windows运行')
    executable = ROOT / 'bin' / 'glance.exe'
    if not args.bridge_only and not executable.is_file():
        parser.error('请先运行 python tools/install_glance.py')
    # Reserve/check before starting any workers. Never attach to an existing app.
    ports = [8182] if args.bridge_only else [8180, 8182]
    reserved = []
    try:
        for port in ports:
            listener = socket.socket()
            reserved.append(listener)
            listener.bind(('127.0.0.1', port))
    except OSError as error:
        parser.error(f'端口已占用，请先关闭本展示实例或选择稍后运行。{error}')
    finally:
        for listener in reserved:
            listener.close()
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in ('workbuddy-mcp', 'workbuddy-preferences'):
        target = data_dir / (name + '.json')
        if not target.exists():
            shutil.copyfile(ROOT / 'config' / (name + '.example.json'), target)
    env = os.environ.copy()
    env['WORKBENCH_DATA_DIR'] = str(data_dir)
    env['WORKBENCH_OFFLINE'] = '1' if args.offline else '0'
    env['PYTHONUTF8'] = '1'
    env['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(name, None)
    children = []
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        bridge = subprocess.Popen([sys.executable, '-X', 'utf8', 'bridge.py'],
                                  cwd=ROOT / 'app', env=env, creationflags=creation_flags)
        children.append(bridge)
        wait_ready(bridge, 'http://127.0.0.1:8182/health')
        if not args.bridge_only:
            config = ROOT / 'app' / 'config' / 'glance.yml'
            command = [str(executable), '--config', str(config)]
            glance = subprocess.Popen(command, cwd=ROOT / 'app', env=env,
                                      creationflags=creation_flags)
            children.append(glance)
            wait_ready(glance, 'http://127.0.0.1:8180/api/healthz')
        print('数据桥接已就绪 http://127.0.0.1:8182/health', flush=True)
        if not args.bridge_only:
            print('打开 http://127.0.0.1:8180/  按Ctrl+C停止本次启动的进程', flush=True)
        if args.check:
            if args.bridge_only:
                return
            from tools.check_http import check_http
            check_http()
            return
        while all(child.poll() is None for child in children):
            time.sleep(0.4)
        raise RuntimeError('一个服务已退出，停止本次启动的其余服务')
    except KeyboardInterrupt:
        return
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
