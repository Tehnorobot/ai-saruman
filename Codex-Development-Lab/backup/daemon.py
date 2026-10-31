"""Separate backup service: immutable-ish dated snapshots with manifest and SHA-256."""
from __future__ import annotations
import datetime as dt,hashlib,io,json,os,stat,tarfile,time,sqlite3,tempfile
from contextlib import contextmanager
from pathlib import Path
from common import db
from common.project_lock import project_lock

REQUESTS=Path(os.getenv('BACKUP_REQUESTS','/backup-requests'))
OUTPUT=Path(os.getenv('LAB_BACKUPS','/backups'))
PROJECTS=Path(os.getenv('LAB_PROJECTS','/projects'))
ARTIFACTS=Path(os.getenv('LAB_ARTIFACTS','/artifacts'))

# Uncompressed file payload limits; the manifest and tar headers are additional.
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000
MAX_ENTRIES = 50000
MAX_EXCLUSIONS = 5000
CACHE_NAMES = {'.lab-cache', '.gradle', '.cache', '.minecraft', 'node_modules', '.git', '.venv',
               '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}
VIDEO_SUFFIXES = {'.mp4', '.webm', '.mkv', '.avi', '.mov'}


def _exclusion(manifest, path, reason, *, size=None, subtree=False):
    if len(manifest['exclusions']) >= MAX_EXCLUSIONS:
        manifest['exclusions_truncated'] += 1
        return
    entry = {'path': path, 'reason': reason}
    if size is not None:entry['size'] = size
    if subtree:entry['scope'] = 'subtree'
    manifest['exclusions'].append(entry)


def _policy(parts, directory=False):
    """Keep source resources and recovery evidence, not disposable game data."""
    if any(part in CACHE_NAMES for part in parts):return 'cache_directory'
    if any(part in {'.lab.lock', '.lab-runner.json'} for part in parts):return 'runtime_control_file'
    if any(part in {'.aws', '.ssh'} or part.startswith('.env') for part in parts):return 'secret_path'
    if any(parts[i] == '.codex' and parts[i+1] in {'auth.json', 'sessions', 'shell_snapshots'}
           for i in range(len(parts)-1)):return 'secret_path'
    if not directory and Path(parts[-1]).suffix.lower() in VIDEO_SUFFIXES:return 'video_stays_local'
    if 'build' in parts:
        tail = parts[parts.index('build') + 1:]
        if not tail:return None
        if tail[0] in {'test-results', 'reports'}:return None
        if tail[0] == 'libs':
            if directory or Path(tail[-1]).suffix.lower() in {'.jar', '.zip'}:return None
            return 'generated_build_output'
        if tail[0] == 'run':
            if len(tail) < 3:return None if directory else 'game_runtime_data'
            if tail[2] in {'logs', 'crash-reports', 'reports'}:return None
            return 'game_runtime_data'
        return 'generated_build_output'
    if 'run' in parts:
        tail = parts[parts.index('run') + 1:]
        if not tail:return None if directory else 'game_runtime_data'
        if tail[0] not in {'logs', 'crash-reports', 'reports'}:return 'game_runtime_data'
    return None


@contextmanager
def _scan_directory(root, relative):
    path=root/relative
    if os.name == 'posix' and hasattr(os,'O_NOFOLLOW'):
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
        fd=os.open('/',flags)
        try:
            for component in (*root.absolute().parts[1:],*relative.parts):
                child=os.open(component,flags,dir_fd=fd)
                os.close(fd);fd=child
            with os.scandir(fd) as iterator:yield iterator
        finally:os.close(fd)
    else:
        current=root
        for component in relative.parts:
            if current.is_symlink():raise ValueError('Symlink in backup directory')
            current/=component
        if current.is_symlink():raise ValueError('Symlink in backup directory')
        with os.scandir(path) as iterator:yield iterator


def _candidates(root, prefix, manifest, budget):
    """Prune large directory trees before descending; never traverse symlinks."""
    if root.is_symlink():
        _exclusion(manifest, prefix, 'symlink', subtree=True)
        return
    if not root.is_dir():return
    pending = [root]
    while pending:
        parent = pending.pop()
        try:
            entries = []
            with _scan_directory(root,parent.relative_to(root)) as iterator:
                for entry in iterator:
                    if budget[0] >= MAX_ENTRIES:
                        _exclusion(manifest, prefix, 'entry_limit', subtree=True)
                        return
                    budget[0] += 1
                    try:entries.append((entry.name,entry.stat(follow_symlinks=False).st_mode))
                    except OSError:
                        relative=(parent/entry.name).relative_to(root)
                        _exclusion(manifest,prefix+'/'+relative.as_posix(),'unreadable_entry')
        except (OSError,ValueError):
            _exclusion(manifest, prefix+'/'+parent.relative_to(root).as_posix(), 'unreadable_directory', subtree=True)
            continue
        directories = []
        for name,mode in sorted(entries):
            path = parent/name
            relative = path.relative_to(root)
            arc = prefix+'/'+relative.as_posix()
            try:
                if stat.S_ISLNK(mode):
                    _exclusion(manifest, arc, 'symlink')
                    continue
                is_directory = stat.S_ISDIR(mode)
                reason = _policy(relative.parts, is_directory)
                if reason:
                    _exclusion(manifest, arc, reason, subtree=is_directory)
                elif is_directory:
                    directories.append(path)
                elif stat.S_ISREG(mode):
                    yield relative, arc
                else:_exclusion(manifest, arc, 'special_file')
            except OSError:_exclusion(manifest, arc, 'unreadable_entry')
        pending.extend(reversed(directories))


@contextmanager
def _open_regular(root, relative):
    """Protect file opens from a project-controlled link replacement."""
    root = root.absolute()
    if os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open('/', flags)
        try:
            for component in (*root.parts[1:], *relative.parts[:-1]):
                child = os.open(component, flags, dir_fd=fd)
                os.close(fd);fd = child
            opened = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:os.close(fd)
        with os.fdopen(opened, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):raise ValueError('Not a regular file')
            yield source
    else:
        path = root
        for component in relative.parts:
            if path.is_symlink():raise ValueError('Symlink in backup path')
            path /= component
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe backup path')
        with path.open('rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):raise ValueError('Not a regular file')
            yield source


def _add(tar, manifest, arc, raw, mode=0o644, mtime=None):
    info=tarfile.TarInfo(arc);info.size=len(raw);info.mode=mode
    info.mtime=int(time.time() if mtime is None else mtime)
    tar.addfile(info,io.BytesIO(raw))
    manifest['files'][arc]=hashlib.sha256(raw).hexdigest()


def safe_id(s):return isinstance(s,str) and len(s)==14 and all(c in '0123456789abcdef' for c in s)

def create_backup(job_id,reason):
    row=db.job_row(job_id)
    if not row or not safe_id(row['project_id']) or not safe_id(job_id):raise ValueError('Invalid job')
    d=OUTPUT/job_id;d.mkdir(parents=True,exist_ok=True)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H-%M-%S-%fZ')
    final=d/f'{stamp}-{reason[:24].replace("/","-")}.tar.gz'
    tmp=final.with_suffix('.partial')
    manifest={'schema_version':2,'date_utc':stamp,'reason':reason,'job':row,'files':{},
              'exclusions':[],'exclusions_truncated':0,
              'limits':{'file_bytes':MAX_FILE_BYTES,'binary_bytes':MAX_BINARY_BYTES,
                        'state_bytes':MAX_STATE_BYTES,'total_payload_bytes':MAX_TOTAL_BYTES,
                        'files':MAX_FILES,'scanned_entries':MAX_ENTRIES}}
    roots=[('project',PROJECTS/row['project_id']),('artifacts',ARTIFACTS/row['project_id']/job_id)]
    # Reserve space for the consistent queue snapshot; sources and dependencies
    # take priority over generated builds and reports within the remaining budget.
    with project_lock(PROJECTS/row['project_id'],shared=True), tarfile.open(tmp,'w:gz') as tar:
        candidates=[];budget=[0];payload_bytes=0
        for prefix,root in roots:
            for rel,arc in _candidates(root,prefix,manifest,budget):
                priority = (3 if prefix == 'artifacts' else 2 if 'build' in rel.parts
                            else 1 if rel.suffix.lower() in {'.jar','.zip'} else 0)
                candidates.append((priority,arc,root,rel))
        for _priority,arc,root,rel in sorted(candidates):
            if len(manifest['files']) >= MAX_FILES-1:
                _exclusion(manifest,arc,'file_count_limit');continue
            limit=MAX_BINARY_BYTES if rel.suffix.lower() in {'.jar','.zip'} else MAX_FILE_BYTES
            try:
                with _open_regular(root,rel) as source:
                    metadata=os.fstat(source.fileno())
                    if metadata.st_size>limit:
                        _exclusion(manifest,arc,'file_size_limit',size=metadata.st_size);continue
                    available=MAX_TOTAL_BYTES-MAX_STATE_BYTES-payload_bytes
                    if metadata.st_size>available:
                        _exclusion(manifest,arc,'total_size_limit',size=metadata.st_size);continue
                    raw=source.read(min(limit,available)+1)
                if len(raw)>limit or len(raw)>available:
                    _exclusion(manifest,arc,'file_changed_past_limit',size=len(raw));continue
                # Hash exactly what addfile receives, never reopen for the digest.
                _add(tar,manifest,arc,raw,stat.S_IMODE(metadata.st_mode),metadata.st_mtime)
                payload_bytes+=len(raw)
            except (OSError,ValueError):_exclusion(manifest,arc,'unreadable_or_unsafe_file')
        # An online SQLite backup preserves queue/events consistently even with WAL active.
        with tempfile.TemporaryDirectory(prefix='lab-backup-') as td:
            dst=Path(td)/'lab.sqlite3'
            source=sqlite3.connect(db.DB)
            target=sqlite3.connect(dst)
            state_saved=False
            try:
                page_size=source.execute('PRAGMA page_size').fetchone()[0]
                def check_state_size(_status,_remaining,total):
                    if total*page_size>MAX_STATE_BYTES:raise ValueError('State snapshot exceeds size limit')
                source.backup(target,pages=256,progress=check_state_size)
                target.commit()
                state_saved=True
            except ValueError:
                _exclusion(manifest,'state/lab.sqlite3','state_size_limit')
            finally:
                source.close()
                target.close()
            if state_saved:
                raw=dst.read_bytes()
                _add(tar,manifest,'state/lab.sqlite3',raw)
                payload_bytes+=len(raw)
        manifest['payload_bytes']=payload_bytes
        manifest['complete']=not manifest['exclusions'] and not manifest['exclusions_truncated']
        raw=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        info=tarfile.TarInfo('backup_manifest.json');info.size=len(raw);info.mtime=int(time.time())
        tar.addfile(info,io.BytesIO(raw))
    tmp.replace(final)
    digest=hashlib.sha256()
    with final.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    (final.with_suffix(final.suffix+'.sha256')).write_text(digest.hexdigest()+'  '+final.name+'\n')
    return final


def run_once():
    REQUESTS.mkdir(parents=True,exist_ok=True)
    for req in sorted(REQUESTS.glob('*.json')):
        try:
            data=json.loads(req.read_text(encoding='utf-8'))
            job_id=data['job_id'];reason=data.get('reason','snapshot')
            backup=create_backup(job_id,reason)
            db.log(job_id,'Резервная копия сохранена: '+backup.name)
            req.unlink()
        except Exception as exc:
            # Corrupted request won't consume every tick; leave for operator to inspect.
            print(f'Backup error {req.name}: {str(exc)[:200]}',flush=True)
            try:req.rename(req.with_suffix('.failed'))
            except OSError:pass

def main():
    db.init()
    while True:
        run_once();time.sleep(5)
if __name__=='__main__':main()
