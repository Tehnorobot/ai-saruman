"""Cooperative cross-process lock for project writers and backup snapshots."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import time


def _initialize_lock_file(path: Path):
    """Create a non-secret shared lock for services running under different UIDs."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    except FileExistsError:
        return
    try:
        if os.name != 'nt':
            # Override the creator's umask only for this newly created lock inode.
            os.fchmod(fd, 0o666)
    finally:
        os.close(fd)


@contextmanager
def project_lock(root: Path, *, shared: bool = False, allow_runner: bool = False):
    """Hold the project lock; Windows conservatively serializes readers too.

    Writers must hold the default exclusive lock for their entire mutation.
    Keep the lock outside workspace so project build commands cannot replace it.
    """
    root = Path(root)
    def guard():
        if not allow_runner and (root/'.lab-runner.json').exists():
            raise RuntimeError('An isolated runner needs controller cleanup before project access')
    lock_file = root / '.lab.lock'
    if shared:
        try:
            handle = lock_file.open('rb')
        except FileNotFoundError:
            # New projects initialize this before backup sees their read-only mount.
            # For writable legacy projects it is safe to initialize on first use.
            try:
                _initialize_lock_file(lock_file)
                handle = lock_file.open('rb')
            except OSError as exc:
                raise RuntimeError('Project lock is missing; initialize .lab.lock from a writable project service before backup') from exc
    else:
        root.mkdir(parents=True, exist_ok=True)
        _initialize_lock_file(lock_file)
        handle = lock_file.open('a+b')
    with handle:
        if os.name == 'nt':
            import msvcrt
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (13, 36):
                        raise
                    time.sleep(0.1)
            try:
                if not shared and handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b'\0')
                    handle.flush()
                guard()
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                guard()
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
