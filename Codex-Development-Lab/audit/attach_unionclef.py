"""Copy the explicitly supplied compatibility JAR into one paused project."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from urllib.request import urlopen
from common.project_lock import project_lock

root=Path(__file__).resolve().parents[1]
jid='5babdfba708646'
with urlopen('http://localhost:8080/api/jobs/'+jid,timeout=15) as response:job=json.load(response)
assert job['status']=='USER_PAUSED'
project=root/'projects'/job['project_id'];workspace=project/'workspace'
source=Path('C:/Users/aimihail/AppData/Roaming/.minecraft/mods/unionclef-1.21.11-0.95.61.jar')
with zipfile.ZipFile(source) as archive:
    metadata=json.loads(archive.read('fabric.mod.json'))
    assert metadata['id']=='altoclef' and metadata['version']=='1.21.11-0.95.61'
digest=hashlib.sha256(source.read_bytes()).hexdigest()
with project_lock(project):
    with urlopen('http://localhost:8080/api/jobs/'+jid,timeout=15) as response:assert json.load(response)['status']=='USER_PAUSED'
    destination=workspace/'libs'/source.name
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():assert hashlib.sha256(destination.read_bytes()).hexdigest()==digest
    else:shutil.copyfile(source,destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest()==digest
    gradle=workspace/'build.gradle'
    text=gradle.read_text(encoding='utf-8')
    declaration="    modImplementation files('libs/"+source.name+"')"
    if declaration not in text:
        assert text.count('dependencies {')==1
        text=text.replace('dependencies {','dependencies {\n'+declaration,1)
        gradle.write_text(text,encoding='utf-8')
    # A real client scenario must fail if the supplied compatibility mod is
    # absent. This check proves presence, not successful control handover.
    game=workspace/'src/gametest/java/dev/autodefense/gametest/AutoDefenseClientGameTest.java'
    text=game.read_text(encoding='utf-8')
    needle='        String selected = System.getProperty("plab.scenario", "");'
    check='        check(FabricLoader.getInstance().isModLoaded("altoclef"), "Supplied UnionClef must be loaded for compatibility testing");'
    if check not in text:
        assert text.count(needle)==1
        text=text.replace(needle,check+'\n'+needle,1)
        game.write_text(text,encoding='utf-8')
    inventory={'schema_version':1,'mods':[{'path':'libs/'+source.name,'id':metadata['id'],
        'version':metadata['version'],'sha256':digest,'size':destination.stat().st_size,
        'purpose':'Required UnionClef compatibility target; inspect exact installed classes and test real handover.'}]}
    (workspace/'compatibility-inputs.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    (project/'compatibility-inputs.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
    notes='''# Supplied compatibility mod

The user supplied libs/unionclef-1.21.11-0.95.61.jar, mod ID altoclef.
See compatibility-inputs.json for the version and SHA-256.
build.gradle includes it with Loom modImplementation so compilation and real
client game tests use this installed version. Do not replace it with mocks or
a downloaded version. Inspect the JAR using jar/javap; it contains
adris.altoclef.Py4jEntryPoint. Do not invent methods from old documentation.
The actual handover tests must exercise this mod's real controllers and input
release, including late/background input writes, then safe task restart.
A mod-loaded assertion proves presence only, never successful handover.
Keep these tests in acceptance-plan.json with honest assertions and videos.
The original game folder is NOT mounted; this copied JAR is the only supplied
third-party mod. Nested dependencies listed in its metadata are inside the JAR.
If a further dependency is required, report its actual missing ID rather than
copying the entire modpack. No prior Auto Defense JAR is included.
'''
    (workspace/'COMPATIBILITY_INPUTS.md').write_text(notes,encoding='utf-8')
out=root/'audit/unionclef-input';out.mkdir(exist_ok=True)
(out/'attachment.json').write_text(json.dumps({'job_id':jid,'project_id':job['project_id'],
    **inventory,'source_folder_mounted':False,'old_defense_jar_copied':False,
    'runtime_verified':False,'job_resumed':False},indent=2),encoding='utf-8')
print(json.dumps({'job_id':jid,'project_id':job['project_id'],'mod':source.name,'sha256':digest,
    'size':destination.stat().st_size,'job_resumed':False,'runtime_verified':False}))
