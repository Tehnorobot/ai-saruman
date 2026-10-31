"""Independent admission audit and offline Java cold/warm runner benchmark.

This script never starts a model, reads authentication, or accepts game EULA.
The runtime mode requires an explicitly supplied scratch /projects and /output;
use it in a disposable client container connected only to a scratch runner.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time
import uuid
import zipfile


def audit_compose(config, lab_root):
    """Check sanitized Compose JSON (services.volumes/networks only are needed)."""
    lab = Path(lab_root).resolve(strict=True)
    findings = []
    for name, definition in config.get('volumes', {}).items():
        definition = definition or {}
        if definition.get('external') or definition.get('driver_opts'):
            findings.append('Named volume has external/driver options: ' + name)
    for name, service in config.get('services', {}).items():
        if service.get('privileged') or service.get('pid') == 'host' or service.get('network_mode') == 'host':
            findings.append('Host namespace/privilege is exposed: ' + name)
        for mount in service.get('volumes') or []:
            if not isinstance(mount, dict):
                findings.append('Compose mount was not normalized: ' + name)
                continue
            if mount.get('type') != 'bind':
                continue
            source = mount.get('source', '')
            target = mount.get('target', '')
            if source == '/var/run/docker.sock' and target == '/var/run/docker.sock' and name == 'runner':
                continue
            try:
                path = Path(source)
                if not path.is_absolute():
                    raise ValueError('Unresolved mount path')
                actual = path.resolve()
                if not actual.is_relative_to(lab):
                    raise ValueError('Mount escapes laboratory')
            except (OSError, ValueError):
                findings.append('Host bind is outside the canonical laboratory: ' + name + ':' + target)
            if 'docker.sock' in source or 'docker.sock' in target:
                findings.append('Engine socket is exposed outside runner: ' + name)
        if name == 'agent':
            network_names = service.get('networks', {})
            if not network_names or service.get('network_mode'):
                findings.append('Agent has no isolated internal network')
            for network_name in network_names:
                network = config.get('networks', {}).get(network_name, {})
                options = network.get('driver_opts', {})
                if not network.get('internal') or options.get('com.docker.network.bridge.gateway_mode_ipv4') != 'isolated':
                    findings.append('Agent network has an external or host gateway: ' + network_name)
    return {'status': 'FAIL' if findings else 'PASS', 'findings': findings}


def benchmark(output):
    from worker.sandbox import DockerSandbox
    from common.safe_archive import pack_tree
    projects = Path(os.environ.get('LAB_PROJECTS', '/projects')).resolve()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    project, job = uuid.uuid4().hex[:14], uuid.uuid4().hex[:14]
    workspace = projects / project / 'workspace'
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / 'src/main/java/demo').mkdir(parents=True)
    (workspace / 'src/smoke/java/demo').mkdir(parents=True)
    (workspace / 'settings.gradle').write_text("rootProject.name='offline-runner-benchmark'\n")
    (workspace / 'build.gradle').write_text('''plugins { id 'java' }
java { toolchain.languageVersion = JavaLanguageVersion.of(21) }
sourceSets {
    smoke {
        java.srcDir 'src/smoke/java'
        compileClasspath += sourceSets.main.output
        runtimeClasspath += sourceSets.main.output
    }
}
tasks.register('smoke', JavaExec) {
    dependsOn smokeClasses
    classpath = sourceSets.smoke.runtimeClasspath
    mainClass = 'demo.Check'
}
tasks.named('check') { dependsOn tasks.named('smoke') }
''')
    (workspace / 'gradlew').write_text('#!/bin/sh\nexec /opt/gradle/bin/gradle --offline "$@"\n')
    (workspace / 'gradlew').chmod(0o755)
    production = workspace / 'src/main/java/demo/Calculator.java'
    production.write_text('package demo; public class Calculator { public static int sum(int a,int b){return a+b;} }\n')
    (workspace / 'src/smoke/java/demo/Check.java').write_text('''package demo;
import java.nio.file.*;
import java.io.IOException;
public class Check { public static void main(String[] args) throws Exception {
    if (Calculator.sum(2,3) != 5 || Calculator.sum(-8,3) != -5) throw new AssertionError("real Java calculation");
    String status = Files.readString(Path.of("/proc/self/status"));
    String uidLine = status.lines().filter(s -> s.startsWith("Uid:")).findFirst().orElseThrow();
    java.util.Scanner ids = new java.util.Scanner(uidLine.substring(4));
    for (int i=0; i<4; i++) if (ids.nextInt()!=1000) throw new AssertionError("Project command retained root identity");
    String caps = status.lines().filter(s -> s.startsWith("CapEff:")).findFirst().orElseThrow().substring(7).trim();
    if (!caps.equals("0000000000000000")) throw new AssertionError("Project command retained capabilities");
    try {
        Files.writeString(Path.of("/output/verification.json"), "forged successful verdict");
        throw new AssertionError("Untrusted project could overwrite trusted evidence");
    } catch (IOException expected) { }
    for (String hidden : new String[]{"/state", "/agent-tasks", "/var/run/docker.sock", "/run/lab-runner/runner.sock"})
        if (Files.exists(Path.of(hidden))) throw new AssertionError("Service path visible: " + hidden);
    // The base image contains an empty /projects directory. It must not expose
    // any host project trees or permit writes to that read-only image folder.
    if (Files.isDirectory(Path.of("/projects"))) {
        try (var entries = Files.list(Path.of("/projects"))) {
            if (entries.findAny().isPresent()) throw new AssertionError("Other project trees are visible");
        }
        try {
            Files.writeString(Path.of("/projects/outside-workspace.txt"), "forbidden");
            throw new AssertionError("Project could write outside its workspace/cache");
        } catch (IOException expected) { }
    }
    Path marker = Path.of("/cache/independent-benchmark.marker");
    System.out.println(Files.exists(marker) ? "PRIVATE_CACHE_WARM" : "PRIVATE_CACHE_COLD");
    Files.writeString(marker, "persistent private cache");
    System.out.println("INDEPENDENT_JAVA_SMOKE_OK");
} }
''')
    # A dependency-shaped source fixture makes archive cost visible without
    # introducing downloads, credentials, Minecraft assets or invented tests.
    for index in range(400):
        fixture = workspace / 'fixture' / f'{index:04d}.txt'
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_bytes((f'fixture-{index}\n'.encode()) * 100)
    legacy_cache = workspace / '.lab-cache' / 'synthetic-dependencies'
    legacy_cache.mkdir(parents=True)
    for index in range(256):
        (legacy_cache / f'{index:04d}.bin').write_bytes(bytes([index % 251]) * 16384)
    legacy_started = time.monotonic()
    legacy_archive = io.BytesIO()
    pack_tree(workspace, legacy_archive)
    legacy = {'archive_bytes': legacy_archive.tell(),
              'pack_seconds': round(time.monotonic() - legacy_started, 4),
              'note': 'One legacy unfiltered source+cache archive; excludes container/build time.'}
    legacy_archive.close()
    runs = []
    for label in ('cold', 'warm'):
        if label == 'warm':
            # Force source compilation again rather than timing only UP-TO-DATE.
            production.write_text(production.read_text() + '// warm compilation\n')
        stage = output / label
        stage.mkdir()
        started = time.monotonic()
        report = DockerSandbox().verify(workspace, stage, 180, job, lambda: None, eula=False)
        wall = time.monotonic() - started
        build = next((c for c in report.get('checks', []) if c.get('label') == 'gradle_build'), None)
        if not build or build['status'] != 'PASS':
            raise AssertionError('Real offline Gradle build failed: ' + json.dumps(report))
        logs = '\n'.join(path.read_text(errors='replace') for path in stage.glob('*.log'))
        if 'INDEPENDENT_JAVA_SMOKE_OK' not in logs:
            raise AssertionError('Java assertion program did not actually run')
        expected_cache = 'PRIVATE_CACHE_COLD' if label == 'cold' else 'PRIVATE_CACHE_WARM'
        if expected_cache not in logs:
            raise AssertionError('Project-private cache did not preserve the expected cold/warm state')
        uploaded = report.get('metrics', {}).get('workspace_upload_bytes')
        if not isinstance(uploaded, int) or uploaded >= legacy['archive_bytes']:
            raise AssertionError('Batch upload did not omit the synthetic old dependency cache')
        jars = list((stage / 'builds').glob('*.jar'))
        if not jars:
            raise AssertionError('No actual JAR returned to the evidence builds folder')
        with zipfile.ZipFile(jars[0]) as jar:
            if 'demo/Calculator.class' not in jar.namelist():
                raise AssertionError('Returned JAR lacks compiled production class')
        if (workspace.parent / '.lab-runner.json').exists():
            raise AssertionError('Completed benchmark left project quarantined')
        caches = [p.relative_to(workspace).as_posix() for p in workspace.rglob('*')
                  if p.is_dir() and p.name in ('.lab-cache', 'caches', 'wrapper')]
        if caches:
            raise AssertionError('Private dependency cache leaked to workspace: ' + repr(caches))
        if any((workspace / name).exists() or (workspace / name).is_symlink()
               for name in ('.lab-cache', '.gradle', 'build')):
            raise AssertionError('Private build/cache paths were exported to the source workspace')
        runs.append({'phase': label, 'wall_seconds': round(wall, 4),
                     'checks': report['checks'], 'timings': report.get('timings'),
                     'metrics': report.get('metrics'),
                     'jar_sha256': hashlib.sha256(jars[0].read_bytes()).hexdigest()})
    result = {'status': 'PASS', 'project': project, 'job': job,
              'fixture_files': 400, 'legacy_unfiltered_archive': legacy, 'runs': runs,
              'note': 'Cold/warm values describe this offline fixture only; no Minecraft performance claim.'}
    (output / 'benchmark.json').write_text(json.dumps(result, indent=2))
    return result


def network_probe(output, host_addresses):
    """Probe a disposable project container, never the controller's own network."""
    from worker.sandbox import DockerSandbox
    projects = Path(os.environ.get('LAB_PROJECTS', '/projects')).resolve()
    workspace = projects / uuid.uuid4().hex[:14] / 'workspace'
    workspace.mkdir(parents=True, exist_ok=False)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Test addresses are targets only. No account credentials or model requests.
    program = '''import http.client,json,socket,sys,subprocess
from pathlib import Path
direct = json.loads(sys.argv[1]); results=[]
for host,port in direct:
    try:
        connection=socket.create_connection((host,port),timeout=1)
    except OSError:
        results.append({'direct':host,'port':port,'blocked':True})
    else:
        connection.close()
        raise AssertionError('Untrusted job reached a direct forbidden endpoint: '+host)
for host in ('127.0.0.1','localhost','host.docker.internal','169.254.169.254','192.168.1.1','[::1]','[::ffff:127.0.0.1]'):
    connection=http.client.HTTPConnection('lab-egress',3128,timeout=10)
    try:
        connection.request('CONNECT',host+':443',headers={'Host':host+':443'})
        response=connection.getresponse()
        if response.status not in (400,403):raise AssertionError('Proxy did not explicitly reject private target '+host+': '+str(response.status))
        results.append({'proxy_target':host,'status':response.status})
    finally:connection.close()
connection=http.client.HTTPSConnection('lab-egress',3128,timeout=25)
connection.set_tunnel('repo.maven.apache.org',443)
try:
    connection.request('HEAD','/maven2/')
    response=connection.getresponse()
    if not 200<=response.status<500 or response.status==407:raise AssertionError('Public Maven HTTPS failed: '+str(response.status))
    results.append({'public_https':'repo.maven.apache.org','status':response.status,'tls':True})
finally:connection.close()
Path('TlsProbe.java').write_text("""import java.net.*;
public class TlsProbe {
  public static void main(String[] args) throws Exception {
    var c=(HttpURLConnection)new URI("https://repo.maven.apache.org/maven2/").toURL().openConnection();
    c.setRequestMethod("HEAD");c.setConnectTimeout(15000);c.setReadTimeout(15000);
    int status=c.getResponseCode();
    if(status<200 || status>=400)throw new AssertionError("HTTPS status "+status);
    System.out.println("JAVA_HTTPS_PROXY_OK");c.disconnect();
  }
}""")
java=subprocess.run(['java','TlsProbe.java'],capture_output=True,text=True,timeout=30)
if java.returncode or 'JAVA_HTTPS_PROXY_OK' not in java.stdout:
    raise AssertionError('Java HTTPS proxy configuration failed: '+java.stdout+java.stderr)
results.append({'java_https':'repo.maven.apache.org','tls':True,'proxy_properties':True})
print(json.dumps({'status':'PASS','probes':results}),flush=True)
'''
    targets = [('host.docker.internal', 8080), ('1.1.1.1', 443)] + [(host, 8080) for host in host_addresses]
    result = DockerSandbox().execute(['python3', '-c', program, json.dumps(targets)], workspace,
                                     output / 'network.log', 90, uuid.uuid4().hex[:14], lambda: None)
    if result['status'] != 'PASS':
        raise AssertionError('Network boundary probe failed: ' + json.dumps(result))
    lines = (output / 'network.log').read_text().splitlines()
    report = next((json.loads(line) for line in reversed(lines) if line.startswith('{"status": "PASS"')), None)
    if report is None:
        raise AssertionError('Network probe did not emit its result')
    (output / 'network.json').write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='mode', required=True)
    admission = subcommands.add_parser('compose')
    admission.add_argument('--json', type=Path, required=True)
    admission.add_argument('--lab-root', type=Path, required=True)
    runtime = subcommands.add_parser('benchmark')
    runtime.add_argument('--output', type=Path, required=True)
    network = subcommands.add_parser('network')
    network.add_argument('--output', type=Path, required=True)
    network.add_argument('--host-address', action='append', default=[])
    args = parser.parse_args()
    result = (audit_compose(json.loads(args.json.read_text(encoding='utf-8-sig')), args.lab_root)
              if args.mode == 'compose' else network_probe(args.output, args.host_address)
              if args.mode == 'network' else benchmark(args.output))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
