"""Observe native evidence and bridge explicit game publication requests to Lab."""
import json
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import closing

from .roles import load

ActivityStore = load('activity').ActivityStore
safe_text = load('activity').safe_text


class GameActivity:
    def __init__(self, project, runtime, store, role_database, journal=None, goals_path=None, memory_lock=None):
        self.project, self.runtime, self.store = Path(project), runtime, store
        self.journal = journal or ActivityStore(project)
        self.role_database = Path(role_database)
        self.goals_path = Path(goals_path or self.project/'integrations/saruman/data/automation.sqlite')
        self.closed, self.thread = threading.Event(), None
        self.lock = threading.Lock()
        self.memory_lock = memory_lock or threading.RLock()
        self.last_error = None

    def start(self):
        if self.thread:
            return
        def loop():
            while not self.closed.is_set():
                try:
                    self.tick()
                    self.last_error = None
                except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
                    if self.last_error != type(error).__name__:
                        self.journal.record('observer.error', status='error', details={'error': type(error).__name__})
                    self.last_error = type(error).__name__
                self.closed.wait(2)
        self.thread = threading.Thread(target=loop, name='game-activity', daemon=True)
        self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(3)

    def tick(self):
        if not self.lock.acquire(False):
            return
        try:
            self.journal.import_intents(self.role_database)
            with self.memory_lock:
                self.journal.sync_memory()
            self.publications()
        finally:
            self.lock.release()

    def update_goal(self, identifier, **fields):
        with closing(sqlite3.connect(self.goals_path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM goals WHERE id=?', (identifier,)).fetchone()
            if not row:
                return
            goal = json.loads(row[0])
            if goal['state'] in {'cancel_requested','cancelled'} and fields.get('state') != 'cancelled':
                return
            goal.update(fields, updated=time.time())
            db.execute('UPDATE goals SET state=?,updated=?,body=? WHERE id=?',
                       (goal['state'], goal['updated'], json.dumps(goal, ensure_ascii=False), identifier))
        self.journal.record('goal.result', status=goal['state'], trace=goal.get('context_id'), details={'goal': goal})

    def publications(self):
        if not self.goals_path.is_file():
            return
        with closing(sqlite3.connect(self.goals_path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            rows = db.execute("SELECT body FROM goals WHERE state NOT IN ('completed','failed','cancelled','uncertain')").fetchall()
        for row in rows:
            goal = json.loads(row[0])
            if goal['kind'] != 'telegram':
                continue
            flow = self.runtime.workflows
            identifier = 'minecraft-'+goal['id']
            value = flow.get(identifier)
            if goal['state'] == 'cancel_requested':
                if not flow.lock.acquire(False):
                    continue  # A slow caption must not freeze observation/memory.
                try:
                    value = flow.get(identifier)
                    post = next((step.get('result',{}).get('post_id') for step in (value or {}).get('steps',[])
                                 if step['operation']=='draft'), None)
                    observed = None
                    if post:
                        with self.store.db() as db:
                            observed = db.execute('SELECT status,message_id FROM posts WHERE id=?', (post,)).fetchone()
                        if observed and observed['status']=='sending':
                            continue  # Delivery outcome is still being observed.
                        if observed and observed['status'] in {'draft','queued','failed'}:
                            self.store.action(post, 'cancel')
                    if value and value['status'] not in {'completed','failed','cancelled'}:
                        value.update(status='cancelled', detail='Публикация отменена'); flow.save(value)
                    self.update_goal(goal['id'], state='cancelled', reason='Публикация отменена',
                        publication_status=observed['status'] if observed else 'not_sent',
                        message_id=observed['message_id'] if observed else None)
                finally:
                    flow.lock.release()
                continue
            if value is None:
                if not self.runtime.active:
                    continue
                if goal.get('include_photo'):
                    state = self.runtime.game.read('status.json')
                    if not state.get('world_ready'):
                        continue
                    if state.get('world_session') != goal['world']:
                        self.update_goal(goal['id'], state='failed', reason='Мир изменился до снимка; чужой мир не публикуется')
                        continue
                steps = ([{'operation':'screenshot','text':''}] if goal.get('include_photo') else [])
                steps += [{'operation':'draft','text':goal['topic']}, {'operation':'publish','text':''}]
                value = flow.create(identifier, steps, goal['topic'], context_id=goal.get('context_id'),
                                    world_session=goal['world'] if goal.get('include_photo') else None)
                self.journal.record('telegram.request', status='queued', trace=goal.get('context_id'),
                    details={'workflow':identifier, 'topic':goal['topic'], 'include_photo':goal.get('include_photo',False)},
                    key='publication:'+goal['id'])
                self.update_goal(goal['id'], state='running', workflow_id=identifier, reason='Подготовка публикации в Lab')
            if value['status'] == 'completed':
                publication = next(step['result'] for step in value['steps'] if step['operation']=='publish')
                if not publication.get('message_id'):
                    self.update_goal(goal['id'], state='failed', reason='Не найдено подтверждение Telegram с message_id')
                    continue
                self.update_goal(goal['id'], state='completed', completed=1,
                    reason='Telegram подтвердил публикацию', message_id=publication.get('message_id'),
                    post_id=publication.get('post_id'), publication_status='sent')
            elif value['status'] in {'failed','cancelled'}:
                self.update_goal(goal['id'], state='failed', reason=value.get('detail','Публикация не подтверждена'))

    def raw_log(self, offset=None, size=48*1024):
        path = self.project/'.saruman/runtime/minecraft/minecraft.log'
        if not path.is_file():
            return {'text':'Журнал Minecraft пока пуст', 'offset':0, 'next_offset':0, 'size':0}
        length = path.stat().st_size
        size = max(1024, min(int(size), 128*1024))
        start = max(0, length-size) if offset is None else max(0, min(int(offset), length))
        with path.open('rb') as file:
            file.seek(start)
            if start:
                file.readline()
            start = file.tell(); raw = file.read(size); end = file.tell()
        return {'text':safe_text(raw.decode('utf-8', errors='replace')), 'offset':start, 'next_offset':end, 'size':length}
