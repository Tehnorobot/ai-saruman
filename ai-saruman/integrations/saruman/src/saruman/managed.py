"""Local Lab lifecycle bridge. Fixed files, no network listener or model calls."""
import json
import os
from pathlib import Path
import threading
import time
import uuid


class FileOwnerLock:
    """The OS releases ownership on process exit; a stale file never blocks a restart."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self):
        if self.file:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        if os.fstat(file.fileno()).st_size == 0:
            file.write(b"0"); file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise RuntimeError("Процесс уже управляется другим экземпляром Lab или игрового runtime") from None
        self.file = file

    def close(self):
        if self.file:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close(); self.file = None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BackgroundAgent:
    """The blocking supervisor loop must never occupy the lifecycle/Stop thread."""
    def __init__(self,run,is_running):
        self.run,self.is_running=run,is_running
        self.thread=None

    def start(self):
        if self.is_running():return True
        if self.thread and self.thread.is_alive():return False
        self.thread=threading.Thread(target=self.run,name="lab-game-agent",daemon=True)
        self.thread.start()
        return True


class ManagedControl:
    def __init__(self, directory, worker_id, clock=time.time):
        self.directory, self.worker_id, self.clock = Path(directory), worker_id, clock
        self.lock = threading.RLock()
        self.phase, self.reason = "checking", "Проверка игрового окружения"
        self.start_agent = self.stop_agent = self.inspect = None
        self.shutdown = None
        self.shutdown_sent = False
        self.directive_handler = None
        self.directive_id = None
        self.revision = None
        self.had_owner = False
        self.stop_event = threading.Event()
        self.thread = None
        self.filter_enabled = True

    def startup_cancelled(self):
        """Negative owner commands also cancel preflight before runtime callbacks exist."""
        try:command=json.loads((self.directory/"command.json").read_text(encoding="utf-8"))
        except (OSError,ValueError):return False
        return (command.get("worker_id")==self.worker_id
                and (command.get("shutdown") is True or command.get("active") is False))

    def phase_changed(self, phase, reason):
        with self.lock:
            self.phase, self.reason = phase, reason

    def bind(self, start, stop, inspect, shutdown=None, directive=None):
        with self.lock:
            self.start_agent, self.stop_agent, self.inspect = start, stop, inspect
            self.shutdown = shutdown
            self.directive_handler = directive
            self.phase, self.reason = "ready", "Игровой обработчик готов"

    def step(self):
        try:
            command = json.loads((self.directory / "command.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            command = {}
        owned = (command.get("worker_id") == self.worker_id
                 and 0 <= self.clock() - command.get("updated", 0) < 15)
        with self.lock:
            # A persisted shutdown may finish an interrupted startup after Lab exited.
            # Only negative authority survives heartbeat expiry; an old Start never does.
            if (command.get("worker_id") == self.worker_id and command.get("shutdown")
                    and self.shutdown is not None and not self.shutdown_sent):
                self.shutdown_sent = True
                self.shutdown()
            if self.start_agent is not None:
                if not owned:
                    if self.had_owner:
                        self.stop_agent()
                    self.had_owner = False
                elif command.get("revision") != self.revision:
                    if command.get("active"):
                        applied=self.start_agent() is not False
                    else:
                        self.stop_agent()
                        applied=True
                    if applied:self.revision = command.get("revision")
                    self.had_owner = True
                details = self.inspect()
                directive=command.get("directive") or {}
                if (owned and command.get("active") and self.directive_handler and directive.get("id")
                        and directive.get("id")!=self.directive_id):
                    saved={}
                    try:saved=json.loads((self.directory/"admin-result.json").read_text(encoding="utf-8"))
                    except (OSError,ValueError):pass
                    self.directive_id=directive["id"]
                    if saved.get("command_id")!=self.directive_id:
                        # Reserve before execution. After a crash an uncertain command is
                        # observed, never blindly executed a second time.
                        atomic_json(self.directory/"admin-result.json",{"command_id":self.directive_id,"status":"dispatching"})
                        result=self.directive_handler(directive["text"],self.directive_id)
                        atomic_json(self.directory/"admin-result.json",{"command_id":self.directive_id,**result})
            else:
                details = {}
            atomic_json(self.directory / "status.json", {
                "worker_id": self.worker_id, "pid": os.getpid(), "updated": self.clock(),
                "phase": self.phase, "reason": self.reason, "revision": self.revision,
                "owner_connected": owned, **details,
                "filter_enabled": self.filter_enabled,
            })

    def start(self):
        def loop():
            while not self.stop_event.is_set():
                try:
                    self.step()
                except Exception:
                    # A journal/callback failure cannot authorize an agent restart.
                    pass
                self.stop_event.wait(0.5)
        self.thread = threading.Thread(target=loop, name="lab-lifecycle", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(1)


control = None


def configure(root):
    global control
    directory, worker_id = os.getenv("SARUMAN_LAB_CONTROL_DIR"), os.getenv("SARUMAN_LAB_WORKER_ID")
    if not directory or not worker_id:
        return None
    expected = (Path(root).parents[1] / ".saruman/runtime/minecraft").resolve()
    if Path(directory).resolve() != expected:
        raise ValueError("Invalid Lab lifecycle directory")
    control = ManagedControl(expected, worker_id)
    control.start()
    return control
