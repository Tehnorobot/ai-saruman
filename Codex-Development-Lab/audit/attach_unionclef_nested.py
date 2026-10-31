"""Make supplied nested mod dependencies explicit for Loom's dev classpath."""
import hashlib,json
from pathlib import Path
import zipfile
from urllib.request import urlopen
from common.project_lock import project_lock
root=Path(__file__).resolve().parents[1];jid='5babdfba708646'
with urlopen('http://localhost:8080/api/jobs/'+jid,timeout=15) as response:job=json.load(response)
assert job['status']=='USER_PAUSED'
project=root/'projects'/job['project_id'];ws=project/'workspace'
entries=('META-INF/jars/tungsten-BETA-1.0.0.jar','META-INF/jars/nether-pathfinder-1.5.jar')
with project_lock(project):
    manifest=json.loads((ws/'compatibility-inputs.json').read_text(encoding='utf-8'))
    outer=ws/manifest['mods'][0]['path']
    assert hashlib.sha256(outer.read_bytes()).hexdigest()==manifest['mods'][0]['sha256']
    gradle=ws/'build.gradle';text=gradle.read_text(encoding='utf-8')
    with zipfile.ZipFile(outer) as archive:
        declared={item['file'] for item in json.loads(archive.read('fabric.mod.json'))['jars']}
        for entry in entries:
            assert entry in declared
            data=archive.read(entry);assert len(data)<8*1024*1024
            target=ws/'libs'/'unionclef-dependencies'/Path(entry).name
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():assert target.read_bytes()==data
            else:target.write_bytes(data)
            relative=target.relative_to(ws).as_posix()
            declaration="    modImplementation files('"+relative+"')"
            if declaration not in text:text=text.replace('dependencies {','dependencies {\n'+declaration,1)
            row={'path':relative,'sha256':hashlib.sha256(data).hexdigest(),
                'source_jar':manifest['mods'][0]['path'],'source_entry':entry,
                'purpose':'Nested runtime dependency made explicit for Loom dev client.'}
            manifest.setdefault('nested_dependencies',[])
            if not any(r['path']==relative for r in manifest['nested_dependencies']):manifest['nested_dependencies'].append(row)
    gradle.write_text(text,encoding='utf-8')
    for path in (ws/'compatibility-inputs.json',project/'compatibility-inputs.json'):
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    notes=ws/'COMPATIBILITY_INPUTS.md'
    extra='\nLoom dev runs require the supplied nested Tungsten and nether-pathfinder JARs explicitly on the mod classpath. They were extracted byte-for-byte from the supplied UnionClef JAR; see nested_dependencies in the manifest. Keep these modImplementation entries. Fabric Loader already supplies MixinExtras; its older nested copy is not forced into the dev classpath.\n'
    current=notes.read_text(encoding='utf-8')
    if extra not in current:notes.write_text(current+extra,encoding='utf-8')
print(json.dumps(manifest['nested_dependencies']))
