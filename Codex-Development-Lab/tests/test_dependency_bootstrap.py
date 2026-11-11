"""Exercise optional dependency setup through real Gradle client launch tasks.

The launcher below records arguments and properties. It is not Minecraft and
these tests never establish dependency readiness or product acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid
import zipfile


GRADLE = shutil.which('gradle')
ADAPTER = Path(__file__).resolve().parents[1] / 'fixtures/minecraft-client/quickplay.init.gradle'
SUPPORTED = '1.21.11-0.95.61'
ADAPTER_ID = 'altoclef-title-' + SUPPORTED


@unittest.skipUnless(GRADLE and shutil.which('javac'), 'Gradle and a JDK are required')
class DependencyBootstrapLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='lab-dependency-launch-')
        cls.root = Path(cls.temporary.name)
        cls.project = cls.root / 'project'
        cls.project.mkdir()
        cls.private = cls.root / 'private-game'
        world = cls.private / 'saves/lab-fixture'
        world.mkdir(parents=True)
        (world / 'level.dat').write_bytes(b'launch-contract-only; not a Minecraft world')
        cls.helper = cls.root / 'dependency-bootstrap.jar'

        def jar(path, metadata=None, *, raw=None):
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(path, 'w') as archive:
                if raw is not None:
                    archive.writestr('fabric.mod.json', raw)
                elif metadata is not None:
                    archive.writestr('fabric.mod.json', json.dumps(metadata))
                else:
                    archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')

        jar(cls.helper, {'schemaVersion': 1, 'id': 'lab_dependency_bootstrap', 'version': '1.0.0'})
        dependencies = cls.project / 'dependency-inputs'
        jar(dependencies / 'required.jar', {'schemaVersion': 1, 'id': 'altoclef', 'version': SUPPORTED})
        jar(dependencies / 'also-required.jar', {'schemaVersion': 1, 'id': 'altoclef', 'version': SUPPORTED})
        jar(dependencies / 'wrong-version.jar', {'schemaVersion': 1, 'id': 'altoclef', 'version': '1.21.11-0.95.60'})
        jar(dependencies / 'other.jar', {'schemaVersion': 1, 'id': 'other_mod', 'version': SUPPORTED})
        jar(dependencies / 'no-metadata.jar')
        jar(dependencies / 'malformed.jar', raw='{"schemaVersion": 1, "id": "altoclef",')
        # Its presence in a conventional directory must not establish a runtime dependency.
        jar(cls.project / 'libs/unionclef-unused.jar', {'schemaVersion': 1, 'id': 'altoclef', 'version': SUPPORTED})

        (cls.project / 'settings.gradle').write_text("rootProject.name = 'dependency-launch-test'\n", encoding='utf-8')
        source = cls.project / 'src/main/java/net/minecraft/client/main/Main.java'
        source.parent.mkdir(parents=True)
        source.write_text(r'''package net.minecraft.client.main;
import java.nio.file.*;
public class Main {
  static String quote(String value) {
    return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
  }
  public static void main(String[] args) throws Exception {
    StringBuilder json = new StringBuilder("{\"args\":[");
    for (int i = 0; i < args.length; i++) {
      if (i > 0) json.append(",");
      json.append(quote(args[i]));
    }
    json.append("],\"properties\":{");
    String[] names = {"fabric.addMods", "lab.client.setup.world", "lab.client.setup.adapter", "lab.client.setup.output"};
    for (int i = 0; i < names.length; i++) {
      if (i > 0) json.append(",");
      json.append(quote(names[i])).append(":").append(quote(System.getProperty(names[i], "")));
    }
    json.append("},\"cwd\":").append(quote(Path.of("").toAbsolutePath().toString())).append("}");
    Files.writeString(Path.of(System.getProperty("fixture.output")), json.toString());
  }
}
''', encoding='utf-8')
        (cls.project / 'build.gradle').write_text('''plugins { id 'java' }
def outputRoot = providers.environmentVariable('FIXTURE_TEST_OUTPUT').get()
def inputs = [
  plainClient: [], supportedClient: ['required.jar'], wrongVersionClient: ['wrong-version.jar'],
  otherClient: ['other.jar'], noMetadataClient: ['no-metadata.jar'],
  duplicateClient: ['required.jar', 'also-required.jar'], malformedClient: ['malformed.jar'],
  providerClient: ['required.jar'], jvmModsProviderClient: ['required.jar'], repeatedPathClient: ['required.jar'],
  mixedModsProviderClient: ['required.jar'], additionalModsClient: [], managedClient: ['required.jar'],
  seedIdentityClient: [], reloadIdentityClient: [],
  managedProviderClient: ['required.jar'], serverTask: ['required.jar'], unitTask: ['required.jar']
]
inputs.each { name, jars ->
  tasks.register(name, JavaExec) {
    dependsOn classes
    classpath = sourceSets.main.runtimeClasspath + files(jars.collect { 'dependency-inputs/' + it })
    mainClass = 'net.minecraft.client.main.Main'
    systemProperty 'fixture.output', new File(outputRoot, name + '.json').absolutePath
    systemProperty 'fabric.addMods', '/already/provided.jar'
    args '--width', '640', '--gameDir=old-run', '--quickPlaySingleplayer=old-world',
         '--quickPlayMultiplayer', 'old.example:25565', '--quickPlayRealms=123',
         '--quickPlayPath', 'old-quickplay.json', '--height', '360',
         '--username', name, '--uuid=00000000-0000-0000-0000-000000000099'
    if (name == 'reloadIdentityClient') {
      argumentProviders.add({ -> ['--username=DifferentPlayer', '--uuid', '00000000-0000-0000-0000-000000000098'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'managedClient') systemProperty 'fabric.client.gametest', 'false'
    if (name in ['providerClient', 'managedProviderClient']) {
      mainClass = 'net.fabricmc.devlaunchinjector.Main'
      jvmArguments.add('-Dfabric.dli.env=client')
      argumentProviders.add({ -> ['--quickPlaySingleplayer', 'provider-world', '--demo'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'managedProviderClient') {
      jvmArgumentProviders.add({ -> ['-Dfabric.client.gametest=false'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'jvmModsProviderClient') {
      systemProperties.remove('fabric.addMods')
      jvmArgumentProviders.add({ -> ['-Dfabric.addMods=/from/provider.jar'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'mixedModsProviderClient') {
      systemProperty 'fabric.addMods', '/from/system-property.jar'
      jvmArgumentProviders.add({ -> ['-Dfabric.addMods=/from/first-provider.jar', '-Dfabric.addMods=/from/last-provider.jar'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'additionalModsClient') {
      systemProperty 'fabric.addMods', file('dependency-inputs/required.jar').absolutePath
    }
    if (name == 'repeatedPathClient') {
      systemProperty 'fabric.addMods', new File(project.projectDir, 'dependency-inputs/../dependency-inputs/required.jar').absolutePath
    }
    if (name == 'serverTask') mainClass = 'example.Server'
    if (name == 'unitTask') mainClass = 'example.Unit'
  }
}
''', encoding='utf-8')
        for kind in ('Server', 'Unit'):
            p = cls.project / ('src/main/java/example/' + kind + '.java')
            p.parent.mkdir(exist_ok=True)
            p.write_text('package example; public class ' + kind + ' { public static void main(String[] args) throws Exception { net.minecraft.client.main.Main.main(args); } }', encoding='utf-8')
        injector = cls.project / 'src/main/java/net/fabricmc/devlaunchinjector/Main.java'
        injector.parent.mkdir(parents=True)
        injector.write_text('package net.fabricmc.devlaunchinjector; public class Main { public static void main(String[] args) throws Exception { net.minecraft.client.main.Main.main(args); } }', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def launch(self, tasks, **overrides):
        output = self.root / ('output-' + self.id().rsplit('.', 1)[-1])
        output.mkdir(exist_ok=True)
        env = dict(os.environ, LAB_WORLD_FIXTURE_MODE='quickplay',
                   LAB_WORLD_FIXTURE_NAME='lab-fixture', LAB_WORLD_FIXTURE_GAME_DIR=str(self.private),
                   LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR=str(self.helper),
                   FIXTURE_TEST_OUTPUT=str(output), GRADLE_USER_HOME=str(self.root / 'gradle-home'))
        env.update(overrides)
        result = subprocess.run([GRADLE, '--offline', '--no-daemon', '--console=plain',
                                 '--init-script', str(ADAPTER), *tasks], cwd=self.project,
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        return result, output

    def read_launch(self, output, name):
        return json.loads((output / (name + '.json')).read_text())

    def assert_identity(self, launch):
        args = launch['args']
        self.assertEqual(args.count('--username'), 1)
        self.assertEqual(args.count('--uuid'), 1)
        self.assertFalse(any(a.startswith(('--username=', '--uuid=')) for a in args))
        name = args[args.index('--username') + 1]
        actor = args[args.index('--uuid') + 1]
        self.assertRegex(name, r'^Lab[0-9a-f]{13}$')
        # Java's UUID.nameUUIDFromBytes uses MD5 with UUID v3 bits. The offline
        # server derives actor identity from the selected username in this form.
        expected = uuid.UUID(bytes=hashlib.md5(('OfflinePlayer:' + name).encode()).digest(), version=3)
        self.assertEqual(actor, str(expected))
        return name, actor

    def assert_native(self, launch):
        self.assert_identity(launch)
        self.assertEqual(launch['args'][-4:], ['--gameDir', str(self.private),
                                             '--quickPlaySingleplayer', 'lab-fixture'])
        self.assertEqual(launch['properties']['fabric.addMods'], '/already/provided.jar')
        self.assertEqual(launch['properties']['lab.client.setup.world'], '')
        self.assertEqual(launch['properties']['lab.client.setup.adapter'], '')
        self.assertEqual(launch['properties']['lab.client.setup.output'], '')

    def assert_title_first(self, launch, *, lazy=False, existing_mods='/already/provided.jar'):
        expected = ['--width', '640', '--height', '360']
        if lazy:
            expected.append('--demo')
        name, actor = self.assert_identity(launch)
        expected += ['--username', name, '--uuid', actor]
        expected += ['--gameDir', str(self.private)]
        self.assertEqual(launch['args'], expected)
        self.assertFalse(any(arg.startswith('--quickPlay') for arg in launch['args']))
        self.assertEqual(Path(launch['cwd']), self.private)
        properties = launch['properties']
        self.assertEqual(properties['lab.client.setup.world'], 'lab-fixture')
        self.assertEqual(properties['lab.client.setup.adapter'], ADAPTER_ID)
        self.assertEqual(Path(properties['lab.client.setup.output']),
                         self.private / 'client-dependency-preparation.json')
        self.assertIn(existing_mods, properties['fabric.addMods'])
        self.assertIn(str(self.helper), properties['fabric.addMods'])
        self.assertEqual(properties['fabric.addMods'].count(str(self.helper)), 1)

    def test_actual_supported_runtime_dependency_uses_title_first(self):
        result, output = self.launch(['supportedClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_title_first(self.read_launch(output, 'supportedClient'))
        self.assertFalse((self.private / 'client-dependency-preparation.json').exists(),
                         'Launcher configuration must not fabricate a runtime readiness receipt')

    def test_missing_unrelated_wrong_version_and_unused_dependencies_keep_native_launch(self):
        names = ['plainClient', 'otherClient', 'wrongVersionClient', 'noMetadataClient']
        result, output = self.launch(names)
        self.assertEqual(result.returncode, 0, result.stdout)
        for name in names:
            with self.subTest(name=name):
                self.assert_native(self.read_launch(output, name))

    def test_unneeded_helper_is_not_required_or_read(self):
        result, output = self.launch(['plainClient'], LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR=str(self.root / 'missing-helper.jar'))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_native(self.read_launch(output, 'plainClient'))

    def test_lazy_client_arguments_preserve_unrelated_options_and_remove_quickplay(self):
        result, output = self.launch(['providerClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_title_first(self.read_launch(output, 'providerClient'), lazy=True)

    def test_seed_reload_tasks_and_a_new_gradle_process_use_the_same_offline_player(self):
        result, output = self.launch(['seedIdentityClient', 'reloadIdentityClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        seed = self.assert_identity(self.read_launch(output, 'seedIdentityClient'))
        reload = self.assert_identity(self.read_launch(output, 'reloadIdentityClient'))
        self.assertEqual(seed, reload)
        again, output = self.launch(['reloadIdentityClient'])
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertEqual(seed, self.assert_identity(self.read_launch(output, 'reloadIdentityClient')))
        receipts = [json.loads(line.split('LAB_WORLD_FIXTURE_LAUNCH:', 1)[1])
                    for line in result.stdout.splitlines() if 'LAB_WORLD_FIXTURE_LAUNCH:' in line]
        self.assertEqual(len(receipts), 2)
        self.assertEqual({(x['player']['username'], x['player']['uuid']) for x in receipts}, {seed})
        self.assertEqual({x['player']['kind'] for x in receipts}, {'offline_fixture'})

    def test_an_independent_private_world_gets_its_own_player_identity(self):
        first, output = self.launch(['seedIdentityClient'])
        self.assertEqual(first.returncode, 0, first.stdout)
        identity = self.assert_identity(self.read_launch(output, 'seedIdentityClient'))
        other = self.root / 'another-private-game'
        world = other / 'saves/lab-fixture'
        world.mkdir(parents=True)
        (world / 'level.dat').write_bytes(b'launch-contract-only; not a Minecraft world')
        second, output = self.launch(['seedIdentityClient'], LAB_WORLD_FIXTURE_GAME_DIR=str(other))
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertNotEqual(identity, self.assert_identity(self.read_launch(output, 'seedIdentityClient')))

    def test_lazy_jvm_mod_provider_is_preserved_when_bootstrap_is_appended(self):
        result, output = self.launch(['jvmModsProviderClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_title_first(self.read_launch(output, 'jvmModsProviderClient'),
                                existing_mods='/from/provider.jar')

    def test_last_jvm_override_is_preserved_instead_of_shadowed_system_property(self):
        result, output = self.launch(['mixedModsProviderClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        launch = self.read_launch(output, 'mixedModsProviderClient')
        self.assert_title_first(launch, existing_mods='/from/last-provider.jar')
        self.assertNotIn('/from/system-property.jar', launch['properties']['fabric.addMods'])
        self.assertNotIn('/from/first-provider.jar', launch['properties']['fabric.addMods'])

    def test_dependency_loaded_only_through_explicit_additional_mods_gets_setup(self):
        result, output = self.launch(['additionalModsClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assert_title_first(self.read_launch(output, 'additionalModsClient'),
                                existing_mods=str(self.project / 'dependency-inputs/required.jar'))

    def test_one_artifact_referenced_by_classpath_and_addmods_is_not_a_duplicate(self):
        result,output=self.launch(['repeatedPathClient'])
        self.assertEqual(result.returncode,0,result.stdout)
        self.assert_title_first(self.read_launch(output,'repeatedPathClient'),
            existing_mods=str(self.project/'dependency-inputs/../dependency-inputs/required.jar'))

    def test_managed_game_tests_server_and_unit_tasks_do_not_inject_dependency_setup(self):
        names = ['managedClient', 'managedProviderClient', 'serverTask', 'unitTask']
        result, output = self.launch(names)
        self.assertEqual(result.returncode, 0, result.stdout)
        for name in names:
            with self.subTest(name=name):
                launch = self.read_launch(output, name)
                self.assertIn('--gameDir=old-run', launch['args'])
                self.assertIn('old.example:25565', launch['args'])
                self.assertIn(name, launch['args'])
                self.assertIn('--uuid=00000000-0000-0000-0000-000000000099', launch['args'])
                self.assertEqual(Path(launch['cwd']), self.project)
                self.assertEqual(launch['properties']['fabric.addMods'], '/already/provided.jar')
                self.assertEqual(launch['properties']['lab.client.setup.adapter'], '')

    def test_explicit_managed_mode_keeps_project_lifecycle(self):
        result, output = self.launch(['supportedClient'], LAB_WORLD_FIXTURE_MODE='managed')
        self.assertEqual(result.returncode, 0, result.stdout)
        launch = self.read_launch(output, 'supportedClient')
        self.assertIn('--gameDir=old-run', launch['args'])
        self.assertEqual(launch['properties']['lab.client.setup.adapter'], '')
        self.assertEqual(launch['properties']['fabric.addMods'], '/already/provided.jar')

    def test_duplicate_actual_dependency_is_diagnosed_before_client_starts(self):
        result, output = self.launch(['duplicateClient'])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('duplicate', result.stdout.lower())
        self.assertIn('altoclef', result.stdout.lower())
        self.assertFalse((output / 'duplicateClient.json').exists())

    def test_malformed_runtime_metadata_cannot_invent_a_dependency_adapter(self):
        result, output = self.launch(['malformedClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('LAB_CLIENT_DEPENDENCY_METADATA', result.stdout)
        self.assert_native(self.read_launch(output, 'malformedClient'))

    def test_required_missing_helper_has_a_concrete_prelaunch_error(self):
        result, output = self.launch(['supportedClient'], LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR=str(self.root / 'missing-helper.jar'))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('bootstrap', result.stdout.lower())
        self.assertFalse((output / 'supportedClient.json').exists())

    @unittest.skipUnless(hasattr(os, 'symlink'), 'Symlinks unavailable')
    def test_required_helper_symlink_is_rejected(self):
        link = self.root / 'linked-helper.jar'
        try:
            link.symlink_to(self.helper)
        except OSError as exc:
            self.skipTest(str(exc))
        try:
            result, output = self.launch(['supportedClient'], LAB_CLIENT_DEPENDENCY_BOOTSTRAP_JAR=str(link))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('symlink', result.stdout.lower())
            self.assertFalse((output / 'supportedClient.json').exists())
        finally:
            link.unlink()


if __name__ == '__main__':
    unittest.main()
