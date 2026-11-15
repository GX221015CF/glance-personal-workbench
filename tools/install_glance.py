"""Install the upstream v0.8.7 Windows build after SHA-256 verification."""
import hashlib
import io
from pathlib import Path
import urllib.request
import zipfile

URL = 'https://github.com/glanceapp/glance/releases/download/v0.8.7/glance-windows-amd64.zip'
SHA256 = '29833378792c9dd2b1d4609c85d6124bee0178b2795d1f997d5079e11ae5d321'
TARGET = Path(__file__).resolve().parent.parent / 'bin' / 'glance.exe'


def main():
    if TARGET.exists():
        raise SystemExit('bin/glance.exe已存在，请核对来源后使用')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(URL, timeout=60) as response:
        archive = response.read(50 * 1024 * 1024 + 1)
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise SystemExit('发行包SHA-256与本项目记录不符，未安装')
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        matches = [name for name in package.namelist() if name.rsplit('/', 1)[-1] == 'glance.exe']
        if len(matches) != 1:
            raise SystemExit('发行包未包含唯一glance.exe')
        binary = package.read(matches[0])
    TARGET.parent.mkdir(exist_ok=True)
    with TARGET.open('xb') as output:
        output.write(binary)
    print('已核验并安装Glance v0.8.7 Windows x64')


if __name__ == '__main__':
    main()
