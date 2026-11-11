"""Codex execution in its own container: no access to verifier DB, reports or S3 keys."""
from __future__ import annotations
import json,os,re,subprocess,tempfile,time,sys
from datetime import datetime, timezone
from pathlib import Path
from common.codex_settings import codex_cli_overrides, codex_agent_overrides
from common.project_lock import project_lock
from common.skill_environment import install_skills
from agent.process_scope import TaskProcesses
from common.execution_limits import role_timeout

ROOT=Path(os.getenv('AGENT_TASKS','/agent-tasks'))
PROJECTS=Path(os.getenv('LAB_PROJECTS','/projects'))
TIMEOUT=max(60,int(os.getenv('AGENT_TIMEOUT_SECONDS','3600')))
PROVIDER=os.getenv('AGENT_PROVIDER','codex')
EXECUTOR_ROLE=os.getenv('EXECUTOR_ROLE','coder')
PLANNER_AUTH_SOURCE=Path('/planner-auth/auth.json')


class TaskCleanupFailed(RuntimeError):
    pass


def task_provider(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        provider=value.get('provider','codex') if isinstance(value,dict) else 'codex'
        return provider if provider in ('codex','dslab','litellm','minimax') else 'codex'
    except (OSError,ValueError):
        # The Codex daemon retains ownership of malformed legacy requests and
        # publishes its normal validation error rather than crashing the poller.
        return 'codex'


def matches_executor(task):
    role = task.get('role', 'coder')
    provider = task.get('provider', 'codex')
    patch=task.get('output_contract')=='SubmissionPatch'
    return (role in ('coder', 'planner', 'reviewer') and
            ((EXECUTOR_ROLE=='planner') if patch else (role == EXECUTOR_ROLE or role=='reviewer' and EXECUTOR_ROLE=='planner'))
            and provider in ('codex', 'dslab','litellm','minimax')
            and (provider == PROVIDER or (PROVIDER=='dslab' and provider in ('litellm','minimax')) or (EXECUTOR_ROLE == 'planner' and PROVIDER == 'any')))


def task_matches(path):
    try:
        task = json.loads(path.read_text(encoding='utf-8'))
        return matches_executor(task) if isinstance(task, dict) else matches_executor({})
    except (OSError, ValueError):
        # Malformed legacy requests still receive a terminal Codex coder error.
        return matches_executor({})


def pid_namespace():
    return os.stat('/proc/self/ns/pid').st_ino if os.name == 'posix' else None


def acknowledge_restarted_cleanup():
    """Only a fresh PID namespace can acknowledge an unconfirmed old writer."""
    for marker in (ROOT/'cleanup-failed').glob('*.json'):
        failure=json.loads(marker.read_text(encoding='utf-8'))
        if not matches_executor(failure.get('result', {})):
            continue
        tid=marker.stem
        if not re.fullmatch(r'[0-9a-f]{14}-round[0-9]{2,16}(?:-[0-9a-f]{32})?',tid):
            raise TaskCleanupFailed('Invalid cleanup recovery marker')
        if not failure.get('pid_namespace') or failure['pid_namespace']==pid_namespace():
            raise TaskCleanupFailed('Cleanup requires a container restart before acknowledgement')
        atomic(ROOT/'results'/(tid+'.json'),failure['result'])
        (ROOT/'running'/(tid+'.json')).unlink(missing_ok=True)
        marker.unlink()

def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.partial')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def build_command(task, root):
    """Planner uses the dedicated container's read-only project mount."""
    role = task.get('role', 'coder')
    if role not in ('coder', 'planner', 'reviewer'):
        raise ValueError('Unknown agent role')
    patch=task.get('output_contract')=='SubmissionPatch'
    if ((EXECUTOR_ROLE!='planner') if patch else (role != EXECUTOR_ROLE and not (role=='reviewer' and EXECUTOR_ROLE=='planner'))):
        raise ValueError('Task requires its dedicated executor role')
    provider = task.get('provider', 'codex')
    if provider in ('dslab','litellm','minimax'):
        return [sys.executable, '-I', str(Path(__file__).with_name('dslab.py'))]
    if provider != 'codex':
        raise ValueError('Unknown provider')
    options = codex_cli_overrides(task.get('model', ''), task.get('reasoning_effort', 'default'))
    if role in ('planner','reviewer') or task.get('output_contract'):
        options += ['-c', 'features.multi_agent=false']
    else:
        options += codex_agent_overrides(root)
    # Nested bwrap namespaces are unavailable with the container's dropped
    # capabilities. The planner therefore relies on its kernel read-only bind
    # mount, without granting extra capabilities or weakening container policy.
    if task.get('output_contract'):
        from common.pipeline_contracts import load_submission_schema
        name=task['output_contract']
        load_submission_schema(name)  # Strict preflight, trusted image registry only.
        options += ['--output-schema', str(Path(__file__).resolve().parents[1]/'schemas'/'pipeline'/(name+'.submission.schema.json'))]
    return ['codex', 'exec', '--skip-git-repo-check', '--sandbox', 'danger-full-access',
            '-c', 'approval_policy="never"', '--json', *options, '-C', str(root), '-']


def bootstrap_planner_auth():
    """Link the private CLI profile to its read-only credential mount.

    Never read, copy or replace credentials. CLI token refresh may replace its
    own private link; it cannot modify the original mounted authentication.
    """
    if EXECUTOR_ROLE != 'planner':
        return
    home = Path(os.getenv('CODEX_HOME', '/home/lab/.codex'))
    target = home / 'auth.json'
    if os.path.lexists(target) or not PLANNER_AUTH_SOURCE.is_file():
        return
    home.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(PLANNER_AUTH_SOURCE), str(target))
    except FileExistsError:
        # A simultaneous private CLI setup must never be overwritten.
        pass

def run_task(src):
    task=json.loads(src.read_text(encoding='utf-8'))
    pid, jid, tid = task['project_id'], task['job_id'], task['id']
    if (not all(isinstance(s,str) and re.fullmatch('[0-9a-f]{14}',s) for s in (pid,jid))
            or not isinstance(tid,str)
            or not re.fullmatch(jid + r'-round[0-9]{2,16}(?:-[0-9a-f]{32})?', tid)
            or src.stem != tid):
        raise ValueError('Invalid task identifiers')
    if not matches_executor(task):
        raise ValueError('Task belongs to another executor')
    if EXECUTOR_ROLE == 'planner':
        # The controller holds the project lock throughout planning. This
        # container cannot install skills or create a lock in the RO project.
        return _run_task(src)
    # The supervisor holds this lock while the model writes project files.
    # Cancellation is checked again after acquiring it, before process launch.
    with project_lock(PROJECTS/pid):
        install_skills(PROJECTS/pid/'workspace', Path(__file__).resolve().parents[1]/'skills')
        return _run_task(src)


def _run_task(src):
    task=json.loads(src.read_text(encoding='utf-8'))
    tid=task['id']; pid=task['project_id']; jid=task['job_id']
    if not all(len(s)==14 and all(c in '0123456789abcdef' for c in s) for s in (pid,jid)):
        raise ValueError('Invalid task identifiers')
    root=(PROJECTS/pid/'workspace').resolve()
    if not root.is_dir():raise ValueError('Project directory missing')
    # The entire job uses the model/effort chosen in the dashboard, including quota retries.
    # Tokens are separate argv members, not a shell string; no CLI injection.
    provider=task.get('provider','codex')
    timeout=role_timeout(task.get('role','coder'), TIMEOUT)
    task['execution_timeout_seconds']=timeout
    task['execution_deadline_mode']='soft'
    cmd=build_command(task, root)
    if provider == 'codex':
        bootstrap_planner_auth()
    started=time.monotonic()
    log=ROOT/'logs'/f'{tid}.jsonl';log.parent.mkdir(parents=True,exist_ok=True)
    status='FAIL';error=None
    proc=None
    scope=None
    cleanup_failed=False
    with log.open('w',encoding='utf-8') as f:
        try:
            if (ROOT/'cancel'/tid).exists():
                atomic(ROOT/'results'/f'{tid}.json',{'id':tid,'status':'CANCELLED',
                       'duration_seconds':0,'log':log.name})
                src.unlink(missing_ok=True)
                return
            scope=TaskProcesses()
            # A seekable input avoids blocking the supervisor on a full pipe
            # when Codex has not started reading yet (cancel/timeout stay live).
            with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as task_input:
                if provider in ('dslab','litellm','minimax'):
                    task_input.write(json.dumps(task))
                else:
                    deadline=datetime.fromtimestamp(time.time()+timeout, timezone.utc).isoformat()
                    helper=''
                    if task.get('output_contract'):
                        context=task.get('tool_context_path')
                        if not context:
                            context=None
                        helper=('\nImage-owned retrieval tools: use python3 -m agent.codex_tools TOOL JSON --context '+context+' . '
                            'Tools: read_requirements, query_memory, read_artifact, read_execution, read_diff, record_note, list_files, run_command. '
                            'These tools preserve full linked memory and public notes without granting code writes to read-only roles. '
                            'Run diagnostic commands through run_command to retain full stdout/stderr artifacts and refs; read public notes before repeating investigation. ') if context else ''
                    task_input.write(task['prompt']+helper+'\nExecution handoff: '+deadline+' UTC is a soft completion target, not a process timeout. '
                        'Check the current UTC time between major investigation steps. Once due, stop broad exploration, '
                        'record observed findings and unresolved questions and submit the current valid result. '
                        'Do not invent completion or PASS. Sources and checkpoints must remain intact. '
                        'Only explicit user cancellation, configured token/request budgets and isolation rules stop work.')
                task_input.seek(0)
                process_environment={**os.environ,'LAB_ACTIVE_TASK_ID':tid}
                proc=subprocess.Popen(cmd,cwd=root,stdin=task_input,stdout=f,stderr=subprocess.STDOUT,
                    text=True,start_new_session=True,env=process_environment)
            while proc.poll() is None:
                if (ROOT/'cancel'/tid).exists():
                    status='CANCELLED';break
                time.sleep(1)
            if status != 'CANCELLED':
                status='PASS' if proc.returncode==0 else 'FAIL'
        except Exception as exc:
            status='ERROR'
            error=type(exc).__name__+': '+str(exc)[:220]
            f.write('\n[agent executor] '+error+'\n')
        finally:
            try:
                remaining=scope.close(proc) if scope else []
                if remaining:
                    cleanup_failed=True
                    status='ERROR'
                    error=f'Task process cleanup failed: {remaining}'
                if proc and proc.poll() is None:
                    proc.kill();proc.wait(timeout=6)
            except Exception as exc:
                cleanup_failed=True
                status='ERROR'
                error=f'Task cleanup failed: {type(exc).__name__}: {str(exc)[:200]}'
    result={'id':tid,'status':status,'error':error,
          'duration_seconds':round(time.monotonic()-started,2),'log':log.name,
          'model':task.get('model') or 'codex_default',
          'reasoning_effort':task.get('reasoning_effort') or 'default','provider':provider,
          'role':task.get('role','coder'),'output_contract':task.get('output_contract')}
    if cleanup_failed:
        atomic(ROOT/'cleanup-failed'/f'{tid}.json',
               {'pid_namespace':pid_namespace(),'result':result})
        # No terminal ACK and no unlocked next task before container restart.
        raise TaskCleanupFailed(error)
    atomic(ROOT/'results'/f'{tid}.json',result)
    src.unlink(missing_ok=True)

def main():
    if EXECUTOR_ROLE not in ('coder', 'planner') or PROVIDER not in ('codex', 'dslab', 'any'):
        raise ValueError('Unknown executor role or provider')
    if PROVIDER == 'any' and EXECUTOR_ROLE != 'planner':
        raise ValueError('Provider any requires the isolated planner executor')
    for p in ('requests','running','results','logs','cancel'):(ROOT/p).mkdir(exist_ok=True,parents=True)
    acknowledge_restarted_cleanup()
    while True:
        # Recover claimed tasks first. Atomic rename distinguishes a pending
        # request (safe to withdraw) from a task that may have started.
        for pending in sorted((ROOT/'requests').glob('*.json')):
            if not task_matches(pending):
                continue
            try: pending.replace(ROOT/'running'/pending.name)
            except FileNotFoundError: pass
        for p in sorted((ROOT/'running').glob('*.json')):
            if not task_matches(p):
                continue
            if (ROOT/'results'/p.name).exists():
                p.unlink(missing_ok=True)
                continue
            try:run_task(p)
            except TaskCleanupFailed:
                raise
            except Exception as exc:
                atomic(ROOT/'results'/p.name,{'id':p.stem,'status':'FAIL',
                  'error':f'Agent executor failed: {type(exc).__name__}: {str(exc)[:200]}'})
                p.unlink(missing_ok=True)
        time.sleep(3)
if __name__=='__main__':main()
