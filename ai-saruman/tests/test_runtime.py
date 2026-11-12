"""Lifecycle and IPC checks: no real game process, credentials, models or publications."""
import json
from pathlib import Path
import tempfile
import time
import threading
import sqlite3
import unittest
from unittest.mock import Mock, MagicMock, patch
from types import SimpleNamespace

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


class AtomicIpcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'command.json'
        atomic_json(self.path, {'revision': 1})

    def test_windows_sharing_conflict_retries_same_write_and_preserves_old_json(self):
        managed = load('managed')
        original = managed.os.replace
        calls = []
        def replace(source, target):
            calls.append((source, target))
            if len(calls) < 3:
                self.assertEqual(json.loads(self.path.read_text()), {'revision': 1})
                error = PermissionError('Reader holds the previous file')
                error.winerror = 32 if len(calls) == 1 else 5
                raise error
            return original(source, target)
        with patch.object(managed.os, 'replace', side_effect=replace), patch.object(managed.time, 'sleep'):
            atomic_json(self.path, {'revision': 2, 'directive': {'id': 'same-command'}})
        self.assertEqual(json.loads(self.path.read_text()), {'revision': 2, 'directive': {'id': 'same-command'}})
        self.assertEqual(len({str(source) for source, _ in calls}), 1)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_real_permission_error_surfaces_without_destroying_previous_command(self):
        managed = load('managed')
        with patch.object(managed.os, 'replace', side_effect=PermissionError('Denied')) as replace, patch.object(managed.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                atomic_json(self.path, {'revision': 2})
        replace.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), {'revision': 1})
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])


class LifecycleTests(unittest.TestCase):
    def test_busy_journal_cannot_prevent_game_heartbeat(self):
        now=[1000.0];self.runtime.clock=lambda:now[0]
        game=MinecraftProcess(self.root,clock=self.runtime.clock);self.runtime.game=game
        game.worker_id='owned';game.request(True)
        for instant in (1000.0,1002.0):
            now[0]=instant
            atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':instant,'phase':'ready','agent_running':True,'world_ready':True})
            with patch.object(self.store,'set_meta',side_effect=sqlite3.OperationalError('database is locked')):
                self.runtime.step()
            self.assertEqual(game.read('command.json')['updated'],instant)
            self.assertIn('Журнал временно недоступен',self.runtime.error)
        self.runtime.step();self.assertIsNone(self.runtime.error)

    def test_lifecycle_loop_survives_a_failed_iteration(self):
        ticked=threading.Event();calls=[]
        def step():
            calls.append(1)
            if len(calls)==1:raise sqlite3.OperationalError('database is locked')
            ticked.set()
        with patch.object(self.runtime,'step',side_effect=step):
            self.runtime.start({'minecraft':False})
            self.assertTrue(ticked.wait(5))
            self.assertTrue(self.runtime.thread.is_alive())

    def test_start_recovers_a_dead_lifecycle_thread(self):
        dead=threading.Thread(target=lambda:None);dead.start();dead.join()
        self.runtime.thread=dead;self.runtime.start({'minecraft':False})
        self.assertIsNot(self.runtime.thread,dead);self.assertTrue(self.runtime.thread.is_alive())

    def test_stop_reaches_game_even_when_journal_commit_fails(self):
        self.runtime.start({'minecraft':False});self.runtime.game.directive={'id':'unfinished','text':'work'}
        with patch.object(self.store,'set_meta',side_effect=sqlite3.OperationalError('database is locked')):
            result=self.runtime.stop()
        self.assertFalse(result['active']);self.assertFalse(self.game.active);self.assertIsNone(self.game.directive)

    def test_cached_running_state_is_not_shown_when_lifecycle_thread_died(self):
        self.runtime.active=True;self.runtime.last_tick=time.time()
        self.runtime.thread=Mock();self.runtime.thread.is_alive.return_value=False
        self.runtime.game.value={'status':'running','reason':'old cached value'}
        result=self.runtime.snapshot()
        self.assertFalse(result['health']['healthy'])
        self.assertEqual(result['components']['minecraft']['status'],'interrupted')
        self.assertIn('устарело',result['error'])

    def test_wal_allows_heartbeat_write_while_another_connection_reads_history(self):
        self.store.set_meta('example','old')
        with self.store.db() as reader:
            self.assertEqual(reader.execute('PRAGMA journal_mode').fetchone()[0],'wal')
            reader.execute('BEGIN');reader.execute('SELECT * FROM meta').fetchall()
            self.store.set_meta('character_runtime',{'active':True,'updated':1000})
        self.assertEqual(self.store.meta('character_runtime')['updated'],1000)

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
        for payload in ({"minecraft":"true"},{"filter_enabled":"false"},{"filter_enabled":0},{"command":"powershell"},{"path":"C:/elsewhere"}):
            with self.assertRaises(ValueError):self.runtime.start(payload)
        self.director.start.assert_not_called()

    def test_filter_defaults_on_and_explicit_choice_survives_stop_start(self):
        self.assertTrue(self.runtime.snapshot()['options']['filter_enabled'])
        self.runtime.start({'minecraft':False,'filter_enabled':False})
        self.assertFalse(self.game.filter_enabled)
        self.runtime.stop();self.runtime.start()
        self.assertFalse(self.runtime.snapshot()['options']['filter_enabled'])
        self.assertFalse(self.game.filter_enabled)
        self.runtime.stop();self.runtime.start({'filter_enabled':True})
        self.assertTrue(self.game.filter_enabled)

    def test_live_filter_change_requires_stop_without_mutating_saved_options(self):
        self.runtime.start({'minecraft':False})
        before=self.store.meta('runtime_options');epoch=self.runtime.epoch
        with self.assertRaisesRegex(BlockingIOError,'Stop'):
            self.runtime.start({'filter_enabled':False})
        self.assertEqual(self.store.meta('runtime_options'),before)
        self.assertEqual(self.runtime.epoch,epoch)

    def test_old_saved_options_keep_filter_enabled(self):
        self.store.set_meta('runtime_options',{'minecraft':False})
        self.runtime.start()
        self.assertTrue(self.game.filter_enabled)

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

    def test_unavailable_native_api_is_not_reported_as_a_closed_world(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/'command.json',{'worker_id':'owned','revision':1})
        atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':time.time(),'phase':'ready',
            'agent_running':True,'world_ready':False,'native_status':'unavailable','native_error':'stale Java object'})
        game.request(True);game.step()
        self.assertEqual(game.value['status'],'unavailable')
        self.assertIn('stale Java object',game.value['reason'])

    def test_actual_native_activity_reaches_lab_component(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/'command.json',{'worker_id':'owned','revision':1})
        atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':time.time(),'phase':'ready',
            'agent_running':True,'world_ready':True,'native_activity':{'phase':'player','reason':'Обращение игрока: собственное занятие прерывается'}})
        game.request(True);game.step()
        self.assertEqual(game.value['status'],'running')
        self.assertIn('Обращение игрока',game.value['reason'])

    def test_death_is_waiting_respawn_not_a_permanent_manual_pause(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/'command.json',{'worker_id':'owned','revision':1})
        atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':time.time(),'phase':'ready',
            'agent_running':True,'world_ready':True,'manual_stop_latched':True,
            'native_stop_kind':'reset','player_alive':False})
        game.request(True);game.step()
        self.assertEqual(game.value['status'],'waiting_respawn')

    def test_live_process_heartbeat_does_not_hide_a_stale_game_snapshot(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/'command.json',{'worker_id':'owned','revision':1})
        atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':time.time(),'phase':'ready',
            'agent_running':True,'world_ready':True,'native_status':'running',
            'native_updated':time.time()-30,'native_activity':{'reason':'Защита удерживает управление'}})
        game.request(True);game.step()
        self.assertEqual(game.value['status'],'unavailable')
        self.assertNotIn('Защита удерживает',game.value['reason'])

    def test_unavailable_observation_wins_over_saved_running_fields(self):
        game=MinecraftProcess(self.root)
        atomic_json(game.directory/'command.json',{'worker_id':'owned','revision':1})
        atomic_json(game.directory/'status.json',{'worker_id':'owned','updated':time.time(),'phase':'ready',
            'agent_running':True,'world_ready':True,'native_fresh':False,'native_updated':time.time(),
            'native_error':'ConnectionResetError: getGoalRotation'})
        game.request(True);game.step()
        self.assertEqual(game.value['status'],'unavailable')
        self.assertIn('getGoalRotation',game.value['reason'])


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

    def test_uncertain_dispatch_is_not_replayed_after_worker_restart(self):
        managed=load('managed')
        managed.admin_result(self.root,{'command_id':'reserved','status':'dispatching'})
        handler=Mock(return_value={'status':'accepted'});self.control.directive_handler=handler
        self.command(directive={'id':'reserved','text':'grow a tree'})
        self.control.step();handler.assert_not_called()
        saved=json.loads((self.root/'results/reserved.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'],'uncertain')

    def test_startup_stop_and_shutdown_cancel_only_the_matching_worker(self):
        self.assertFalse(self.control.startup_cancelled())
        self.command();self.assertFalse(self.control.startup_cancelled())
        self.command(active=False);self.assertTrue(self.control.startup_cancelled())
        self.command(active=False,worker_id='other');self.assertFalse(self.control.startup_cancelled())
        self.command(shutdown=True);self.assertTrue(self.control.startup_cancelled())

    def test_status_reports_applied_filter_mode(self):
        self.control.filter_enabled=False
        self.command();self.control.step()
        self.assertFalse(json.loads((self.root/'status.json').read_text())['filter_enabled'])

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


class FilterProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1000
        self.game=MinecraftProcess(self.root,clock=lambda:self.now)

    def ready(self,filter_enabled=True):
        self.game.worker_id='owned';self.game.requested=True
        self.game.process=Mock();self.game.process.poll.return_value=None
        atomic_json(self.game.directory/'status.json',{'worker_id':'owned','phase':'ready',
                    'updated':self.now,'filter_enabled':filter_enabled})

    def test_filter_change_gracefully_restarts_owned_worker_before_new_launch(self):
        self.ready();self.game.filter_enabled=False
        with patch('saruman.runtime.subprocess.Popen') as spawn:
            self.game.step()
            command=self.game.read('command.json')
            self.assertFalse(command['active']);self.assertTrue(command['shutdown'])
            self.assertEqual(self.game.value['status'],'restarting')
            spawn.assert_not_called()
            self.game.process.poll.return_value=0
            self.game.step()
            self.assertIsNone(self.game.process)
            self.assertFalse(self.game.restarting)
            self.assertEqual(self.game.retry_at,self.now)
            spawn.assert_not_called()

    def test_stop_during_restart_never_relaunches(self):
        self.ready();self.game.filter_enabled=False;self.game.step()
        self.game.request(False)
        self.assertTrue(self.game.shutdown_requested)
        self.game.process.poll.return_value=0;self.game.step()
        with patch('saruman.runtime.subprocess.Popen') as spawn:self.game.step()
        spawn.assert_not_called();self.assertEqual(self.game.value['status'],'stopped')

    def test_disabled_filter_adds_cli_flag_only_after_explicit_selection(self):
        game_root=self.root/'integrations/saruman'
        python=game_root/'.venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
        (game_root/'.env.saruman').touch()
        def connect(address,**kwargs):
            if address[1]==22228:raise OSError('no external runtime')
            return MagicMock()
        for enabled in (True,False):
            game=MinecraftProcess(self.root,clock=lambda:self.now);game.filter_enabled=enabled;game.request(True)
            with patch('saruman.runtime.os',SimpleNamespace(name='nt',environ={})),patch('saruman.runtime.socket.create_connection',side_effect=connect),patch('saruman.runtime.subprocess.Popen') as spawn:
                game.step()
            self.assertEqual('--no-filter' in spawn.call_args.args[0],not enabled)
            self.assertEqual(game.applied_filter_enabled,enabled)

    def test_adoption_reads_actual_mode_before_deciding_to_restart(self):
        atomic_json(self.game.directory/'command.json',{'worker_id':'owned','revision':1,'active':False,'updated':self.now})
        atomic_json(self.game.directory/'status.json',{'worker_id':'owned','updated':self.now,
                    'phase':'ready','agent_running':False,'filter_enabled':False})
        self.game.filter_enabled=False;self.game.request(True)
        with patch('saruman.runtime.subprocess.Popen') as spawn:self.game.step()
        self.assertFalse(self.game.restarting)
        self.assertFalse(self.game.applied_filter_enabled)
        self.assertFalse(self.game.read('command.json')['shutdown'])
        spawn.assert_not_called()

    def test_filter_startup_wait_can_be_cancelled_without_a_network_probe(self):
        doctor=load('doctor')
        with patch.object(doctor,'filter_is_ready') as probe:
            self.assertFalse(doctor.wait_for_filter('http://unused',180,cancelled=lambda:True))
        probe.assert_not_called()
        with patch.object(doctor,'filter_is_ready',return_value=False) as probe,patch.object(doctor.time,'sleep') as sleep:
            self.assertFalse(doctor.wait_for_filter('http://unused',180,sleep=sleep,
                             cancelled=Mock(side_effect=[False,True])))
        self.assertEqual(probe.call_count,1)
        sleep.assert_called_once()

    def test_cli_mode_reaches_main_runtime_arguments(self):
        launcher=load('launcher')
        args=SimpleNamespace(auto_start=False,with_social=False,no_stt=False,no_filter=True,no_redis=False)
        self.assertIn('--no-filter',launcher.runtime_args(args))

if __name__=="__main__":unittest.main()
