"""Offline chains and media delivery. No model, desktop capture or real Telegram traffic."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zlib

from saruman.admin import Admin,named_world
from saruman.roles import load
from saruman.telegram import Store
from saruman.workflows import Workflows
from _saruman_telegram.gateway import Publisher,Settings,Server,Telegram,TelegramError
from _saruman_telegram.automation import Control,Director
from _saruman_telegram.automation import record_goal
from _saruman_telegram.client import call
from _saruman_telegram.media import inspect_png,decode_photo


def png(red=0):
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(bytes([0,red,0,0,red,0,0])*2))+chunk(b'IEND',b'')


class WorkflowTests(unittest.TestCase):
    def test_native_recovery_is_pending_not_a_failed_chain_or_a_completed_tree(self):
        value=self.chain();identifier=value['steps'][0]['id']
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'accepted','request_state':'waiting',
            'facts':{'goals':[{'kind':'trees','state':'interrupted','completed':0,'reason':'scheduler recovery'}]}}
        self.flow.step()
        self.assertEqual(self.flow.get('command')['status'],'running')
        self.capture.assert_not_called();self.writer.assert_not_called()
        self.complete_game();self.flow.step();self.flow.step()
        self.capture.assert_called_once()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'telegram.sqlite')
        self.results={};self.game=Mock();self.game.value={};self.game.read.side_effect=lambda name:self.results.get(name,{})
        self.runtime=SimpleNamespace(active=True,lock=threading.RLock(),game=self.game,connection=Mock())
        self.writer=Mock(return_value={'text':'Дерево выросло.'})
        self.capture=Mock(side_effect=lambda identifier, **kwargs:self.store.save_photo(png()))
        self.flow=self.new_flow()

    def new_flow(self):
        flow=Workflows(self.root,self.store,self.runtime,self.writer,self.capture)
        flow.start=Mock()  # Drive actual state transitions deterministically.
        return flow

    def chain(self):
        steps=[{'operation':op,'text':text} for op,text in [('game_task','Посади дерево'),('screenshot',''),('draft',''),('publish','')]]
        return self.flow.create('command',steps,'Посади дерево и опубликуй пост со скриншотом')

    def complete_game(self):
        identifier=self.flow.get('command')['steps'][0]['id']
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'completed',
            'facts':{'goals':[{'kind':'trees','completed':1,'state':'completed','world':'secret','context_id':'private','coordinates':[1,2,3]}]}}

    def admit_build_chain(self, *, world_name='New World'):
        adapter = Mock()
        adapter.chat_target.side_effect = lambda text: 'mage_home' if text.startswith('построй') else ''
        adapter.state.return_value = (True,{})
        adapter.journal = load('activity').ActivityStore(self.root/'activity.sqlite')
        adapter.instance.return_value.call.return_value = {'world':{'connected':True,'name':world_name}}
        adapter.entry.return_value.lock = threading.RLock()
        adapter.instance.return_value.photo_subject.return_value = {
            'run_id':'house-run', 'project_id':'house', 'world_uid':'save-id',
            'min':[10, 64, 20], 'max':[20, 75, 30], 'facing':'north',
        }
        self.runtime.workflows = self.flow
        self.runtime.start = Mock()
        self.runtime.snapshot = Mock(return_value={'options':{'minecraft':True}})
        self.runtime.extensions = Mock()
        self.runtime.extensions.operator_handle.return_value = {
            'status':'running','run_id':'house-run','world_uid':'save-id','detail':'Идёт стройка',
        }
        connection = self.runtime.connection
        connection.identifier = None
        connection.configuration.side_effect = lambda *, world=None: {'mode':'world','target':world or 'New World'}
        def request(*, identifier, configuration):
            connection.last_request_id = identifier
            connection.value = {'status':'running','request_id':identifier, **configuration}
        connection.request.side_effect = request
        text = 'зайди в мир New World, построй дом с печью и опубликуй отчёт со скриншотом'
        steps = [{'operation':op,'text':value} for op,value in [
            ('connect','New World'),('adapter_task','построй дом с печью'),
            ('screenshot',''),('draft',''),('publish',''),
        ]]
        intent = Mock(return_value={'operation':'workflow','text':'','steps':steps})
        admin = Admin(self.store,self.runtime,intent,adapter)
        self.flow.admin = admin
        row = admin.execute({'text':text,'request_id':'compound'})
        self.assertEqual(json.loads(row['result'])['workflow_id'],'compound')
        self.results['status.json'] = {'world_ready':True,'world_session':'world-new'}
        return adapter, self.flow.get('compound')['steps'][1]['id']

    def test_repeat_build_text_creates_fresh_join_build_and_publication_ids(self):
        self.admit_build_chain()
        first = self.flow.get('compound')
        text = self.flow.admin.history()[0]['input']
        self.flow.admin.interpret.side_effect = AssertionError('Repeated command must not depend on model history')
        new = self.flow.admin.execute({'text':text,'request_id':'compound-again'})
        retransmit = self.flow.admin.execute({'text':text,'request_id':'compound-again'})
        self.assertEqual(json.loads(new['result'])['workflow_id'], 'compound-again')
        self.assertEqual(json.loads(retransmit['result'])['workflow_id'], 'compound-again')
        second = self.flow.get('compound-again')
        self.assertEqual([step['operation'] for step in first['steps']], [step['operation'] for step in second['steps']])
        self.assertFalse({step['id'] for step in first['steps']} & {step['id'] for step in second['steps']})

    def test_cancelled_build_finishes_old_chain_at_menu(self):
        _, build_id = self.admit_build_chain()
        self.flow.step()
        with self.store.db() as db:
            db.execute("INSERT INTO admin_messages VALUES (?,?,?,'cancelled',0,'mage_home')",
                       (build_id, 'построй дом с печью', json.dumps({'detail':'Отменено'})))
        self.results['status.json'] = {'world_ready':False}
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'], 'cancelled')
        self.capture.assert_not_called()

    def test_returning_to_menu_requires_explicit_resume_before_entering_again(self):
        adapter, build_id = self.admit_build_chain()
        self.flow.step()
        first_join = self.runtime.connection.last_request_id
        self.results['status.json'] = {'world_ready':False}
        adapter.instance.return_value.call.return_value = {'world':{'connected':False}, 'ui':{'screen':'class_442'}}
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'], 'paused')
        self.assertEqual(self.flow.get('compound')['steps'][0]['status'], 'completed')
        self.flow.step()
        self.runtime.connection.request.assert_called_once()
        self.flow.resume('compound')
        self.flow.step()
        self.assertNotEqual(self.runtime.connection.last_request_id, first_join)
        self.assertEqual(self.runtime.connection.request.call_args.kwargs['configuration']['target'], 'New World')
        self.assertEqual(self.flow.get('compound')['steps'][1]['id'], build_id)
        self.runtime.extensions.operator_handle.assert_not_called()

    def test_new_demo_join_is_not_preempted_by_build_workflow_after_leaving_its_world(self):
        adapter, _ = self.admit_build_chain(world_name='build')
        first = self.flow.get('compound')
        first['steps'][0]['configuration']['target'] = 'build'
        self.flow.save(first)
        self.flow.step()
        self.results['status.json'] = {'world_ready':False}
        adapter.instance.return_value.call.return_value = {'world':{'connected':False}, 'ui':{'screen':'class_442'}}
        self.flow.step()
        self.flow.create('demo-request', [{'operation':'connect', 'text':'demo'}], 'Зайди в мир demo')
        for _ in range(3):
            self.flow.step()

        targets = [call.kwargs['configuration']['target'] for call in self.runtime.connection.request.call_args_list]
        self.assertEqual(targets, ['build','demo'])
        self.assertEqual(self.flow.get('demo-request')['status'], 'completed')
        self.assertEqual(self.flow.get('compound')['status'], 'paused')
        self.runtime.extensions.operator_handle.assert_not_called()

    def test_inflight_result_cannot_revive_durably_cancelled_workflow(self):
        self.chain()

        def finish_during_cancellation(*_):
            self.flow.cancel_pending()
            return True, {'facts':{'state':'completed'}}

        with patch.object(self.flow, 'run', side_effect=finish_during_cancellation):
            self.flow.step()
        self.flow = self.new_flow()
        self.flow.step()
        self.assertEqual(self.flow.get('command')['status'], 'cancelled')
        with self.assertRaises(ValueError):
            self.flow.resume('command')
        self.capture.assert_not_called()
        self.writer.assert_not_called()

    def test_cancelled_join_cannot_dispatch_with_a_stale_snapshot_after_start(self):
        self.admit_build_chain(world_name='build')
        stale = self.flow.get('compound')
        self.flow.cancel_pending()
        complete, result = self.flow.run(stale, 0)
        self.assertFalse(complete)
        self.assertEqual(result['status'], 'cancelled')
        self.runtime.connection.request.assert_not_called()

    def test_open_world_and_stale_dispatcher_are_reported_separately(self):
        self.admit_build_chain()
        self.flow.step()
        self.results['status.json'] = {'world_ready':False,'native_fresh':False,'native_error':'Данные устарели'}
        self.flow.step()
        workflow = self.flow.get('compound')
        self.assertIn('Мир «New World» открыт', workflow['detail'])
        self.assertEqual(workflow['steps'][1]['result']['status'], 'waiting_adapter')
        self.runtime.extensions.operator_handle.assert_not_called()

    def test_open_world_keeps_build_pending_and_shows_service_failure(self):
        self.admit_build_chain()
        self.flow.step()
        self.results['status.json'] = {'phase':'waiting_services','reason':'Игровая проверка не пройдена (Redis TCP)'}
        self.game.value = {'status':'retrying','reason':self.results['status.json']['reason']}
        self.flow.step()
        workflow = self.flow.get('compound')
        self.assertEqual(workflow['status'], 'waiting')
        self.assertEqual(workflow['steps'][0]['status'], 'completed')
        self.assertEqual(workflow['steps'][1]['result']['status'], 'waiting_services')
        self.assertIn('Redis TCP', workflow['detail'])
        self.runtime.extensions.operator_handle.assert_not_called()

    def test_build_dispatch_waits_for_the_new_worker_even_if_old_status_was_ready(self):
        _, build_id = self.admit_build_chain()
        self.flow.step()
        self.game.value = {'status':'restarting','reason':'Восстанавливаем игровой обработчик'}
        self.flow.step()
        workflow = self.flow.get('compound')
        self.assertEqual(workflow['steps'][1]['status'], 'waiting')
        self.assertEqual(workflow['detail'], self.game.value['reason'])
        self.runtime.extensions.operator_handle.assert_not_called()
        self.game.value = {'status':'running'}
        self.results['status.json']['world_session'] = 'worker-reconnected'
        self.flow.step()
        self.runtime.extensions.operator_handle.assert_called_once_with(
            'mage_home', 'построй дом с печью', build_id, None, world_session='worker-reconnected')

    def test_world_build_and_report_wait_for_same_build_and_photo(self):
        adapter, build_id = self.admit_build_chain()
        self.flow.step()
        self.runtime.extensions.operator_handle.assert_not_called()
        self.flow.step()
        self.flow.step()
        self.runtime.extensions.operator_handle.assert_called_once_with(
            'mage_home','построй дом с печью',build_id,None,world_session='world-new',
        )
        self.capture.assert_not_called()
        adapter.journal.record('adapter.minecraft.state',trace='admin-'+build_id,status='completed',
                               details={'state':{'runId':'another-house'},'text':'Other house'})
        self.flow.step()
        self.capture.assert_not_called()
        adapter.journal.record('adapter.minecraft.state',trace='admin-'+build_id,status='completed',
                               details={'state':{'runId':'house-run'},'text':'Постройка подтверждена'})
        for _ in range(4):
            self.flow.step()
        self.capture.assert_called_once_with(self.flow.get('compound')['steps'][2]['id'],
                                            focus=adapter.instance.return_value.photo_subject.return_value)
        self.assertEqual(self.writer.call_args.args[0]['confirmed_game_results'],
                         [{'kind':'mage_home','state':'completed','completed':1}])
        post = self.store.snapshot()['posts'][0]
        self.assertEqual(post['status'],'queued')
        with self.store.db() as db:
            db.execute("UPDATE posts SET status='sent',message_id=42 WHERE id=?",(post['id'],))
        self.flow.step()
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'],'completed')

    def test_unframed_house_photo_does_not_generate_report(self):
        adapter, build_id = self.admit_build_chain()
        self.flow.step()
        self.flow.step()
        adapter.journal.record('adapter.minecraft.state', trace='admin-'+build_id, status='completed',
                               details={'state':{'runId':'house-run'}, 'text':'Готово'})
        self.capture.side_effect = RuntimeError('Дом закрыт рельефом.')
        for _ in range(4):
            self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'], 'failed')
        self.writer.assert_not_called()
        self.assertEqual(self.store.snapshot()['posts'], [])

    def test_compound_build_refuses_other_save_before_start(self):
        self.admit_build_chain(world_name='New World (1)')
        self.flow.step()
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'],'failed')
        self.runtime.extensions.operator_handle.assert_not_called()
        self.capture.assert_not_called()
        self.writer.assert_not_called()

    def test_paused_build_keeps_the_same_run_across_a_reconnect(self):
        adapter, build_id = self.admit_build_chain()
        self.runtime.extensions.operator_handle.return_value['world_uid'] = 'save-id'
        self.flow.step()
        self.flow.step()
        adapter.journal.record('adapter.minecraft.state', trace='admin-'+build_id, status='paused',
                               details={'state':{'runId':'house-run'}, 'text':'Ждёт восстановления модели'})
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['status'], 'waiting')
        self.results['status.json'] = {'world_ready':True, 'world_session':'rejoined'}
        world = adapter.instance.return_value.call.return_value['world']
        world['worldUid'] = 'different-save'
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['world_session'], 'world-new')
        world['worldUid'] = 'save-id'
        self.flow.step()
        self.assertEqual(self.flow.get('compound')['world_session'], 'rejoined')
        self.runtime.extensions.operator_handle.assert_called_once()
        self.capture.assert_not_called()
        adapter.journal.record('adapter.minecraft.state', trace='admin-'+build_id, status='completed',
                               details={'state':{'runId':'house-run'}, 'text':'Готово'})
        self.flow.step()
        self.flow.step()
        self.capture.assert_called_once()

    def test_chain_waits_for_exact_observed_completion_and_delivery(self):
        value=self.chain();identifier=value['steps'][0]['id'];self.flow.step()
        self.assertEqual(self.game.directive['id'],identifier)
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'accepted'}
        self.flow.step();self.capture.assert_not_called()
        self.results['results/'+identifier+'.json']={'command_id':'other','status':'completed'}
        self.flow.step();self.capture.assert_not_called()
        self.complete_game()
        for _ in range(5):self.flow.step()
        post=self.store.snapshot()['posts'][0]
        self.assertEqual(post['status'],'queued');self.assertEqual(self.flow.get('command')['status'],'running')
        self.assertNotIn('secret',str(self.writer.call_args));self.assertNotIn('coordinates',str(self.writer.call_args))
        self.assertEqual(self.writer.call_args.args[0]['confirmed_game_results'][0]['completed'],1)
        with self.store.db() as db:db.execute("UPDATE posts SET status='sent',message_id=42 WHERE id=?",(post['id'],))
        self.flow.step();self.flow.step()
        self.assertEqual(self.flow.get('command')['status'],'completed')
        self.assertEqual(self.flow.get('command')['steps'][-1]['result']['message_id'],42)

    def test_failure_preserves_evidence_and_prevents_success_post(self):
        value=self.chain();identifier=value['steps'][0]['id']
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'unresolved','facts':{'reason':'no sapling'}}
        for _ in range(4):self.flow.step()
        self.assertEqual(self.flow.get('command')['status'],'failed')
        self.assertIn('no sapling',str(self.flow.get('command')))
        self.assertIn('no sapling',self.flow.get('command')['detail'])
        self.capture.assert_not_called();self.writer.assert_not_called()

    def test_exact_failed_goal_reason_reaches_the_chain_summary(self):
        value=self.chain();identifier=value['steps'][0]['id']
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'unresolved','request_state':'finished',
            'facts':{'goals':[{'state':'completed','reason':'unrelated earlier success'},
                              {'state':'failed','reason':'native acquisition timed out'}]}}
        self.flow.step()
        self.assertEqual(self.flow.get('command')['detail'],'native acquisition timed out')
        self.capture.assert_not_called();self.writer.assert_not_called()

    def test_temporary_suspension_keeps_checkpoints_after_reconstruction(self):
        self.chain();self.complete_game();self.flow.step();self.flow.step()
        self.runtime.active=False
        self.flow.step();self.assertEqual(self.flow.get('command')['status'],'waiting_start')
        self.writer.assert_not_called()
        self.flow=self.new_flow();self.runtime.active=True
        self.flow.step()
        self.assertEqual(self.capture.call_count,1);self.assertEqual(self.writer.call_count,1)
        self.assertEqual(self.flow.get('command')['steps'][0]['status'],'completed')

    def test_draft_write_crash_window_does_not_regenerate_or_duplicate_post(self):
        self.chain();self.complete_game()
        for _ in range(3):self.flow.step()
        value=self.flow.get('command');value['steps'][2]['status']='running';value['steps'][2]['result']={}
        value['steps'][2].pop('post_id',None);self.flow.save(value)
        self.flow=self.new_flow();self.flow.step()
        self.assertEqual(self.writer.call_count,1);self.assertEqual(len(self.store.snapshot()['posts']),1)

    def test_photo_failure_does_not_queue_post(self):
        self.chain();self.complete_game();self.flow.step()
        self.capture.side_effect=RuntimeError('World is not open');self.flow.step()
        self.assertEqual(self.flow.get('command')['status'],'failed');self.writer.assert_not_called()

    def test_invalid_order_and_unauthorized_payload_rejected_before_side_effects(self):
        for steps in [[],[{'operation':'publish','text':''}],[{'operation':'shell','text':'whoami'}],
                      [{'operation':'screenshot','text':'C:/private/file.png'}],
                      [{'operation':'game_task','text':'','recipient':'someone'}]]:
            with self.subTest(steps=steps),self.assertRaises(ValueError):self.flow.create('bad',steps,'message')
        self.game.heartbeat.assert_not_called()

    def test_compound_world_command_goes_to_interpreter(self):
        self.assertIsNone(named_world('Войди в мир test_3, посади дерево'))
        self.assertIsNone(named_world('Войди в мир test_3 и посади дерево'))
        self.assertEqual(named_world('Войди в мир «Мир и лес»'),'Мир и лес')

    def test_resume_keeps_one_existing_workflow_and_completed_steps(self):
        value=self.chain();self.complete_game();self.flow.step()
        self.runtime.workflows=self.flow;self.runtime.start=Mock();self.runtime.snapshot=Mock(return_value={})
        interpret=Mock(return_value={'operation':'resume','text':value['id'],'steps':[]})
        admin=Admin(self.store,self.runtime,interpret)
        admin.execute({'request_id':'followup','text':'Я уже в мире, приступай к задаче'})
        payload=interpret.call_args.args[0]
        self.assertEqual(payload['pending_workflows'][0]['steps'][0]['status'],'completed')
        self.assertEqual(payload['pending_workflows'][0]['id'],value['id'])
        self.assertEqual(self.flow.get(value['id'])['steps'][0]['status'],'completed')
        self.assertEqual(self.flow.get(value['id'])['steps'][0]['id'],value['steps'][0]['id'])
        self.assertEqual(self.flow.context()[0]['id'],value['id'])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM workflows').fetchone()[0],1)
        self.game.heartbeat.assert_not_called()
        self.runtime.connection.request.assert_not_called()
        self.assertEqual(json.loads(admin.history()[0]['result'])['workflow_id'],value['id'])

    def test_resume_never_reopens_a_failed_or_completed_workflow(self):
        value=self.chain();value['status']='failed';self.flow.save(value)
        with self.assertRaises(ValueError):self.flow.resume(value['id'])
        self.assertEqual(self.flow.context(),[])

    def test_admin_compound_command_is_idempotent_and_history_tracks_steps(self):
        self.runtime.workflows=self.flow;self.runtime.start=Mock();self.runtime.snapshot=Mock(return_value={'options':{'minecraft':True}})
        interpret=Mock(return_value={'operation':'workflow','text':'','steps':[{'operation':'screenshot','text':''}]})
        admin=Admin(self.store,self.runtime,interpret)
        payload={'request_id':'owner','text':'Сделай скриншот'}
        admin.execute(payload);admin.execute(payload);self.assertEqual(interpret.call_count,1)
        self.flow.step();self.flow.step()
        self.assertEqual(admin.history()[0]['status'],'completed')
        self.assertEqual(self.capture.call_count,1)

    def test_per_command_native_journal_does_not_erase_completion(self):
        managed=load('managed');directory=self.root/'ipc'
        managed.admin_result(directory,{'command_id':'one','status':'completed','facts':{'goals':[]}})
        managed.admin_result(directory,{'command_id':'one','status':'accepted'})
        managed.admin_result(directory,{'command_id':'two','status':'accepted'})
        first=json.loads((directory/'results/one.json').read_text())
        self.assertEqual(first['status'],'completed')
        self.assertEqual(json.loads((directory/'admin-result.json').read_text())['command_id'],'two')

    def test_workflow_game_event_is_reserved_for_its_post_not_scheduled_again(self):
        value=self.chain();identifier=value['steps'][0]['id']
        previous={'kind':'trees','run_id':'native-run','world':'world','completed':0,'context_id':'admin-'+identifier}
        record_goal(previous,{'completed':1,'state':'completed'},self.store)
        event=self.store.snapshot()['events'][0]
        self.assertEqual(event['post_id'],'chain-'+value['steps'][2]['id'])
        self.assertEqual(self.store.snapshot()['pending_events'],0)

    def test_cancelled_or_unsubstantiated_game_results_do_not_leave_chain_running(self):
        value=self.chain();identifier=value['steps'][0]['id']
        self.results['results/'+identifier+'.json']={'command_id':identifier,'status':'completed'}
        self.flow.step();self.assertEqual(self.flow.get('command')['status'],'failed')
        self.capture.assert_not_called()

    def test_connect_step_polls_same_request_and_preserves_config_across_restart(self):
        connection=self.runtime.connection
        config={'mode':'world','target':'test_3','launcher':'','arguments':[]}
        connection.configuration.return_value=config;connection.last_request_id=None;connection.identifier=None
        def request(**kw):
            connection.last_request_id=kw['identifier'];connection.value={'status':'starting'}
        connection.request.side_effect=request
        self.flow.create('join',[{'operation':'connect','text':'test_3'}],'Войди в мир test_3')
        self.flow.step();self.flow.step();self.assertEqual(connection.request.call_count,1)
        identifier = self.flow.get('join')['steps'][0]['id']
        connection.value={'status':'running','request_id':identifier,'mode':'world','target':'another'}
        self.flow.step();self.assertEqual(self.flow.get('join')['steps'][0]['status'],'waiting')
        connection.value={'status':'running','request_id':'another-request','mode':'world','target':'test_3'}
        self.flow.step();self.assertEqual(self.flow.get('join')['steps'][0]['status'],'waiting')
        connection.value={'status':'running','request_id':identifier,'mode':'world','target':'test_3'}
        self.flow.step();self.assertEqual(self.flow.get('join')['steps'][0]['status'],'completed')
        self.assertEqual(connection.request.call_args.kwargs['configuration'],config)

    def test_deployment_script_is_valid_python_and_never_changes_env(self):
        import ast,importlib.util
        path=Path(__file__).resolve().parents[1]/'integrations/telegram-gateway/update_gateway.py'
        spec=importlib.util.spec_from_file_location('gateway_updater',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        script=module.remote_script(path.parent);ast.parse(script)
        self.assertIn("for name,source in files.items()",script)
        self.assertIn("('gateway.py'",path.read_text())


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'telegram.sqlite')
        self.photo=self.store.save_photo(png());self.payload=self.store.photo_payload(self.photo)
        self.telegram=Mock();self.telegram.call.return_value={'message_id':42}
        self.publisher=Publisher(Settings('123:fake','-100123','a'*40,self.root/'gateway.sqlite'),self.telegram)

    def test_real_http_photo_upload_lost_response_reconcile_and_immutable_id(self):
        server=Server(('127.0.0.1',0),self.publisher);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        url='http://127.0.0.1:'+str(server.server_port)
        self.store.configure({'enabled':True});identifier=self.store.workflow_post('photo-post','Дерево',self.photo)
        self.store.action(identifier,'queue');control=Control(self.store)
        control.deliver(url,'a'*40,self.store.clock())
        self.assertEqual(self.store.snapshot()['posts'][0]['status'],'sent')
        self.assertEqual(self.telegram.call.call_args.args[0],'sendPhoto')
        self.assertEqual(self.telegram.call.call_args.args[1]['_photo_bytes'],png())
        with self.store.db() as db:db.execute("UPDATE posts SET status='sending',next_try=0 WHERE id=?",(identifier,))
        control.deliver(url,'a'*40,self.store.clock());self.assertEqual(self.telegram.call.call_count,1)
        code,_=call(url,'a'*40,'POST','/v1/posts',{'publication_id':identifier,'text':'changed','photo':self.payload})
        self.assertEqual(code,409)

    def test_old_gateway_blocks_only_photo_before_any_post(self):
        self.store.configure({'enabled':True});self.store.workflow_post('photo','caption',self.photo);self.store.action('photo','queue')
        transport=Mock(side_effect=[(404,{}),(200,{'api_version':3})]);control=Control(self.store,transport=transport)
        control.deliver('http://127.0.0.1:8787','key',self.store.clock())
        self.assertTrue(all(c.args[2]=='GET' for c in transport.call_args_list))
        row=self.store.snapshot()['posts'][0]
        self.assertEqual(row['status'],'queued');self.assertEqual(row['error'],'gateway_upgrade_required')

    def test_unknown_photo_is_never_retried_and_failed_retry_retains_photo(self):
        payload={'publication_id':'unknown','text':'caption','photo':self.payload}
        self.telegram.call.side_effect=TelegramError('connection',uncertain=True)
        self.publisher.publish(payload);self.publisher.retry('unknown');self.assertEqual(self.telegram.call.call_count,1)
        self.telegram.call.side_effect=TelegramError('bad_request')
        self.publisher.publish({**payload,'publication_id':'failed'})
        self.telegram.call.side_effect=None;self.publisher.retry('failed')
        self.assertEqual(self.telegram.call.call_args.args[0],'sendPhoto')
        self.assertEqual(self.telegram.call.call_args.args[1]['_photo_bytes'],png())

    def test_corrupt_encoding_hash_file_and_caption_rejected(self):
        for photo in [{'data':'not_base64','sha256':'x'},{**self.payload,'sha256':'0'*64}]:
            with self.assertRaises(ValueError):decode_photo(photo)
        with self.assertRaises(ValueError):inspect_png(png()[:-1])
        with self.assertRaises(ValueError):self.store.media_path('../secret')
        with self.assertRaises(ValueError):self.store.workflow_post('too-long','x'*1025,self.photo)
        with self.assertRaises(ValueError):self.publisher.publish({'publication_id':'too-long','text':'x'*1025,'photo':self.payload})
        self.store.media_path(self.photo['sha256']).write_bytes(png(20))
        with self.assertRaises(ValueError):self.store.photo_payload(self.photo)
        self.telegram.call.assert_not_called()

    def test_actual_multipart_encoder_keeps_binary_and_caption(self):
        telegram=Telegram('secret');response=Mock();response.read.return_value=b'{"ok":true,"result":{"message_id":42}}'
        telegram.opener=Mock();telegram.opener.open.return_value.__enter__=Mock(return_value=response)
        telegram.opener.open.return_value.__exit__=Mock(return_value=False)
        telegram.call('sendPhoto',{'chat_id':'-100123','caption':'Дерево','_photo_bytes':png()})
        request=telegram.opener.open.call_args.args[0]
        self.assertIn('multipart/form-data',request.headers['Content-type'])
        self.assertIn(png(),request.data);self.assertIn('Дерево'.encode(),request.data)
        self.assertNotIn(b'_photo_bytes',request.data)


if __name__=='__main__':unittest.main()
