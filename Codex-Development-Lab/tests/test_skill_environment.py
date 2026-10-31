"""Independent skill-installation regressions; synthetic projects, no model calls."""
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import daemon as executor
from worker import engine


REPO = Path(__file__).resolve().parents[1]
OLD_NAME = 'fabric-minecraft-1.21.11'
NEW_NAME = 'fabric-minecraft-12111'


class SkillEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.getenv('LAB_SKILL_TEST_ROOT'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / 'project'
        self.workspace = self.project / 'workspace'
        self.workspace.mkdir(parents=True)
        (self.project / 'ТЗ.md').write_text('# Synthetic specification\n', encoding='utf-8')
        self.template = self.root / 'template'
        (self.template / 'skills').mkdir(parents=True)
        (self.template / 'AGENTS.md').write_text('Synthetic platform rules\n', encoding='utf-8')
        self.skills = self.workspace / '.agents' / 'skills'
        self.skills.mkdir(parents=True)
        self.make_skill(self.template / 'skills' / NEW_NAME, NEW_NAME)
        self.make_skill(self.template / 'skills' / 'code-quality', 'code-quality')

    @staticmethod
    def make_skill(directory, name, body='Template instructions\n'):
        directory.mkdir(parents=True, exist_ok=True)
        text = f'---\nname: {name}\ndescription: Synthetic fixture.\n---\n{body}'
        (directory / 'SKILL.md').write_text(text, encoding='utf-8')
        return text

    def install(self, template=None):
        return engine.copy_skill_environment(self.project, template_root=template or self.template)

    def test_real_bundled_skills_are_copied_into_new_project(self):
        self.assertEqual(self.install(REPO), self.workspace)
        bundled = sorted((REPO / 'skills').glob('*/SKILL.md'))
        self.assertGreaterEqual(len(bundled), 4)
        self.assertTrue((self.skills / 'code-quality' / 'SKILL.md').is_file())
        for source in bundled:
            with self.subTest(skill=source.parent.name):
                copied = self.skills / source.parent.name / 'SKILL.md'
                self.assertEqual(copied.read_bytes(), source.read_bytes())
                name = re.search(r'^name:\s*(.*?)\s*$', copied.read_text(encoding='utf-8'), re.M)
                self.assertIsNotNone(name)
                self.assertRegex(name[1], r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
                self.assertEqual(name[1], copied.parent.name)

    def test_repeated_install_preserves_project_customizations(self):
        self.install()
        customized = self.skills / 'code-quality' / 'SKILL.md'
        customized.write_text('User-owned skill body\n', encoding='utf-8')
        extra = self.skills / 'code-quality' / 'references' / 'local.txt'
        extra.parent.mkdir()
        extra.write_bytes(b'User-owned reference\x00\xff')
        files = [customized, extra, self.workspace / 'AGENTS.md', self.workspace / 'ТЗ.md',
                 self.workspace / '.codex' / 'config.toml',
                 self.workspace / '.codex' / 'agents' / 'reviewer.toml']
        for index, path in enumerate(files[2:]):
            path.write_text(f'User customization {index}\n', encoding='utf-8')
        before = {path: path.read_bytes() for path in files}
        self.install()
        self.assertEqual({path: path.read_bytes() for path in files}, before)

    def test_legacy_migration_preserves_body_resources_and_quoted_names(self):
        for quote in ('', '"', "'"):
            with self.subTest(quote=quote):
                old = self.skills / OLD_NAME
                body = f'User behavior mentions name: {OLD_NAME}\n'
                original = self.make_skill(old, quote + OLD_NAME + quote, body)
                resource = old / 'references' / 'user.bin'
                resource.parent.mkdir()
                resource.write_bytes(b'\x00\x80custom\xff')
                self.install()
                new = self.skills / NEW_NAME
                self.assertFalse(old.exists())
                migrated = (new / 'SKILL.md').read_text(encoding='utf-8')
                self.assertEqual(migrated.split('---', 2)[2], original.split('---', 2)[2])
                self.assertRegex(migrated, r'(?m)^name:\s*[\"\']?' + NEW_NAME + r'[\"\']?\s*$')
                self.assertEqual((new / 'references' / 'user.bin').read_bytes(), b'\x00\x80custom\xff')
                # Remove only this synthetic fixture before the next quoting variant.
                shutil.rmtree(new)

    def test_collision_archives_legacy_without_overwriting_either_custom_skill(self):
        old = self.skills / OLD_NAME
        legacy = self.make_skill(old, OLD_NAME, 'Legacy custom behavior\n')
        (old / 'local.txt').write_text('Keep old resource\n', encoding='utf-8')
        new = self.skills / NEW_NAME
        current = self.make_skill(new, NEW_NAME, 'Current custom behavior\n')
        previous_archive = self.workspace / '.lab-legacy-skills' / OLD_NAME
        self.make_skill(previous_archive, OLD_NAME, 'Earlier archive\n')
        previous_bytes = (previous_archive / 'SKILL.md').read_bytes()
        self.install()
        self.assertFalse(old.exists())
        self.assertEqual((new / 'SKILL.md').read_text(encoding='utf-8'), current)
        self.assertEqual((previous_archive / 'SKILL.md').read_bytes(), previous_bytes)
        archived = list((self.workspace / '.lab-legacy-skills').glob('*/local.txt'))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(encoding='utf-8'), 'Keep old resource\n')
        self.assertEqual((archived[0].parent / 'SKILL.md').read_text(encoding='utf-8'), legacy)
        self.install()
        self.assertEqual(len(list((self.workspace / '.lab-legacy-skills').iterdir())), 2)

    def test_already_renamed_folder_with_old_manifest_is_repaired_in_place(self):
        new = self.skills / NEW_NAME
        original = self.make_skill(new, OLD_NAME, 'Existing local behavior\n')
        self.install()
        self.assertEqual((new / 'SKILL.md').read_text(encoding='utf-8'),
                         original.replace('name: ' + OLD_NAME + '\n', 'name: ' + NEW_NAME + '\n', 1))

    def test_unrelated_user_skills_are_preserved(self):
        user_skill = self.skills / 'my-private-helper'
        original = self.make_skill(user_skill, 'my-private-helper', 'My own implementation rules\n')
        self.install()
        self.assertEqual((user_skill / 'SKILL.md').read_text(encoding='utf-8'), original)

    def test_escaping_legacy_directory_is_rejected_without_changing_target(self):
        outside = self.root / 'outside'
        original = self.make_skill(outside, OLD_NAME, 'Do not mutate this target\n')
        try:
            (self.skills / OLD_NAME).symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Creating directory symlinks is unavailable: ' + str(exc))
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((outside / 'SKILL.md').read_text(encoding='utf-8'), original)

    def test_escaping_legacy_manifest_is_rejected_without_changing_target(self):
        outside = self.root / 'outside-skill.md'
        original = f'---\nname: {OLD_NAME}\ndescription: Outside fixture.\n---\n'
        outside.write_text(original, encoding='utf-8')
        old = self.skills / OLD_NAME
        old.mkdir()
        try:
            (old / 'SKILL.md').symlink_to(outside)
        except OSError as exc:
            self.skipTest('Creating file symlinks is unavailable: ' + str(exc))
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(outside.read_text(encoding='utf-8'), original)
        self.assertTrue(old.is_dir())
        self.assertFalse((self.skills / NEW_NAME).exists())

    def test_unsafe_manifest_fails_before_migration_even_without_symlink_privileges(self):
        """Exercise preflight ordering with only the filesystem boundary mocked."""
        old = self.skills / OLD_NAME
        original = self.make_skill(old, OLD_NAME, 'Keep directory in place on rejected migration\n')
        real_is_symlink = Path.is_symlink

        def symlink_boundary(path):
            return path == old / 'SKILL.md' or real_is_symlink(path)

        with patch.object(Path, 'is_symlink', symlink_boundary):
            with self.assertRaises(ValueError):
                self.install()
        self.assertTrue(old.is_dir())
        self.assertFalse((self.skills / NEW_NAME).exists())
        self.assertEqual((old / 'SKILL.md').read_text(encoding='utf-8'), original)

    def test_executor_updates_both_pending_and_recovered_projects_before_execution(self):
        projects = self.root / 'executor-projects'
        for index, phase in enumerate(('requests', 'running')):
            with self.subTest(phase=phase):
                pid = f'{index + 1:014x}'
                jid = f'{index + 10:014x}'
                tid = jid + '-round01'
                ws = projects / pid / 'workspace'
                skills = ws / '.agents' / 'skills'
                self.make_skill(skills / OLD_NAME, OLD_NAME, 'Recovered custom behavior\n')
                request = self.root / phase / (tid + '.json')
                request.parent.mkdir(exist_ok=True)
                request.write_text(json.dumps({'id': tid, 'project_id': pid,
                                               'job_id': jid, 'prompt': 'Synthetic test'}), encoding='utf-8')

                def observe_execution_boundary(source):
                    self.assertEqual(source, request)
                    self.assertFalse((skills / OLD_NAME).exists())
                    self.assertIn('Recovered custom behavior',
                                  (skills / NEW_NAME / 'SKILL.md').read_text(encoding='utf-8'))
                    self.assertTrue((skills / 'code-quality' / 'SKILL.md').is_file())

                with patch.object(executor, 'PROJECTS', projects), \
                     patch.object(executor, '_run_task', side_effect=observe_execution_boundary) as execute:
                    executor.run_task(request)
                execute.assert_called_once_with(request)


if __name__ == '__main__':
    unittest.main()
