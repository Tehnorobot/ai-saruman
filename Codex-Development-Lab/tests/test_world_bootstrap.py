"""Exercise the trusted launch adapter with real Gradle JavaExec tasks.

The small Java launcher records arguments; it is not Minecraft and these tests
make no gameplay or world-readiness claim.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


GRADLE = shutil.which('gradle')
ADAPTER = Path(__file__).resolve().parents[1] / 'fixtures/minecraft-client/quickplay.init.gradle'


@unittest.skipUnless(GRADLE and shutil.which('javac'), 'Gradle and a JDK are required')
class QuickPlayLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='lab-world-launch-')
        cls.root = Path(cls.temporary.name)
        cls.project = cls.root / 'project'
        cls.project.mkdir()
        cls.private = cls.root / 'private-game'
        world = cls.private / 'saves/lab-fixture'
        world.mkdir(parents=True)
        # A non-empty launch-contract fixture, not a generated Minecraft world.
        (world / 'level.dat').write_bytes(b'launch-contract-only')
        (cls.project / 'settings.gradle').write_text("rootProject.name = 'fixture-launch-test'\n", encoding='utf-8')
        source = cls.project / 'src/main/java/net/minecraft/client/main/Main.java'
        source.parent.mkdir(parents=True)
        source.write_text('''package net.minecraft.client.main;
import java.nio.file.*;
import java.util.*;
public class Main {
  public static void main(String[] args) throws Exception {
    StringBuilder json = new StringBuilder("[");
    for (String arg : args) {
      if (json.length() > 1) json.append(",");
      json.append("\\\"").append(arg.replace("\\\\", "\\\\\\\\").replace("\\\"", "\\\\\\\"")).append("\\\"");
    }
    json.append("]");
    Files.writeString(Path.of(System.getProperty("fixture.output")), json.toString());
    Files.writeString(Path.of(System.getProperty("fixture.output") + ".cwd"), Path.of("").toAbsolutePath().toString());
  }
}
''', encoding='utf-8')
        (cls.project / 'build.gradle').write_text('''plugins { id 'java' }
def outputRoot = providers.environmentVariable('FIXTURE_TEST_OUTPUT').get()
['customClient', 'managedClient', 'serverTask', 'unitTask', 'providerClient', 'managedProviderClient'].each { name ->
  tasks.register(name, JavaExec) {
    dependsOn classes
    classpath = sourceSets.main.runtimeClasspath
    mainClass = 'net.minecraft.client.main.Main'
    systemProperty 'fixture.output', new File(outputRoot, name + '.json').absolutePath
    args '--width', '640', '--gameDir=old-run', '--quickPlayMultiplayer', 'old.example:25565',
         '--quickPlaySingleplayer=old-world', '--height', '360'
    if (name == 'managedClient') systemProperty 'fabric.client.gametest', 'false'
    if (name in ['providerClient', 'managedProviderClient']) {
      mainClass = 'net.fabricmc.devlaunchinjector.Main'
      jvmArguments.add('-Dfabric.dli.env=client')
      argumentProviders.add({ -> ['--gameDir', 'provider-old-run', '--quickPlayRealms=123', '--demo'] } as org.gradle.process.CommandLineArgumentProvider)
    }
    if (name == 'managedProviderClient') jvmArgumentProviders.add({ -> ['-Dfabric.client.gametest=false'] } as org.gradle.process.CommandLineArgumentProvider)
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
                   FIXTURE_TEST_OUTPUT=str(output), GRADLE_USER_HOME=str(self.root / 'gradle-home'))
        env.update(overrides)
        result = subprocess.run([GRADLE, '--offline', '--no-daemon', '--console=plain',
                                 '--init-script', str(ADAPTER), *tasks], cwd=self.project,
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        return result, output

    def identity_args(self, args):
        self.assertEqual(args.count('--username'), 1)
        self.assertEqual(args.count('--uuid'), 1)
        name, actor = args[args.index('--username') + 1], args[args.index('--uuid') + 1]
        self.assertRegex(name, r'^Lab[0-9a-f]{13}$')
        self.assertEqual(uuid.UUID(actor).version, 3)
        return ['--username', name, '--uuid', actor]

    def test_client_gets_one_private_destination_preserving_other_arguments(self):
        result, output = self.launch(['customClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        args = json.loads((output / 'customClient.json').read_text())
        self.assertEqual(args, ['--width', '640', '--height', '360'] + self.identity_args(args) + ['--gameDir',
                                str(self.private), '--quickPlaySingleplayer', 'lab-fixture'])
        self.assertEqual(Path((output / 'customClient.json.cwd').read_text()), self.private)
        self.assertIn('LAB_WORLD_FIXTURE_LAUNCH:', result.stdout)

    def test_managed_client_server_and_unit_launchers_are_untouched(self):
        result, output = self.launch(['managedClient', 'serverTask', 'unitTask', 'managedProviderClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        for name in ('managedClient', 'serverTask', 'unitTask', 'managedProviderClient'):
            args = json.loads((output / (name + '.json')).read_text())
            self.assertIn('--gameDir=old-run', args)
            self.assertIn('old.example:25565', args)
            self.assertEqual(Path((output / (name + '.json.cwd')).read_text()), self.project)
        self.assertIn('LAB_WORLD_FIXTURE_MANAGED:', result.stdout)
        self.assertNotIn('LAB_WORLD_FIXTURE_LAUNCH:', result.stdout)

    def test_lazy_launcher_arguments_and_jvm_side_are_preserved(self):
        result, output = self.launch(['providerClient'])
        self.assertEqual(result.returncode, 0, result.stdout)
        args = json.loads((output / 'providerClient.json').read_text())
        self.assertEqual(args, ['--width', '640', '--height', '360', '--demo'] + self.identity_args(args) + ['--gameDir',
                                str(self.private), '--quickPlaySingleplayer', 'lab-fixture'])
        self.assertEqual(Path((output / 'providerClient.json.cwd').read_text()), self.private)

    def test_explicit_managed_mode_does_not_override_custom_harness(self):
        result, output = self.launch(['customClient'], LAB_WORLD_FIXTURE_MODE='managed')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('--gameDir=old-run', json.loads((output / 'customClient.json').read_text()))

    def test_missing_world_fails_before_client_launch(self):
        result, output = self.launch(['customClient'], LAB_WORLD_FIXTURE_NAME='not-prepared')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('prepared private world is missing', result.stdout)
        self.assertFalse((output / 'customClient.json').exists())

    def test_world_name_cannot_escape_saves(self):
        result, output = self.launch(['customClient'], LAB_WORLD_FIXTURE_NAME='../outside')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe private world name', result.stdout)
        self.assertFalse((output / 'customClient.json').exists())

    @unittest.skipUnless(hasattr(os, 'symlink'), 'Symlinks unavailable')
    def test_world_symlink_is_rejected_before_client_launch(self):
        link = self.private / 'saves/linked-world'
        try:
            link.symlink_to(self.private / 'saves/lab-fixture', target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        try:
            result, output = self.launch(['customClient'], LAB_WORLD_FIXTURE_NAME='linked-world')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('contains a symlink', result.stdout)
            self.assertFalse((output / 'customClient.json').exists())
        finally:
            link.unlink()


if __name__ == '__main__':
    unittest.main()
