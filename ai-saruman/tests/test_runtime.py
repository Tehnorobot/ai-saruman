"""Lifecycle and IPC checks: no real game process, credentials, models or publications."""
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import Mock, patch

from saruman.runtime import CharacterRuntime, MinecraftProcess, atomic_json
from saruman.telegram import Director, Store
from saruman.roles import load


class FakeGame:
    def __init__(self):
        self.active = False
        self.value = {"status": "stopped", "reason": "idle"}
    def request(self, active): self.active = active
    def heartbeat(self): pass
    def shutdown(self): self.active = False
    def read(self,name):return {}
    def step(self): self.value = {"status": "unavailable" if self.active else "stopped", "reason": "game offline"}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "telegram.sqlite3")
        self.director = Mock()
        self.game = FakeGame()
        self.runtime = CharacterRuntime(self.root, self.store, self.director, game=self.game)
        self.addCleanup(self.runtime.close)

    def test_start_without_game_and_stop_preserve_queue_and_schedule(self):
        post = self.store.manual("Test")
        self.store.action(post,"queue")
        self.store.set_meta("next_draft", 123456)
        self.runtime.start({"minecraft": False})
        self.runtime.step()
        self.assertTrue(self.store.meta("character_runtime")["active"])
        self.assertFalse(self.game.active)
        self.runtime.stop()
        self.assertFalse(self.store.meta("character_runtime")["active"])
        self.assertEqual(self.store.snapshot()["posts"][0]["status"],"queued")
        self.assertEqual(self.store.meta("next_draft"),123456)
        self.runtime.start()
        self.assertFalse(self.game.active)  # Saved component selection survives Start.
        self.assertEqual(self.store.meta("next_draft"),123456)

    def test_game_failure_cannot_disable_telegram_or_owner_heartbeat(self):
        self.store.configure({"enabled":True})
        self.game.step = Mock(side_effect=OSError("unavailable"))
        self.runtime.start()
        self.runtime.step()
        self.assertTrue(self.store.meta("character_runtime")["active"])
        self.assertEqual(self.runtime.snapshot()["components"]["telegram"]["status"],"running")
        self.assertIsNotNone(self.runtime.snapshot()["error"])

    def test_second_lab_cannot_take_ownership(self):
        other=CharacterRuntime(self.root,self.store,Mock(),game=FakeGame())
        self.addCleanup(other.close)
        self.runtime.start({"minecraft":False})
        with self.assertRaisesRegex(RuntimeError,"другим"):
            other.start({"minecraft":False})
        self.assertTrue(self.store.meta("character_runtime")["active"])
        other.close()  # Non-owner Stop/close cannot disable the real owner.
        self.assertTrue(self.store.meta("character_runtime")["active"])

    def test_input_does_not_accept_commands_or_paths(self):
        for payload in ({"minecraft":"true"},{"command":"powershell"},{"path":"C:/elsewhere"}):
            with self.assertRaises(ValueError):self.runtime.start(payload)
        self.director.start.assert_not_called()

    def test_adopt_existing_managed_game_and_stop_by_revision(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/"command.json",{"worker_id":"owned","revision":4,"active":True,"updated":time.time()})
        atomic_json(game.directory/"status.json",{"worker_id":"owned","revision":4,"phase":"ready","agent_running":True,"world_ready":True,"updated":time.time()})
        game.request(False)
        with patch("saruman.runtime.subprocess.Popen",side_effect=AssertionError("duplicate game")):
            game.step()
        command=game.read("command.json")
        self.assertFalse(command["active"])
        self.assertGreater(command["revision"],4)
        self.assertEqual(game.value["status"],"stopping")


class ManagedGameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.now=1000
        self.control=load("managed").ManagedControl(self.root,"worker",clock=lambda:self.now)
        self.running=False
        self.starts=self.stops=0
        def start():self.running=True;self.starts+=1
        def stop():self.running=False;self.stops+=1
        self.control.bind(start,stop,lambda:{"agent_running":self.running})

    def command(self,revision=1,active=True,**changes):
        atomic_json(self.root/"command.json",{"worker_id":"worker","revision":revision,"active":active,"updated":self.now,**changes})

    def test_heartbeat_does_not_rearm_manual_stop(self):
        self.command();self.control.step()
        self.assertEqual(self.starts,1)
        self.running=False  # A human stopped the agent in the native UI.
        self.now+=1;self.command();self.control.step()
        self.assertEqual(self.starts,1)
        self.command(revision=2);self.control.step()
        self.assertEqual(self.starts,2)

    def test_stop_is_acknowledged_and_old_owner_cannot_restart(self):
        self.command();self.control.step()
        self.command(revision=2,active=False);self.control.step()
        state=json.loads((self.root/"status.json").read_text())
        self.assertFalse(state["agent_running"])
        self.assertEqual(state["revision"],2)
        self.command(revision=3,worker_id="other");self.control.step()
        self.assertFalse(self.running)

    def test_owner_disappearance_stops_agent_once(self):
        self.command();self.control.step()
        self.now+=16
        self.control.step();self.control.step()
        self.assertFalse(self.running)
        self.assertEqual(self.stops,1)

    def test_status_file_is_complete_json(self):
        self.command();self.control.step()
        state=json.loads((self.root/"status.json").read_text())
        self.assertEqual(state["worker_id"],"worker")
        self.assertTrue(state["owner_connected"])
        self.assertEqual(list(self.root.glob("*.tmp")),[])

    def test_persisted_shutdown_is_safe_even_after_owner_expired(self):
        shutdown=Mock()
        self.control.bind(lambda:None,lambda:None,lambda:{},shutdown)
        self.command(active=False,shutdown=True)
        self.now+=16
        self.control.step();self.control.step()
        shutdown.assert_called_once()

    def test_blocking_agent_loop_does_not_block_stop_or_heartbeat(self):
        running=threading.Event();release=threading.Event()
        def run():running.set();release.wait(5);running.clear()
        worker=load("managed").BackgroundAgent(run,running.is_set)
        self.control.bind(worker.start,release.set,lambda:{"agent_running":running.is_set()})
        try:
            self.command();self.control.step()
            self.assertTrue(running.wait(1))
            self.command(revision=2,active=False);self.control.step()
            worker.thread.join(1)
            self.assertFalse(worker.thread.is_alive())
            state=json.loads((self.root/"status.json").read_text())
            self.assertEqual(state['revision'],2)
        finally:release.set();worker.thread.join(1)


if __name__=="__main__":unittest.main()
