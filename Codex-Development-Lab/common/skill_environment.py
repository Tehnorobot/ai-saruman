"""Install bundled skills and migrate the legacy Fabric identifier safely."""
from pathlib import Path
import re
import shutil
import uuid


def _rename_legacy_manifest(path):
    if path.is_symlink():
        raise ValueError('Skill manifest must not be a symlink')
    if not path.is_file():
        return
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    header = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.DOTALL)
    if not header:
        return
    legacy = re.escape('fabric-minecraft-1.21.11')
    pattern = (r'(?m)^name:[ \t]*(?:' + legacy + r'|"' + legacy
               + r'"|\'' + legacy + r'\')(?P<suffix>[ \t]*(?:#[^\r\n]*)?)(?P<cr>\r?)$')
    fixed = re.sub(pattern, r'name: fabric-minecraft-12111\g<suffix>\g<cr>', header[1])
    if fixed != header[1]:
        updated = text[:header.start(1)] + fixed + text[header.end(1):]
        path.write_bytes((b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')
                         + updated.encode('utf-8'))


def install_skills(ws, templates):
    ws, templates = Path(ws), Path(templates)
    if ws.is_symlink() or not ws.is_dir():
        raise ValueError('Workspace must be a real existing directory')
    root = ws/'.agents'/'skills'
    if not root.resolve().is_relative_to(ws.resolve()):
        raise ValueError('Skills directory escapes workspace')
    root.mkdir(parents=True, exist_ok=True)
    legacy = root/'fabric-minecraft-1.21.11'
    current = root/'fabric-minecraft-12111'
    for directory in (legacy, current):
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError('Skill path escapes project skills')
        if (directory/'SKILL.md').is_symlink():
            raise ValueError('Skill manifest must not be a symlink')
    if legacy.exists():
        if not current.exists():
            legacy.replace(current)
        else:
            # Keep both customizations, but discover only the current skill.
            archive = ws/'.lab-legacy-skills'
            if archive.is_symlink() or not archive.resolve().is_relative_to(ws.resolve()):
                raise ValueError('Legacy skill archive escapes workspace')
            archive.mkdir(exist_ok=True)
            target = archive/legacy.name
            if target.exists():
                target = archive/(legacy.name+'-'+uuid.uuid4().hex[:8])
            legacy.replace(target)
    _rename_legacy_manifest(current/'SKILL.md')
    for source in templates.iterdir():
        if not source.is_dir() or not (source/'SKILL.md').is_file():
            continue
        target = root/source.name
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('Installed skill path escapes project skills')
        if not target.exists():
            shutil.copytree(source, target)
