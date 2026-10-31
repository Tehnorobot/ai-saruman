"""SQLite task queue: dashboard writes, worker leases; durable after worker restart."""
from __future__ import annotations
import json, os, sqlite3, time, uuid
from contextlib import contextmanager
from pathlib import Path
from common.codex_settings import validate_model, validate_reasoning
from common.provider_settings import validate_settings, validate_role_models
from common.project_lock import project_lock

DB = Path(os.getenv('LAB_DB', '/data/lab.sqlite3'))
# Numeric storage/UI precision boundary, not a development round budget.
MAX_ROUND_LIMIT = 2**53 - 1
STATUSES = {'QUEUED', 'RUNNING', 'QUOTA_PAUSED', 'USER_PAUSED', 'FINISHED', 'FAILED', 'STOPPED'}

def validate_round_limit(value):
    """Zero means unlimited; finite limits must roundtrip exactly through JSON/JS."""
    if type(value) is not int or not 0 <= value <= MAX_ROUND_LIMIT:
        raise ValueError('Round limit must be a nonnegative safe integer; 0 means unlimited')
    return value

@contextmanager
def connect():
    controller_root = (hasattr(os, 'geteuid') and os.geteuid() == 0 and os.getegid() == 1000)
    if controller_root:
        # The controller shares SQLite with uid 1000 dashboard/backup. Group
        # write access must survive a cold start and new WAL/SHM creation.
        os.umask(0o002)
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if controller_root:
            DB.chmod(DB.stat().st_mode | 0o060)
        con.execute('PRAGMA busy_timeout=30000')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA foreign_keys=ON')
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def init():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, spec_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
          status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'queued',
          round INTEGER NOT NULL DEFAULT 0, max_rounds INTEGER NOT NULL DEFAULT 5,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          last_error TEXT, resume_at INTEGER NOT NULL DEFAULT 0,
          cancel_requested INTEGER NOT NULL DEFAULT 0, pause_requested INTEGER NOT NULL DEFAULT 0,
          worker TEXT, lease_until INTEGER NOT NULL DEFAULT 0,
          model TEXT NOT NULL DEFAULT '', reasoning_effort TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
          created_at TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL,
          FOREIGN KEY(job_id) REFERENCES jobs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, resume_at);
        CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
        ''')
        # Existing users keep their queued and completed jobs unchanged.
        # Dashboard, backup and worker can start together. Serialize the schema
        # check and ALTER so two processes cannot add the same column.
        con.execute('BEGIN IMMEDIATE')
        columns = {r['name'] for r in con.execute('PRAGMA table_info(jobs)')}
        if 'model' not in columns:
            con.execute("ALTER TABLE jobs ADD COLUMN model TEXT NOT NULL DEFAULT ''")
        if 'reasoning_effort' not in columns:
            con.execute("ALTER TABLE jobs ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'default'")
        if 'resume_generation' not in columns:
            con.execute('ALTER TABLE jobs ADD COLUMN resume_generation INTEGER NOT NULL DEFAULT 0')
        for name, definition in (
            ('role_models', "TEXT NOT NULL DEFAULT '{}'"),
            ('provider', "TEXT NOT NULL DEFAULT 'codex'"),
            ('dslab_token_budget', 'INTEGER NOT NULL DEFAULT 1000000'),
            ('dslab_max_output_tokens', 'INTEGER NOT NULL DEFAULT 8192'),
            ('dslab_max_calls', 'INTEGER NOT NULL DEFAULT 100')):
            if name not in columns:
                con.execute('ALTER TABLE jobs ADD COLUMN ' + name + ' ' + definition)

def utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def event(con, job, message, level='info'):
    con.execute('INSERT INTO events(job_id,created_at,level,text) VALUES(?,?,?,?)',
                (job, utc(), level, str(message)[:1600]))

def create_project(title, spec_name, spec_content, max_rounds=5, model='', reasoning_effort='default',
                   provider='codex', dslab_token_budget=1000000, dslab_max_output_tokens=8192, dslab_max_calls=100,
                   role_models=None):
    max_rounds = validate_round_limit(max_rounds)
    provider, model, reasoning_effort, dslab_token_budget, dslab_max_output_tokens, dslab_max_calls = validate_settings(
        provider, model, reasoning_effort, dslab_token_budget, dslab_max_output_tokens, dslab_max_calls)
    role_models=validate_role_models(role_models)
    init()
    pid, jid = uuid.uuid4().hex[:14], uuid.uuid4().hex[:14]
    # Write file before transaction so no queued job ever sees a missing spec.
    root = Path(os.environ.get('LAB_PROJECTS', '/projects')) / pid
    root.mkdir(parents=True, exist_ok=False)
    with project_lock(root):
        pass
    (root / 'ТЗ.md').write_bytes(spec_content)
    (root / 'workspace').mkdir()
    now = utc()
    with connect() as con:
        con.execute('INSERT INTO projects VALUES(?,?,?,?)', (pid, title[:120], now, spec_name))
        con.execute('INSERT INTO jobs(id,project_id,status,phase,max_rounds,created_at,updated_at,model,reasoning_effort) VALUES(?,?,?,?,?,?,?,?,?)',
                    (jid, pid, 'QUEUED', 'queued', max_rounds, now, now, model, reasoning_effort))
        con.execute('UPDATE jobs SET provider=?,dslab_token_budget=?,dslab_max_output_tokens=?,dslab_max_calls=? WHERE id=?',
                    (provider, dslab_token_budget, dslab_max_output_tokens, dslab_max_calls, jid))
        con.execute('UPDATE jobs SET role_models=? WHERE id=?',(json.dumps(role_models),jid))
        event(con, jid, 'ТЗ получено, проект поставлен в очередь')
    return pid, jid

def job_row(jid):
    with connect() as con:
        row = con.execute('SELECT j.*,p.title FROM jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=?', (jid,)).fetchone()
        return dict(row) if row else None

def jobs_list():
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT j.*,p.title FROM jobs j JOIN projects p ON p.id=j.project_id ORDER BY j.created_at DESC LIMIT 100')]

def events_list(jid, after=0):
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 500', (jid,after))]

def update(jid, **fields):
    if not fields: return
    assert set(fields).issubset({'status','phase','round','last_error','resume_at','cancel_requested','pause_requested','lease_until','worker'})
    fields['updated_at'] = utc()
    with connect() as con:
        con.execute('UPDATE jobs SET '+','.join(f'{x}=?' for x in fields)+' WHERE id=?', (*fields.values(),jid))

def heartbeat(jid, worker_id, seconds=90):
    """Renew only a live lease still held by this worker."""
    now = int(time.time())
    with connect() as con:
        cursor = con.execute('''UPDATE jobs SET lease_until=?,updated_at=?
            WHERE id=? AND worker=? AND status='RUNNING' AND lease_until>=?''',
            (now + seconds, utc(), jid, worker_id, now))
        return cursor.rowcount == 1


def update_owned(jid, worker_id, **fields):
    """Fence worker progress/completion so an expired owner cannot undo recovery."""
    if not fields:
        return False
    assert set(fields).issubset({'status','phase','round','last_error','resume_at','cancel_requested','pause_requested','lease_until','worker'})
    fields['updated_at'] = utc()
    with connect() as con:
        cursor = con.execute('UPDATE jobs SET '+','.join(f'{x}=?' for x in fields)+
            " WHERE id=? AND worker=? AND status='RUNNING' AND lease_until>=?",
            (*fields.values(), jid, worker_id, int(time.time())))
        return cursor.rowcount == 1


def log(jid, message, level='info'):
    with connect() as con: event(con,jid,message,level)

def claim(worker_id):
    init(); now = int(time.time())
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # A dead controller may leave a live agent or sandbox behind. Reclaim
        # RUNNING control requests too, but keep RUNNING until the replacement
        # worker has cancelled the actual writer and acknowledged its exit.
        row = con.execute('''SELECT * FROM jobs WHERE
            (status='QUEUED' AND cancel_requested=0 AND pause_requested=0) OR
            (status='QUOTA_PAUSED' AND resume_at<=? AND cancel_requested=0 AND pause_requested=0) OR
            (status='RUNNING' AND lease_until<=?) ORDER BY created_at LIMIT 1''',(now,now)).fetchone()
        if row:
            con.execute('''UPDATE jobs SET status=?,worker=?,lease_until=?,updated_at=?,
                        resume_generation=resume_generation+? WHERE id=?''',
                        ('RUNNING',worker_id,now+90,utc(),int(row['status']=='QUOTA_PAUSED'),row['id']))
            event(con,row['id'],'Задание занято обработчиком '+worker_id)
        return dict(row) if row else None


def control_job(jid, action):
    """Apply a control transition atomically with claim/completion and its event.

    Return the resulting job, None for an unknown job, and raise ValueError for
    an invalid transition. RUNNING keeps its state until the worker acknowledges
    the request after its writer exits, so resume/settings cannot race its work.
    """
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if row is None:
            return None
        status = row['status']
        if action == 'pause' and status in ('QUEUED', 'RUNNING', 'QUOTA_PAUSED') and not row['cancel_requested']:
            con.execute('''UPDATE jobs SET pause_requested=1,
                status=CASE WHEN status='RUNNING' THEN status ELSE 'USER_PAUSED' END,
                updated_at=? WHERE id=?''', (utc(), jid))
            message = 'Пользователь запросил паузу'
        elif action == 'resume' and status in ('USER_PAUSED', 'QUOTA_PAUSED', 'FAILED') and not row['cancel_requested']:
            con.execute('''UPDATE jobs SET pause_requested=0,cancel_requested=0,status='QUEUED',
                resume_at=0,last_error=NULL,worker=NULL,lease_until=0,
                resume_generation=resume_generation+1,updated_at=? WHERE id=?''', (utc(), jid))
            message = 'Пользователь возобновил задание'
        elif action == 'stop' and status in ('QUEUED', 'RUNNING', 'USER_PAUSED', 'QUOTA_PAUSED'):
            con.execute('''UPDATE jobs SET cancel_requested=1,
                status=CASE WHEN status='RUNNING' THEN status ELSE 'STOPPED' END,
                updated_at=? WHERE id=?''', (utc(), jid))
            message = 'Пользователь запросил остановку'
        else:
            raise ValueError('Action is not valid for current status')
        event(con, jid, message)
        return dict(con.execute('''SELECT j.*,p.title FROM jobs j
            JOIN projects p ON p.id=j.project_id WHERE j.id=?''', (jid,)).fetchone())


def extend_job_rounds(jid, additional_rounds):
    """Queue the saved project after its budget ends; serialize with claims/retries."""
    if type(additional_rounds) is not int or not 1 <= additional_rounds <= MAX_ROUND_LIMIT:
        raise ValueError('additional_rounds must be a positive safe integer')
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if row is None:
            return None
        if (row['status'] != 'FINISHED' or row['phase'] != 'max_rounds_reached' or row['max_rounds'] == 0
                or row['round'] != row['max_rounds'] or row['cancel_requested']
                or row['worker'] is not None):
            raise ValueError('Only a job that reached its round limit can be extended')
        limit = row['max_rounds'] + additional_rounds
        validate_round_limit(limit)
        con.execute('''UPDATE jobs SET max_rounds=?,status='QUEUED',phase='queued_extension',
            pause_requested=0,cancel_requested=0,resume_at=0,last_error=NULL,
            worker=NULL,lease_until=0,resume_generation=resume_generation+1,updated_at=?
            WHERE id=?''', (limit, utc(), jid))
        event(con, jid, f'Добавлено раундов: {additional_rounds}. '
              f'Продолжение с раунда {row["round"] + 1}; общий лимит {limit}.')
        return dict(con.execute('''SELECT j.*,p.title FROM jobs j
            JOIN projects p ON p.id=j.project_id WHERE j.id=?''', (jid,)).fetchone())


def set_job_round_limit(jid, max_rounds):
    """Change a saved budget without dispatching agents; serialize with worker claims."""
    max_rounds = validate_round_limit(max_rounds)
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if row is None:
            return None
        if (row['status'] not in ('QUEUED','USER_PAUSED','QUOTA_PAUSED','FAILED','FINISHED')
                or row['worker'] is not None or row['cancel_requested']
                or (row['status']=='FINISHED' and row['phase']!='max_rounds_reached')):
            raise ValueError('Pause the job before changing its round limit')
        if max_rounds != 0 and max_rounds <= row['round']:
            raise ValueError('Finite limit must exceed the current round')
        finished = row['status']=='FINISHED'
        con.execute('''UPDATE jobs SET max_rounds=?,
            status=CASE WHEN ? THEN 'USER_PAUSED' ELSE status END,
            phase=CASE WHEN ? THEN 'round_limit_updated' ELSE phase END,
            pause_requested=CASE WHEN ? THEN 1 ELSE pause_requested END,
            updated_at=? WHERE id=?''', (max_rounds, finished, finished, finished, utc(), jid))
        label = str(max_rounds) if max_rounds else 'без лимита'
        event(con, jid, f'Лимит раундов изменён: {label}. Состояние работы сохранено; автозапуска нет.')
        return dict(con.execute('''SELECT j.*,p.title FROM jobs j
            JOIN projects p ON p.id=j.project_id WHERE j.id=?''', (jid,)).fetchone())


def set_job_codex_settings(jid, model, reasoning_effort, provider=None,
                           dslab_token_budget=None, dslab_max_output_tokens=None, dslab_max_calls=None):
    """Change future rounds only when not running; atomic SQL update avoids claim races."""
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        current = con.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if current is None:
            return False
        provider, model, reasoning_effort, budget, output, calls = validate_settings(
            provider if provider is not None else current['provider'], model, reasoning_effort,
            current['dslab_token_budget'] if dslab_token_budget is None else dslab_token_budget,
            current['dslab_max_output_tokens'] if dslab_max_output_tokens is None else dslab_max_output_tokens,
            current['dslab_max_calls'] if dslab_max_calls is None else dslab_max_calls)
        cursor = con.execute('''UPDATE jobs SET model=?, reasoning_effort=?, provider=?,
            dslab_token_budget=?,dslab_max_output_tokens=?,dslab_max_calls=?, updated_at=?
            WHERE id=? AND status IN ('QUEUED', 'USER_PAUSED', 'QUOTA_PAUSED')
              AND cancel_requested=0''', (model, reasoning_effort, provider, budget, output, calls, utc(), jid))
        if cursor.rowcount:
            event(con, jid, 'Обновлены провайдер, модель и лимиты для следующих запусков')
        return cursor.rowcount == 1


def set_job_role_models(jid, role_models):
    """Replace stage overrides atomically; never change active work or resume it."""
    normalized=validate_role_models(role_models)
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        cursor=con.execute('''UPDATE jobs SET role_models=?,updated_at=? WHERE id=?
            AND status IN ('QUEUED','USER_PAUSED','QUOTA_PAUSED') AND cancel_requested=0''',
            (json.dumps(normalized),utc(),jid))
        if cursor.rowcount:event(con,jid,'Обновлены модели планировщика, кодера и ревьюера для следующих запусков')
        return cursor.rowcount==1
