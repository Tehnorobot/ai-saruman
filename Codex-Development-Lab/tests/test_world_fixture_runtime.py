"""Fixture orchestration/isolation tests; synthetic files are not Minecraft proof."""
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from worker import world_fixture as runtime
from worker.world_templates import TemplateStore, WorldTemplateError
from test_world_templates import saved_world


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "O_NOFOLLOW"),
                     "Verifier fixture integration requires Linux isolated filesystem operations")
class WorldFixtureRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "gradle.properties").write_text("minecraft_version=1.21.11\n", encoding="utf-8")
        self.output = self.root / "preparation"
        self.store = self.root / "templates"
        self.timeout = 30
        self.mkdtemp = tempfile.mkdtemp

        def local_temporary(*args, **kwargs):
            if len(args)<3 and (kwargs.get("dir") is None or str(kwargs.get("dir")) == "/game-data"):
                kwargs["dir"] = self.root
            return self.mkdtemp(*args, **kwargs)

        directory_patch = patch.object(runtime.tempfile, "mkdtemp", side_effect=local_temporary)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)

    def properties(self, text):
        (self.workspace / "gradle.properties").write_text(text, encoding="utf-8")

    def session(self, *, eula=True):
        return runtime.WorldFixtureSession(self.workspace, self.output, self.timeout,
                                           eula=eula, store=self.store)

    @staticmethod
    def process(code=0):
        value = Mock()
        value.pid = 123456
        value.returncode = code
        value.wait.return_value = code
        value.poll.return_value = code
        return value

    def publish(self):
        return TemplateStore(self.store).ensure(runtime.PROFILE, saved_world)

    def ready_receipt(self):
        return {"status": "TEMPLATE_READY", "profile": deepcopy(runtime.PROFILE),
                "template": self.publish(), "generation": {},
                "provenance": "Test fixture receipt, not acceptance."}

    def helper(self, receipt=None, *, code=0):
        def start(argv, **kwargs):
            self.assertIn("--existing-eula-consent", argv)
            self.assertTrue(kwargs.get("start_new_session"))
            self.assertTrue(kwargs.get("close_fds"))
            output = Path(argv[argv.index("--output") + 1])
            output.mkdir(parents=True, exist_ok=True)
            value = self.ready_receipt() if receipt is None else receipt
            (output / "world-fixture.json").write_text(json.dumps(value), encoding="utf-8")
            return self.process(code)
        return start

    def scenario_directory(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def test_eula_false_does_not_start_helper_or_create_world_files(self):
        with patch.object(runtime.subprocess, "Popen") as popen:
            session = self.session(eula=False)
            self.assertEqual(session.finish()["status"], "NOT_TESTED")
            with self.assertRaisesRegex(RuntimeError, "World fixture unavailable"):
                session.launch(["gradle", "runClientGameTest"], "one", self.root)
            session.close()
        popen.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.store.exists())
        self.assertIsNone(session.temp)

    def test_prepare_without_consent_emits_diagnostic_only(self):
        with patch.object(runtime, "generate_world") as generate, patch.object(runtime.subprocess, "Popen") as popen:
            result = runtime.prepare(self.store, self.output, self.timeout, eula=False)
        self.assertEqual(result["status"], "NOT_TESTED")
        self.assertEqual(json.loads((self.output / "world-fixture.json").read_text())["status"], "NOT_TESTED")
        self.assertFalse(self.store.exists())
        self.assertEqual(list(self.root.rglob("eula.txt")), [])
        generate.assert_not_called()
        popen.assert_not_called()

    def test_transient_filesystem_failure_retries_without_model_or_loss_of_diagnostics(self):
        with patch.object(runtime,'TemplateStore') as factory,patch.object(runtime.time,'sleep') as sleep:
            factory.return_value.ensure.side_effect=[OSError(errno.ENODATA,'temporary volume state'),{'key':'real-store-contract-mock'}]
            result=runtime.prepare(self.store,self.output,30,eula=True)
        self.assertEqual(result['status'],'TEMPLATE_READY')
        self.assertEqual(factory.return_value.ensure.call_count,2)
        self.assertEqual(result['recovered_filesystem_errors'][0]['errno'],errno.ENODATA)
        self.assertIn('temporary volume state',(self.output/'error.log').read_text())
        sleep.assert_called_once()

    def test_permission_failure_is_not_retried_and_full_trace_is_saved(self):
        with patch.object(runtime,'TemplateStore') as factory,patch.object(runtime.time,'sleep') as sleep:
            factory.return_value.ensure.side_effect=PermissionError(errno.EACCES,'protected data')
            result=runtime.prepare(self.store,self.output,30,eula=True)
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(factory.return_value.ensure.call_count,1)
        self.assertIn('PermissionError',(self.output/'error.log').read_text())
        sleep.assert_not_called()

    def test_filesystem_recovery_obeys_existing_verification_time_budget(self):
        with patch.object(runtime,'TemplateStore') as factory,patch.object(runtime.time,'monotonic',side_effect=[0,31]),patch.object(runtime.time,'sleep') as sleep:
            factory.return_value.ensure.side_effect=OSError(errno.ESTALE,'stale volume')
            result=runtime.prepare(self.store,self.output,30,eula=True)
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(factory.return_value.ensure.call_count,1)
        sleep.assert_not_called()

    def test_done_line_from_an_old_generator_log_cannot_complete_a_new_run(self):
        server=self.root/'server.jar';server.write_bytes(b'synthetic asset; never executed')
        log=self.root/'prepare.log';log.write_text('[Server thread/INFO]: Done (1.0s)!\n')
        destination=self.root/'world';destination.mkdir()
        process=self.process()
        with patch.object(runtime,'SERVER_SHA256',hashlib.sha256(server.read_bytes()).hexdigest()),patch.object(runtime.subprocess,'Popen',return_value=process),self.assertRaisesRegex(RuntimeError,'did not finish'):
            runtime.generate_world(destination,log,30,server=server)
        process.stdin.write.assert_not_called()

    def test_two_scenarios_get_fresh_world_and_game_directory_without_addon_config_carryover(self):
        # Deliberately dirty project caches must not seed a new game's config.
        old = self.workspace / "run/config"
        old.mkdir(parents=True)
        (old / "mage_home.json").write_text('{"enabled":true,"old":true}')
        original = ["bash", "gradlew", "--console=plain", "runClientGameTest"]
        with patch.object(runtime.subprocess, "Popen", side_effect=self.helper()) as popen:
            session = self.session()
            first_argv, first_env, first = session.launch(original, "same-scenario", self.scenario_directory("one"))
            first_game = Path(first_env["LAB_WORLD_FIXTURE_GAME_DIR"])
            first_world = first_game / "saves" / first_env["LAB_WORLD_FIXTURE_NAME"]
            pristine_region = (first_world / "region/r.0.0.mca").read_bytes()
            (first_game / "config").mkdir()
            (first_game / "config/mage_home.json").write_text('{"enabled":true}')
            (first_world / "region/r.0.0.mca").write_bytes(b"changed by first scenario")
            second_argv, second_env, second = session.launch(original, "same-scenario", self.scenario_directory("two"))
            second_game = Path(second_env["LAB_WORLD_FIXTURE_GAME_DIR"])
            second_world = second_game / "saves" / second_env["LAB_WORLD_FIXTURE_NAME"]
            self.assertNotEqual(first_game, second_game)
            self.assertNotEqual(first["run_id"], second["run_id"])
            self.assertFalse((second_game / "config/mage_home.json").exists())
            self.assertEqual((second_world / "region/r.0.0.mca").read_bytes(), pristine_region)
            self.assertNotEqual((first_world / "level.dat").stat().st_ino, (second_world / "level.dat").stat().st_ino)
            self.assertEqual(first_argv[:-2], original)
            self.assertEqual(second_argv[:-2], original)
            self.assertEqual(first_argv[-2:], ["--init-script", str(runtime.ADAPTER)])
            self.assertEqual(original, ["bash", "gradlew", "--console=plain", "runClientGameTest"])
            self.assertEqual(first["status"], "CLONE_READY")
            self.assertEqual(second["status"], "CLONE_READY")
            self.assertNotIn("PASS", first["status"])
            self.assertEqual(first_env["LAB_WORLD_FIXTURE_MODE"], "quickplay")
            self.assertIn("pauseOnLostFocus:false", (second_game / "options.txt").read_text())
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(session.close()["status"], "TEMPLATE_READY")

    def test_scenario_id_never_becomes_a_filesystem_path(self):
        with patch.object(runtime.subprocess, "Popen", side_effect=self.helper()):
            session = self.session()
            _, env, evidence = session.launch(["gradle", "runClientGameTest"], "../../escape", self.scenario_directory("report"))
        self.assertEqual(evidence["scenario_id"], "../../escape")
        self.assertTrue(Path(env["LAB_WORLD_FIXTURE_GAME_DIR"]).is_relative_to(session.temp))
        self.assertFalse((self.root / "escape").exists())

    def test_tampered_template_cannot_be_launched_or_recorded_ready(self):
        with patch.object(runtime.subprocess, "Popen", side_effect=self.helper()):
            session = self.session()
            prepared = session.finish()
            cached = Path(prepared["template"]["path"])
            (cached / "region/r.0.0.mca").write_bytes(b"corrupted")
            report = self.scenario_directory("tampered-report")
            with self.assertRaises(WorldTemplateError):
                session.launch(["gradle", "runClientGameTest"], "one", report)
        self.assertFalse((report / "world-fixture.json").exists())

    def test_managed_and_off_modes_preserve_command_and_do_not_prepare(self):
        for mode in ["managed", "off"]:
            self.properties("minecraft_version=1.21.11\nlab_world_fixture_mode=" + mode + "\n")
            with self.subTest(mode=mode), patch.object(runtime.subprocess, "Popen") as popen:
                session = self.session()
                argv = ["gradle", "--init-script", "existing.gradle", "runClient"]
                actual, env, result = session.launch(argv, "one", self.root)
                self.assertIs(actual, argv)
                self.assertEqual(env, {})
                self.assertEqual(result["status"], "MANAGED")
                self.assertEqual(session.close()["status"], "MANAGED")
                self.assertIsNone(session.temp)
                popen.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.store.exists())

    def test_unsupported_minecraft_version_is_explicit_and_never_gets_wrong_template(self):
        self.properties("minecraft_version=1.20.1\n")
        with patch.object(runtime.subprocess, "Popen") as popen:
            session = self.session()
            result = session.finish()
            self.assertEqual(result["status"], "MANAGED")
            self.assertIn("1.20.1", result["reason"])
            argv = ["gradle", "runClientGameTest"]
            actual, env, report = session.launch(argv, "one", self.root)
            self.assertIs(actual, argv)
            self.assertEqual(env, {})
            self.assertIn("Unsupported", report["detail"])
            self.assertIsNone(session.temp)
            popen.assert_not_called()

    def test_unknown_minecraft_version_keeps_scenario_lifecycle_without_preparation(self):
        (self.workspace / "gradle.properties").unlink()
        with patch.object(runtime.subprocess, "Popen") as popen:
            session = self.session()
            result = session.finish()
        self.assertEqual(result["status"], "MANAGED")
        self.assertIn("not established", result["reason"])
        popen.assert_not_called()
        self.assertFalse(self.store.exists())

    def test_literal_minecraft_version_in_gradle_build_selects_the_correct_profile(self):
        (self.workspace / "gradle.properties").unlink()
        (self.workspace / "build.gradle").write_text('dependencies { minecraft "com.mojang:minecraft:1.21.11" }')
        with patch.object(runtime.subprocess, "Popen", side_effect=self.helper()):
            self.assertEqual(self.session().finish()["status"], "TEMPLATE_READY")

    def test_invalid_mode_is_an_error_with_no_helper_process(self):
        self.properties("lab_world_fixture_mode=../arbitrary-path\n")
        with patch.object(runtime.subprocess, "Popen") as popen:
            result = self.session().finish()
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("lab_world_fixture_mode", result["reason"])
        popen.assert_not_called()

    def test_symlinked_gradle_properties_are_not_read(self):
        source = self.root / "outside.properties"
        source.write_text("minecraft_version=1.21.11")
        (self.workspace / "gradle.properties").unlink()
        (self.workspace / "gradle.properties").symlink_to(source)
        with patch.object(runtime.subprocess, "Popen") as popen:
            self.assertEqual(self.session().finish()["status"], "ERROR")
        popen.assert_not_called()

    def test_generation_failure_does_not_publish_pass_and_next_attempt_can_recover(self):
        with patch.object(runtime, "generate_world", side_effect=RuntimeError("server failed to close")):
            failed = runtime.prepare(self.store, self.output, self.timeout, eula=True)
        self.assertEqual(failed["status"], "ERROR")
        self.assertIn("server failed to close", failed["reason"])
        self.assertEqual(list((self.store / "templates").iterdir()), [])

        def generate(path, logfile, timeout):
            saved_world(path)
            return {"generator": "synthetic-test", "clean_shutdown": True}

        with patch.object(runtime, "generate_world", side_effect=generate) as called:
            recovered = runtime.prepare(self.store, self.output, self.timeout, eula=True)
            reused = runtime.prepare(self.store, self.output, self.timeout, eula=True)
        self.assertEqual(recovered["status"], "TEMPLATE_READY")
        self.assertNotEqual(recovered["status"], "PASS")
        self.assertFalse(recovered["template"]["reused"])
        self.assertTrue(reused["template"]["reused"])
        self.assertEqual(called.call_count, 1)

    def test_old_ready_receipt_is_removed_before_new_helper_start(self):
        self.output.mkdir()
        (self.output / "world-fixture.json").write_text(json.dumps(self.ready_receipt()))
        process = self.process(1)

        def failed_helper(argv, **kwargs):
            output = Path(argv[argv.index("--output") + 1])
            self.assertFalse((output / "world-fixture.json").exists())
            return process

        with patch.object(runtime.subprocess, "Popen", side_effect=failed_helper):
            session = self.session()
            self.assertEqual(session.finish()["status"], "ERROR")

    def test_nonzero_helper_exit_cannot_promote_a_ready_receipt(self):
        with patch.object(runtime.subprocess, "Popen", side_effect=self.helper(code=1)):
            result = self.session().finish()
        self.assertEqual(result["status"], "ERROR")
        self.assertNotEqual(result["status"], "TEMPLATE_READY")

    def test_malformed_or_wrong_profile_receipts_are_errors(self):
        wrong_profile = {**runtime.PROFILE, "minecraft_version": "1.20.1"}
        for receipt in [[], None, {"status": "PASS", "profile": runtime.PROFILE},
                        {"status": "TEMPLATE_READY", "profile": wrong_profile}]:
            # helper(None) means a valid receipt, so represent JSON null explicitly.
            def start(argv, **kwargs):
                output = Path(argv[argv.index("--output") + 1])
                (output / "world-fixture.json").write_text(json.dumps(receipt))
                return self.process(0)
            with self.subTest(receipt_type=type(receipt).__name__), patch.object(runtime.subprocess, "Popen", side_effect=start):
                self.assertEqual(self.session().finish()["status"], "ERROR")

    def test_helper_timeout_is_stopped_and_cached_as_error(self):
        process = self.process()
        process.wait.side_effect = subprocess.TimeoutExpired("prepare", self.timeout)
        with patch.object(runtime.subprocess, "Popen", return_value=process), patch.object(runtime, "_stop") as stop:
            session = self.session()
            first = session.finish()
            second = session.finish()
        self.assertEqual(first["status"], "ERROR")
        self.assertIs(first, second)
        stop.assert_called_once_with(process)

    def test_build_failure_can_cancel_preparation_without_waiting_or_touching_game_files(self):
        process = self.process()
        process.poll.return_value = None
        with patch.object(runtime.subprocess, "Popen", return_value=process), patch.object(runtime, "_stop") as stop:
            session = self.session()
            result = session.close(cancel=True)
        self.assertEqual(result["status"], "NOT_EXECUTED")
        stop.assert_called_once_with(process)
        process.wait.assert_not_called()
        self.assertIsNone(session.temp)

    def test_finished_preparation_receipt_survives_a_later_build_failure(self):
        with patch.object(runtime.subprocess,'Popen',side_effect=self.helper()):
            session=self.session()
            self.assertEqual(session.close(cancel=True)['status'],'TEMPLATE_READY')

    def test_cli_flag_alone_does_not_create_consent(self):
        argv = ["world_fixture", "prepare", "--output", str(self.output), "--store", str(self.store),
                "--timeout", "30", "--existing-eula-consent"]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"MINECRAFT_EULA_ACCEPTED": "false"}), \
                patch.object(runtime.os, "geteuid", return_value=0), patch.object(runtime.signal, "signal"), \
                patch("builtins.print"), patch.object(runtime, "prepare") as prepare:
            prepare.return_value = {"status": "NOT_TESTED"}
            self.assertEqual(runtime.main(), 1)
        self.assertFalse(prepare.call_args.kwargs["eula"])

    def test_changed_pinned_server_asset_is_rejected_before_process_or_eula_file(self):
        server = self.root / "server.jar"
        server.write_bytes(b"not the trusted pinned server")
        destination = self.root / "generation"
        destination.mkdir()
        with patch.object(runtime.subprocess, "Popen") as popen, self.assertRaisesRegex(ValueError, "missing or changed"):
            runtime.generate_world(destination, self.root / "prepare.log", self.timeout, server=server)
        popen.assert_not_called()
        self.assertEqual(list(self.root.rglob("eula.txt")), [])
        self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
