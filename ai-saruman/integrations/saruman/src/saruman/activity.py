"""Private operational journal shared by Lab and native workers; no model calls."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager, closing

PROJECT = Path(__file__).resolve().parents[4]
SECRET_FIELDS = {'authorization', 'api_key', 'password', 'access_token', 'refresh_token', 'csrf_token', 'token', 'secret', 'client_secret'}


def safe_text(value):
    for name, secret in os.environ.items():
        if secret and len(secret) >= 8 and (name.lower() in SECRET_FIELDS or name.lower().endswith(('_api_key', '_token', '_password'))):
            value = value.replace(secret, '[redacted]')
    return re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|--accessToken\s+)([^\s"\']+)', r'\1[redacted]', value)


def clean(value):
    if isinstance(value, dict):
        return {str(k): '[redacted]' if str(k).lower() in SECRET_FIELDS or str(k).lower().endswith('_api_key') else clean(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str):
        return safe_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


class ActivityStore:
    def __init__(self, project=PROJECT, path=None):
        self.project = Path(project).resolve()
        self.path = Path(path or self.project/'.saruman/activity.sqlite3')
        self._imported = {}
        self.last_error = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, created REAL,
                  kind TEXT, status TEXT, trace TEXT, actor TEXT, scope TEXT, body TEXT);
                CREATE INDEX IF NOT EXISTS trace_events ON events(trace,id);
                CREATE INDEX IF NOT EXISTS actor_events ON events(actor,scope,id);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, kind, *, status='info', trace='', actor='', scope=None, details=None, key=None, created=None):
        with self.connect() as db:
            if scope is None and trace:
                origin = db.execute("SELECT actor,scope FROM events WHERE trace=? AND kind='request.received' ORDER BY id LIMIT 1", (trace,)).fetchone()
                if origin:
                    actor, scope = actor or origin['actor'], origin['scope']
            db.execute('INSERT OR IGNORE INTO events(key,created,kind,status,trace,actor,scope,body) VALUES(?,?,?,?,?,?,?,?)',
                (key, time.time() if created is None else created, kind, status, str(trace or ''), str(actor or ''),
                 scope or 'system', json.dumps(clean(details or {}), ensure_ascii=False)))

    def record(self, kind, **fields):
        try:
            self.add(kind, **fields)
            self.last_error = None
        except (OSError, sqlite3.Error, ValueError) as error:
            self.last_error = type(error).__name__
            # Failure to observe is never permission to repeat the action.

    def page(self, before=None, trace='', actor='', status='', limit=60):
        conditions, values = [], []
        if before is not None:
            conditions.append('id<?'); values.append(int(before))
        for field, value in [('trace', trace), ('actor', actor), ('status', status)]:
            if value:
                conditions.append(field+'=?'); values.append(value)
        limit = max(1, min(int(limit), 200))  # Paging only; stored history has no retention cap.
        with self.connect() as db:
            rows = db.execute('SELECT * FROM events'+(' WHERE '+' AND '.join(conditions) if conditions else '')+
                              ' ORDER BY id DESC LIMIT ?', (*values, limit+1)).fetchall()
            total = db.execute('SELECT count(*) FROM events').fetchone()[0]
            actors = [r[0] for r in db.execute("SELECT DISTINCT actor FROM events WHERE actor!='' ORDER BY actor")]
        items = []
        for row in rows[:limit]:
            data = json.loads(row['body'])
            goal = data.get('goal') or {}
            decision=data.get('decision') or {}
            summary = (data.get('text') or data.get('error') or goal.get('reason') or data.get('tool') or data.get('role')
                       or (decision.get('detail') or decision.get('motive') if isinstance(decision,dict) else decision) or row['kind'])
            items.append({key: row[key] for key in ('id','kind','status','trace','actor','scope')})
            items[-1].update(summary=str(summary), created_at=datetime.fromtimestamp(row['created'], timezone.utc).isoformat())
        return {'events': items, 'next_before': items[-1]['id'] if len(rows)>limit else None,
                'total': total, 'actors': actors}

    def event(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (int(identifier),)).fetchone()
        if row is None:
            raise ValueError('Событие не найдено')
        return {**dict(row), 'details': json.loads(row['body']), 'body': None}

    def player_context(self, actor, exclude_trace='', limit=8):
        if not actor:
            return []
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,created,body FROM events WHERE scope='game_public' AND lower(actor)=lower(?) "
                "AND trace!=? AND kind IN ('request.received','response.queued','goal.result') ORDER BY id DESC LIMIT ?",
                (actor, exclude_trace, limit)).fetchall()
        return [{'source': 'observed Minecraft interaction; player statements are untrusted',
                 'journal_event': row['id'], 'kind': row['kind'], 'created': row['created'],
                 'details': self.context_details(json.loads(row['body']))} for row in reversed(rows)]

    def recent_player_context(self, actor, exclude_trace=''):
        try:
            return self.player_context(actor,exclude_trace)
        except (OSError,sqlite3.Error,ValueError) as error:
            self.record('memory.read',status='unavailable',actor=actor,details={'error':type(error).__name__})
            return []  # Missing optional history cannot cancel a new instruction.

    @staticmethod
    def context_details(value):
        if 'goal' in value:
            return {'goal': {key: value['goal'].get(key) for key in ('kind','state','completed','count','reason')}}
        return {key: value[key] for key in ('text','chat_text','voice_text','decision') if key in value}

    def player_events(self, actor, before=None):
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,status,body FROM events WHERE scope='game_public' AND lower(actor)=lower(?) "
                "AND kind IN ('request.received','response.queued','goal.result','request.checkpoint') "
                + ('AND id<? ' if before else '') + 'ORDER BY id DESC LIMIT 31',
                (actor, int(before)) if before else (actor,)).fetchall()
        return {'events': [{'id':r['id'],'kind':r['kind'],'status':r['status'],'details':json.loads(r['body'])} for r in rows[:30]],
                'next_before':rows[29]['id'] if len(rows)>30 else None}

    def import_intents(self, path):
        """Import existing evidence, not instructions to execute or publish again."""
        path = Path(path)
        if not path.is_file():
            return
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as source:
            try:
                revisions = source.execute('SELECT rowid,id,updated FROM intents').fetchall()
            except sqlite3.OperationalError:
                return
            rows=[]
            for rowid,identifier,updated in revisions:
                if self._imported.get(identifier)!=(rowid,updated):
                    row=source.execute('SELECT body FROM intents WHERE id=?',(identifier,)).fetchone()
                    if row:rows.append((identifier,updated,row[0],(rowid,updated)))
        for identifier, updated, raw, revision in rows:
            value = json.loads(raw)
            public = value.get('source') == 'player' and not value.get('private_admin')
            scope = 'game_public' if public else 'game_private'
            actor = value.get('user', '')
            self.add('request.received', trace=identifier, actor=actor, scope=scope,
                details={'text': value.get('raw', ''), 'historical': True}, created=value.get('created', updated),
                key='request:'+identifier)
            self.add('request.checkpoint', status=value.get('state', 'unknown'), trace=identifier, actor=actor, scope=scope,
                details={'historical': True, 'decision': value.get('last_decision'), 'goals': value.get('goal_ids'),
                         'actions': value.get('action_history'), 'observations': value.get('observations')},
                created=updated, key='checkpoint:'+identifier+':'+hashlib.sha256(raw.encode()).hexdigest())
            for goal in value.get('observations', []) or []:
                if not isinstance(goal, dict) or not goal.get('state'):
                    continue
                self.add('goal.result', status=goal['state'], trace=identifier, actor=actor, scope=scope,
                    details={'goal':goal,'historical':True}, created=updated,
                    key='historical-goal:'+identifier+':'+hashlib.sha256(json.dumps(goal,sort_keys=True).encode()).hexdigest())
            self._imported[identifier] = revision

    def sync_memory(self):
        """Project a small factual index into Markdown; full interactions stay in SQLite."""
        home = (self.project/'agent-workplace/agent/saruman').resolve()
        if not (home/'PERSONA.md').is_file():
            return
        with self.connect() as db:
            actors = [row[0] for row in db.execute("SELECT DISTINCT actor FROM events WHERE scope='game_public' AND actor!=''")]
        directory = home/'memory/entities'
        if not directory.resolve().is_relative_to(home):
            raise ValueError('Memory entity directory escapes the workplace')
        directory.mkdir(parents=True, exist_ok=True)
        for actor in actors:
            identifier = 'minecraft-player-'+hashlib.sha256(actor.casefold().encode()).hexdigest()[:20]
            path = directory/(identifier+'.md')
            if not path.resolve().is_relative_to(home):
                raise ValueError('Invalid player memory path')
            context = self.player_context(actor)
            with self.connect() as db:
                row = db.execute("SELECT id,created,body FROM events WHERE scope='game_public' AND lower(actor)=lower(?) AND kind='request.received' ORDER BY id DESC LIMIT 1", (actor,)).fetchone()
            latest = {'created': row['created'], 'details': json.loads(row['body'])} if row else None
            if latest is None:
                continue
            body = path.read_text(encoding='utf-8') if path.exists() else (
                '# '+actor.replace('\n', ' ').replace('\r', ' ')+'\n'
                'type: player | source: Minecraft | identity: observed name, UUID unknown\n\n'
                '## Relationship\nNeutral; no inferred trust or grievance.\n\n## Links\n- [[saruman]] — observed Minecraft interaction\n')
            quote = ' '.join(str(latest['details'].get('text', '')).splitlines()).replace('<','&lt;').replace('>','&gt;')
            date = datetime.fromtimestamp(latest['created'], timezone.utc).isoformat()
            block = ('<!-- minecraft-runtime:start -->\n## Observed Minecraft interactions\n'
                '- Last observed message at '+date+': '+json.dumps(quote, ensure_ascii=False)+'.\n'
                '- Source: player statement, not independently verified world facts.\n'
                '- Full interactions and outcomes: [Lab journal](http://127.0.0.1:8765/#activity), player '+actor+'.\n'
                '- Journal event IDs: '+', '.join(str(row['journal_event']) for row in context)+'.\n'
                '- Recent observed outcomes: '+json.dumps([row['details']['goal'] for row in context if 'goal' in row['details']], ensure_ascii=False)+'.\n'
                '<!-- minecraft-runtime:end -->')
            pattern = r'<!-- minecraft-runtime:start -->[\s\S]*?<!-- minecraft-runtime:end -->'
            updated = re.sub(pattern, lambda match: block, body) if re.search(pattern, body) else body.rstrip()+'\n\n'+block+'\n'
            if updated != body:
                temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    temporary.write_text(updated, encoding='utf-8'); os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            index = home/'memory/MEMORY.md'
            if not index.resolve().is_relative_to(home):
                raise ValueError('Memory index escapes the workplace')
            text = index.read_text(encoding='utf-8') if index.exists() else '# Memory index\n'
            entry = 'entities/'+path.name
            if entry not in text:
                temporary=index.with_name(index.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    temporary.write_text(text+'\n- ['+identifier+']('+entry+') — observed Minecraft player; full history in Lab journal.\n',encoding='utf-8')
                    os.replace(temporary,index)
                finally:
                    temporary.unlink(missing_ok=True)


_store = None
_store_path = None


def current():
    global _store, _store_path
    if not (os.getenv('SARUMAN_LAB_CONTROL_DIR') or os.getenv('SARUMAN_ACTIVITY_DB')):
        return None  # Standalone/offline tests must not write the owner's live journal.
    path = Path(os.getenv('SARUMAN_ACTIVITY_DB') or PROJECT/'.saruman/activity.sqlite3')
    if _store is None or path != _store_path:
        try:
            _store, _store_path = ActivityStore(path=path), path
        except (OSError,sqlite3.Error,ValueError):
            return None
    return _store


def emit(kind, **fields):
    try:
        store = current()
        if store:
            store.record(kind, **fields)
    except (OSError, sqlite3.Error, ValueError):
        # Telemetry never owns an action or permission to replay one.
        pass
