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
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'telegram.sqlite')
        self.results={};self.game=Mock();self.game.read.side_effect=lambda name:self.results.get(name,{})
        self.runtime=SimpleNamespace(active=True,lock=threading.RLock(),game=self.game,connection=Mock())
        self.writer=Mock(return_value={'text':'Дерево выросло.'})
        self.capture=Mock(side_effect=lambda identifier:self.store.save_photo(png()))
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

    def test_stop_and_reconstruction_keep_checkpoints_without_repeating_completed_actions(self):
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
        connection.configuration.return_value=config;connection.last_request_id=None
        def request(**kw):
            connection.last_request_id=kw['identifier'];connection.value={'status':'starting'}
        connection.request.side_effect=request
        self.flow.create('join',[{'operation':'connect','text':'test_3'}],'Войди в мир test_3')
        self.flow.step();self.flow.step();self.assertEqual(connection.request.call_count,1)
        connection.value={'status':'running'}
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
