"""Build the client-only setup helper against one pinned Fabric Loader API.

No Minecraft/project sources or Gradle tasks run here. An existing verified
Loader JAR permits a fully offline image build. A fresh checkout can retrieve
that same pinned official artifact before compilation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT.parent / 'runtime'
LOADER_SHA256 = '1b91025ac6b5253a49aabb22f658b5eed9fbba6dc88eb0e4a9364c7b2d1d1865'
LOADER_URL = 'https://maven.fabricmc.net/net/fabricmc/fabric-loader/0.19.2/fabric-loader-0.19.2.jar'


def build(loader: Path, output: Path, *, offline=False):
    loader, output = Path(loader), Path(output)
    if not loader.exists():
        if offline:
            raise ValueError('Pinned Fabric Loader API JAR is required for an offline helper build')
        loader.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(LOADER_URL, timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != LOADER_SHA256:
            raise ValueError('Fabric Loader download does not match the pinned SHA-256')
        loader.write_bytes(data)
    if loader.is_symlink() or not loader.is_file() or hashlib.sha256(loader.read_bytes()).hexdigest() != LOADER_SHA256:
        raise ValueError('Fabric Loader compile API is missing or changed')
    java_home = os.environ.get('JAVA_HOME')
    javac = str(Path(java_home) / 'bin' / ('javac.exe' if os.name == 'nt' else 'javac')) if java_home else shutil.which('javac')
    if not javac:
        raise ValueError('A JDK with javac is required to build the dependency helper')
    source = sorted((ROOT / 'src').rglob('*.java'))
    with tempfile.TemporaryDirectory(prefix='lab-client-setup-build-') as temporary:
        classes = Path(temporary) / 'classes'
        classes.mkdir()
        subprocess.run([javac, '--release', '21', '-encoding', 'UTF-8',
                        '-classpath', str(loader), '-d', str(classes), *map(str, source)], check=True)
        metadata = {'schemaVersion': 1, 'id': 'codex_lab_client_setup', 'version': '1.0.0',
                    'name': 'Codex Lab client dependency setup', 'environment': 'client',
                    'entrypoints': {'client': ['dev.codexlab.fixture.ClientDependencyBootstrap']},
                    'depends': {'fabricloader': '>=0.16.9', 'minecraft': '=1.21.11',
                                'fabric-lifecycle-events-v1': '*'},
                    'description': 'Test-process setup only. No product assertions.'}
        entries = {'fabric.mod.json': json.dumps(metadata, indent=2).encode('utf-8')}
        for item in classes.rglob('*.class'):
            entries[item.relative_to(classes).as_posix()] = item.read_bytes()
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
    result = {'output': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
              'bytes': output.stat().st_size, 'loader_sha256': LOADER_SHA256}
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loader', type=Path, default=RUNTIME / 'fabric-loader-0.19.2.jar')
    parser.add_argument('--output', type=Path, default=RUNTIME / 'dependency-bootstrap.jar')
    parser.add_argument('--offline', action='store_true')
    arguments = parser.parse_args()
    build(arguments.loader, arguments.output, offline=arguments.offline)
