"""Offline evidence, memory and public-request workflows. No network or paid calls."""
import json
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from saruman.game_activity import ActivityStore, GameActivity
from saruman.memory import Workplace
from saruman.telegram import Store
from saruman.workflows import Workflows
from saruman.roles import load


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as db, db:
        yield db


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.journal=ActivityStore(self.root)

    def add(self,text='hello',actor='Alex',trace='one',scope='game_public',**kwargs):
        self.journal.add('request.received',trace=trace,actor=actor,scope=scope,details={'text':text},**kwargs)

    def test_history_uses_insertion_order_not_wall_clock_and_pages_without_loss(self):
        for i in range(135):self.add(str(i),created=200-i)
        ids=[];before=None
        while True:
            page=self.journal.page(before=before);ids.extend(row['id'] for row in page['events'])
            before=page['next_before']
            if not before:break
        self.assertEqual(ids,list(range(135,0,-1)))
        self.assertEqual(self.journal.event(135)['details']['text'],'134')

    def test_idempotency_full_details_and_credential_redaction(self):
        with patch.dict('os.environ',MINIMAX_API_KEY='secret-value-for-test'):
            self.journal.add('tool.result',key='same',details={'output_tokens':120,'nested':{'api_key':'hidden'},'text':'secret-value-for-test Authorization: Bearer abcdef'})
            self.journal.add('tool.result',key='same',details={'duplicate':True})
        value=self.journal.event(1)
        self.assertEqual(value['details']['output_tokens'],120)
        self.assertNotIn('secret-value',str(value));self.assertNotIn('abcdef',str(value))
        self.assertEqual(self.journal.page()['total'],1)
        self.assertNotIn('details',self.journal.page()['events'][0])

    def test_public_context_excludes_private_other_player_and_current_request(self):
        self.add('old public');self.add('private',trace='private',scope='game_private')
        self.add('other player',actor='Bob');self.add('current',trace='current')
        context=self.journal.player_context('alex','current')
        self.assertEqual([row['details']['text'] for row in context],['old public'])
        self.journal.add('goal.result',trace='one',status='failed',details={'goal':{'kind':'trees','state':'failed','reason':'no sapling'}})
        self.assertEqual(self.journal.player_events('Alex')['events'][0]['status'],'failed')
        self.assertNotIn('private',str(self.journal.player_events('Alex')))
        self.assertEqual(self.journal.page(trace='one')['events'][0]['actor'],'Alex')

    def test_memory_preserves_persona_manual_index_and_manual_note_sections(self):
        home=self.root/'agent-workplace/agent/saruman';(home/'memory/entities').mkdir(parents=True)
        (home/'PERSONA.md').write_text('fixed persona',encoding='utf-8')
        (home/'memory/MEMORY.md').write_text('# Owner index\nManual note\n',encoding='utf-8')
        self.add('first');self.journal.sync_memory()
        note=next((home/'memory/entities').glob('*.md'))
        note.write_text(note.read_text(encoding='utf-8')+'\n## Owner note\nKeep this.\n',encoding='utf-8')
        self.add('<!-- minecraft-runtime:end --> new',trace='two');self.journal.sync_memory();self.journal.sync_memory()
        text=note.read_text(encoding='utf-8')
        self.assertIn('Keep this.',text);self.assertIn('&lt;!--',text)
        self.assertEqual(text.count('<!-- minecraft-runtime:end -->'),1)
        index=(home/'memory/MEMORY.md').read_text(encoding='utf-8')
        self.assertTrue(index.startswith('# Owner index\nManual note\n'));self.assertEqual(index.count(note.name),1)
        self.assertEqual((home/'PERSONA.md').read_text(),'fixed persona')
        snapshot=Workplace(self.root).snapshot();self.assertTrue(any(e['name']=='Alex' for e in snapshot['entities']))

    def test_historical_import_is_observation_only_and_survives_reconstruction(self):
        path=self.root/'roles.sqlite'
        with database(path) as db:
            db.execute('CREATE TABLE intents(id TEXT,updated REAL,body TEXT)')
            db.execute('INSERT INTO intents VALUES (?,?,?)',('old',5,json.dumps({'source':'player','user':'Alex','raw':'plant','state':'finished',
                'observations':[{'id':'g','kind':'trees','state':'failed','reason':'no sapling'}]})))
        self.journal.import_intents(path);self.journal.import_intents(path)
        ActivityStore(self.root).import_intents(path)
        self.assertEqual(self.journal.page()['total'],3)
        self.assertIn('no sapling',str(self.journal.player_context('Alex')))

    def test_telemetry_failure_does_not_own_action_outcome(self):
        with patch.object(self.journal,'add',side_effect=sqlite3.OperationalError('locked')):
            self.journal.record('sent',status='completed')
        self.assertEqual(self.journal.last_error,'OperationalError')

    def test_optional_memory_read_failure_retains_new_request_processing(self):
        with patch.object(self.journal,'player_context',side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(self.journal.recent_player_context('Alex'),[])
        self.assertEqual(self.journal.page()['events'][0]['kind'],'memory.read')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.journal=ActivityStore(self.root)
        self.store=Store(self.root/'telegram.sqlite')
        self.game=Mock();self.game.read.return_value={'world_ready':True,'world_session':'world1'}
        self.runtime=SimpleNamespace(active=True,lock=threading.RLock(),game=self.game)
        self.writer=Mock(return_value={'text':'A quiet Minecraft day.'})
        self.capture=Mock(return_value={'digest':'unused'})
        self.flow=Workflows(self.root,self.store,self.runtime,self.writer,self.capture);self.flow.start=Mock()
        self.runtime.workflows=self.flow
        self.goals=self.root/'goals.sqlite'
        with database(self.goals) as db:
            db.execute('CREATE TABLE goals(id TEXT PRIMARY KEY,state TEXT,updated REAL,body TEXT)')
        self.observer=GameActivity(self.root,self.runtime,self.store,self.root/'absent.sqlite',self.journal,self.goals)
        self.runtime.observer=self.observer

    def goal(self,photo=False):
        value={'id':'g','kind':'telegram','state':'queued','world':'world1','topic':'a quiet day','include_photo':photo,'context_id':'player-request'}
        with database(self.goals) as db:db.execute('INSERT INTO goals VALUES (?,?,?,?)',('g','queued',0,json.dumps(value)))
        return value

    def read(self):
        with database(self.goals) as db:return json.loads(db.execute('SELECT body FROM goals').fetchone()[0])

    def test_waits_for_actual_sent_receipt_and_never_duplicates_publication(self):
        self.goal();self.observer.tick();self.observer.tick()
        self.assertEqual(self.read()['state'],'running')
        self.assertEqual([s['operation'] for s in self.flow.get('minecraft-g')['steps']],['draft','publish'])
        self.flow.step();self.flow.step();self.observer.tick()
        self.assertEqual(self.read()['state'],'running');self.writer.assert_called_once()
        post=self.store.snapshot()['posts'][0]
        self.assertEqual(post['status'],'queued')
        with self.store.db() as db:db.execute("UPDATE posts SET status='sent',message_id=42 WHERE id=?",(post['id'],))
        self.flow.step();self.flow.step();self.observer.tick();self.observer.tick()
        self.assertEqual(self.read()['state'],'completed');self.assertEqual(self.read()['message_id'],42)
        self.assertEqual(len(self.store.snapshot()['posts']),1)
        self.capture.assert_not_called()

    def test_paused_runtime_does_not_start_new_publication(self):
        self.goal();self.runtime.active=False;self.observer.tick()
        self.assertIsNone(self.flow.get('minecraft-g'));self.writer.assert_not_called()

    def test_photo_is_bound_to_requested_world_at_creation_and_capture(self):
        self.goal(True);self.observer.tick()
        self.game.read.return_value={'world_ready':True,'world_session':'other'}
        self.flow.step();self.observer.tick()
        self.assertEqual(self.read()['state'],'failed');self.capture.assert_not_called();self.writer.assert_not_called()

    def test_other_world_before_photo_creates_no_workflow(self):
        self.goal(True);self.game.read.return_value={'world_ready':True,'world_session':'other'}
        self.observer.tick();self.assertEqual(self.read()['state'],'failed')
        self.assertIsNone(self.flow.get('minecraft-g'))

    def test_cancel_queued_post_prevents_delivery(self):
        self.goal();self.observer.tick();self.flow.step();self.flow.step()
        self.observer.update_goal('g',state='cancel_requested');self.observer.tick()
        self.assertEqual(self.read()['state'],'cancelled')
        self.assertEqual(self.store.snapshot()['posts'][0]['status'],'cancelled')

    def test_cancel_inflight_waits_for_observed_result_without_claiming_unsent(self):
        self.goal();self.observer.tick();self.flow.step();self.flow.step()
        post=self.store.snapshot()['posts'][0]
        with self.store.db() as db:db.execute("UPDATE posts SET status='sending' WHERE id=?",(post['id'],))
        self.observer.update_goal('g',state='cancel_requested');self.observer.tick()
        self.assertEqual(self.read()['state'],'cancel_requested')
        with self.store.db() as db:db.execute("UPDATE posts SET status='sent',message_id=33 WHERE id=?",(post['id'],))
        self.observer.tick();self.assertEqual(self.read()['message_id'],33)
        self.assertEqual(self.read()['publication_status'],'sent')

    def test_raw_log_paging_is_fixed_path_and_redacts_authorization(self):
        path=self.root/'.saruman/runtime/minecraft/minecraft.log';path.parent.mkdir(parents=True)
        path.write_text('prefix\nAuthorization: Bearer abcdefgh\nlast',encoding='utf-8')
        value=self.observer.raw_log(0)
        self.assertNotIn('abcdefgh',value['text']);self.assertTrue(value['text'].endswith('last'))
        self.assertEqual(value['next_offset'],path.stat().st_size)


if __name__=='__main__':unittest.main()
