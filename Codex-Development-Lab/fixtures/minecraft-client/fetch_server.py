"""Fetch the pinned official vanilla generator asset at image build time."""
import hashlib
from pathlib import Path
import urllib.request

URL='https://piston-data.mojang.com/v1/objects/64bb6d763bed0a9f1d632ec347938594144943ed/server.jar'
SHA256='f83b8e093865806f931c7e34aae41b177d4c076335263dd124c75d6d65dd1726'
target=Path(__file__).parent/'runtime/server-1.21.11.jar'
target.parent.mkdir(parents=True,exist_ok=True)
if not target.exists():
    temporary=target.with_suffix('.download')
    try:
        with urllib.request.urlopen(URL,timeout=120) as source,temporary.open('wb') as output:
            total=0
            while block:=source.read(1024*1024):
                total+=len(block)
                if total>64*1024*1024:raise ValueError('Unexpected generator asset size')
                output.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=SHA256:
            raise ValueError('Generator asset SHA-256 mismatch')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
if hashlib.sha256(target.read_bytes()).hexdigest()!=SHA256:
    raise ValueError('Generator asset SHA-256 mismatch')
print('Pinned Minecraft 1.21.11 world generator verified')
