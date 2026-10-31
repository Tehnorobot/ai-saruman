"""Own and reap Linux task descendants, including programs which call setsid()."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import sys
import time


def processes():
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            result[int(path.parent.name)] = (int(fields[1]), int(fields[19]), fields[0])
        except (OSError, ValueError, IndexError):
            continue
    return result


def descendants(root, table):
    found = {root}
    while True:
        added = {pid for pid, (parent, _, _) in table.items() if parent in found} - found
        if not added:
            return found - {root}
        found.update(added)


class TaskProcesses:
    """The daemon is sequential; a subreaper adopts detached task grandchildren."""
    def __init__(self):
        self.enabled = sys.platform == 'linux'
        self.baseline = set()
        self.previous_subreaper = ctypes.c_int()
        if self.enabled:
            self.libc = ctypes.CDLL(None, use_errno=True)
            if self.libc.prctl(37, ctypes.byref(self.previous_subreaper), 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), 'Cannot inspect child subreaper')
            if self.libc.prctl(36, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), 'Cannot establish child subreaper')
            table = processes()
            self.baseline = {(pid, table[pid][1]) for pid in descendants(os.getpid(), table)}

    def owned(self):
        table = processes()
        return {pid: table[pid] for pid in descendants(os.getpid(), table)
                if (pid, table[pid][1]) not in self.baseline}

    @staticmethod
    def send(pid, start, sig):
        # pidfds avoid signalling an unrelated process after PID reuse.
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        try:
            if processes().get(pid, (None, None, None))[1] == start:
                signal.pidfd_send_signal(fd, sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)

    def close(self, process=None, grace=3):
        if not self.enabled:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=grace)
                except Exception:
                    process.kill()
                    process.wait(timeout=grace)
            return []
        remaining = {}
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                deadline = time.monotonic() + grace
                while True:
                    remaining = self.owned()
                    for pid, (_, start, state) in remaining.items():
                        if state != 'Z':
                            self.send(pid, start, sig)
                    if process:
                        process.poll()
                    for pid in remaining:
                        if process and pid == process.pid:
                            continue
                        try:
                            os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            pass
                    remaining = self.owned()
                    if not remaining or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                if not remaining:
                    break
            return sorted(remaining)
        finally:
            self.libc.prctl(36, self.previous_subreaper.value, 0, 0, 0)
