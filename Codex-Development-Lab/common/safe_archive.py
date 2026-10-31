"""Linux-only, descriptor-relative transport for untrusted workspace trees.

No archive ownership is applied and no archive link is followed while writing.
Archives are deliberately limited to directories, ordinary files and contained
relative symbolic links. Callers must hold their project lock during transport.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import uuid
import re
import time


MAX_BYTES = 20 * 1024**3
MAX_MEMBERS = 200_000
_JOURNAL = ".lab-workspace-swap.json"


class _TransportInfo(tarfile.TarInfo):
    """Bound extension headers before tarfile allocates or interprets them."""

    def _proc_member(self, archive):
        archive._lab_header_count = getattr(archive, "_lab_header_count", 0) + 1
        if archive._lab_header_count > MAX_MEMBERS * 2:
            raise ValueError("Archive contains too many headers")
        if self.type == tarfile.XHDTYPE:
            if not 0 <= self.size <= 64 * 1024:
                raise ValueError("Archive extension header exceeds size limit")
            depth = getattr(archive, "_lab_extension_depth", 0)
            if depth >= 4:
                raise ValueError("Archive extension nesting exceeds limit")
            archive._lab_extension_depth = depth + 1
            try:
                return super()._proc_member(archive)
            finally:
                archive._lab_extension_depth = depth
        if self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE,
                             tarfile.DIRTYPE, tarfile.SYMTYPE):
            raise ValueError("Unsupported archive header type")
        return super()._proc_member(archive)

    def _reject_sparse(self, *args):
        raise ValueError("Sparse archive entries are unsupported")

    _proc_gnusparse_00 = _reject_sparse
    _proc_gnusparse_01 = _reject_sparse
    _proc_gnusparse_10 = _reject_sparse


def _supported() -> None:
    if (not sys.platform.startswith("linux") or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd
            or not shutil.rmtree.avoids_symlink_attacks):
        raise RuntimeError("Workspace archive transport requires Linux descriptor isolation")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_root(root: Path) -> int:
    _supported()
    # Do not use resolve(): every ancestor must itself be a real directory.
    absolute = os.path.abspath(os.fspath(root))
    fd = os.open("/", _directory_flags())
    try:
        for part in absolute.split("/"):
            if not part:
                continue
            child = os.open(part, _directory_flags(), dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def _parent(root_fd: int, parts: tuple[str, ...], *, create: bool = False):
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, _directory_flags(), dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name:
        raise ValueError("Invalid archive path")
    # tar directories may carry one conventional trailing slash.
    name = name[:-1] if name.endswith("/") else name
    parts = tuple(name.split("/"))
    if (len(name) > 4096 or not parts or len(parts) > 256
            or any(p in ("", ".", "..") for p in parts)
            or any(":" in p for p in parts)):
        raise ValueError(f"Unsafe archive path: {name!r}")
    return parts


def _link_target(parent: tuple[str, ...], target: str) -> None:
    if (not target or target.startswith("/") or "\\" in target
            or "\0" in target or ":" in target or len(target) > 4096):
        raise ValueError("Unsafe symbolic link target")
    stack = list(parent)
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise ValueError("Symbolic link escapes workspace")
            stack.pop()
        else:
            stack.append(part)


def _validate_links(links: dict[tuple[str, ...], str]) -> None:
    """Check composed links too: lexical containment of each link is insufficient."""
    for path, target in links.items():
        stack = list(path[:-1])
        pending = target.split("/")
        followed = 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                if not stack:
                    raise ValueError("Chained symbolic link escapes workspace")
                stack.pop()
                continue
            stack.append(part)
            replacement = links.get(tuple(stack))
            if replacement is not None:
                followed += 1
                if followed > 40:
                    raise ValueError("Symbolic link cycle or excessive link depth")
                stack.pop()
                pending = replacement.split("/") + pending


def pack_tree(root: Path, fileobj, *, exclude_top=()) -> None:
    """Write a tar snapshot without following source symbolic links."""
    excluded = frozenset(exclude_top)
    if any(not isinstance(name, str) or not name or name in ('.', '..')
           or '/' in name or '\\' in name for name in excluded):
        raise ValueError('Archive exclusions must be literal top-level names')
    root_fd = _open_root(root)
    links: dict[tuple[str, ...], str] = {}
    count = total = 0
    try:
        with tarfile.open(fileobj=fileobj, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            def walk(fd: int, prefix: tuple[str, ...]) -> None:
                nonlocal count, total
                # scandir(fd) reads the opened directory, never its replaceable path.
                with os.scandir(fd) as entries:
                    names = []
                    for entry in entries:
                        if not prefix and entry.name in excluded:
                            continue
                        names.append(entry.name)
                        if count + len(names) > MAX_MEMBERS:
                            raise ValueError("Workspace contains too many entries")
                    names.sort()
                for name in names:
                    parts = prefix + (name,)
                    _parts("/".join(parts))
                    count += 1
                    if count > MAX_MEMBERS:
                        raise ValueError("Workspace contains too many entries")
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    member = tarfile.TarInfo("/".join(parts))
                    member.mode = stat.S_IMODE(info.st_mode) & 0o777
                    member.mtime = max(0, int(info.st_mtime))
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(name, _directory_flags(), dir_fd=fd)
                        try:
                            member.type = tarfile.DIRTYPE
                            archive.addfile(member)
                            walk(child, parts)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                                         | os.O_NONBLOCK, dir_fd=fd)
                        with os.fdopen(opened, "rb") as source:
                            actual = os.fstat(source.fileno())
                            if not stat.S_ISREG(actual.st_mode):
                                raise ValueError("Source file changed type during snapshot")
                            member.size = actual.st_size
                            member.mode = stat.S_IMODE(actual.st_mode) & 0o777
                            total += member.size
                            if total > MAX_BYTES:
                                raise ValueError("Workspace archive exceeds size limit")
                            archive.addfile(member, source)
                    elif stat.S_ISLNK(info.st_mode):
                        target = os.readlink(name, dir_fd=fd)
                        _link_target(prefix, target)
                        links[parts] = target
                        member.type = tarfile.SYMTYPE
                        member.linkname = target
                        archive.addfile(member)
                    else:
                        raise ValueError("Workspace contains a special device, socket or pipe")
            walk(root_fd, ())
            _validate_links(links)
    finally:
        os.close(root_fd)


def unpack_tree(fileobj, root: Path) -> None:
    """Extract into an existing empty directory, rejecting unsafe tar members."""
    root_fd = _open_root(root)
    try:
        _unpack_fd(fileobj, root_fd)
    finally:
        os.close(root_fd)


def _unpack_fd(fileobj, root_fd: int) -> None:
    seen: set[tuple[str, ...]] = set()
    directories: dict[tuple[str, ...], int] = {}
    links: dict[tuple[str, ...], str] = {}
    total = 0
    if os.listdir(root_fd):
        raise ValueError("Archive destination must be empty")
    with tarfile.open(fileobj=fileobj, mode="r|*", tarinfo=_TransportInfo) as archive:
        for member in archive:
            parts = _parts(member.name)
            if parts in seen:
                raise ValueError("Duplicate archive entry")
            seen.add(parts)
            if len(seen) > MAX_MEMBERS:
                raise ValueError("Archive contains too many entries")
            if member.mode < 0 or member.mode & ~0o777:
                raise ValueError("Archive contains special permission bits")
            if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE,
                                    tarfile.DIRTYPE, tarfile.SYMTYPE)
                    or member.sparse is not None
                    or any(k.startswith("GNU.sparse") for k in member.pax_headers)):
                raise ValueError("Unsupported archive entry type")
            if member.size < 0 or (not member.isfile() and member.size != 0):
                raise ValueError("Invalid archive entry size")
            total += member.size
            if total > MAX_BYTES:
                raise ValueError("Archive exceeds size limit")
            with _parent(root_fd, parts, create=True) as (parent_fd, name):
                if member.isdir():
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        # Only an implicit, real parent directory may already exist.
                        check = os.open(name, _directory_flags(), dir_fd=parent_fd)
                        os.close(check)
                    directories[parts] = member.mode
                elif member.issym():
                    _link_target(parts[:-1], member.linkname)
                    os.symlink(member.linkname, name, dir_fd=parent_fd)
                    links[parts] = member.linkname
                else:
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
                    with os.fdopen(fd, "wb") as destination:
                        source = archive.extractfile(member)
                        if source is None:
                            raise ValueError("Archive file has no content")
                        with source:
                            shutil.copyfileobj(source, destination, length=1024 * 1024)
                        os.fchmod(destination.fileno(), member.mode | 0o600)
                        destination.flush()
                        os.fsync(destination.fileno())
        _validate_links(links)
    # Apply restrictive directory permissions only after all writes finish.
    for parts in sorted(directories, key=len, reverse=True):
        with _parent(root_fd, parts) as (parent_fd, name):
            fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            try:
                os.fchmod(fd, directories[parts] | 0o700)
            finally:
                os.close(fd)


def _retry_fs(operation, *args, **kwargs):
    """Retry short Windows bind sharing conflicts; never bypass a failed check."""
    delays = (0.05, 0.1, 0.2, 0.4, 0.8)
    transient = {errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY}
    for attempt in range(len(delays) + 1):
        try:
            return operation(*args, **kwargs)
        except OSError as exc:
            if exc.errno not in transient or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def _remove_tree(parent_fd: int, name: str) -> None:
    """Remove through opened directories, including read-only directory trees."""
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        _retry_fs(os.unlink, name, dir_fd=parent_fd)
        return
    fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        mode = stat.S_IMODE(os.fstat(fd).st_mode)
        # Windows-created bind directories can be root-owned yet writable to
        # the worker. chmod requires ownership; deletion only requires access.
        # Repair restrictive owned directories, but do not chmod an already
        # permissive directory merely to set its existing bits again.
        if mode & 0o700 != 0o700:
            os.fchmod(fd, mode | 0o700)
        for child in os.listdir(fd):
            _remove_tree(fd, child)
    finally:
        os.close(fd)
    _retry_fs(os.rmdir, name, dir_fd=parent_fd)


def _exchange(parent_fd: int, first: str, second: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    exchange = getattr(libc, "renameat2", None)
    if exchange is None:
        raise OSError(errno.ENOSYS, "Linux renameat2 is unavailable")
    exchange.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_char_p, ctypes.c_uint)
    exchange.restype = ctypes.c_int
    if exchange(parent_fd, os.fsencode(first), parent_fd, os.fsencode(second), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _sync_directories(fd: int) -> None:
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=fd)
            try:
                _sync_directories(child)
            finally:
                os.close(child)
    os.fsync(fd)


def _tree_exists(parent_fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Workspace recovery entry is not a real directory")
    fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    os.close(fd)
    return True


def _read_journal(parent_fd: int, workspace_name: str):
    try:
        fd = os.open(_JOURNAL, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2048 or info.st_nlink != 1:
            raise ValueError("Invalid workspace recovery journal")
        raw = os.read(fd, 2049)
        if len(raw) > 2048:
            raise ValueError("Workspace recovery journal exceeds size limit")
        try:
            marker = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Malformed workspace recovery journal") from exc
        if (not isinstance(marker, dict)
                or set(marker) != {"version", "workspace", "incoming", "previous"}
                or type(marker.get("version")) is not int or marker["version"] != 1
                or marker.get("workspace") != workspace_name
                or not isinstance(marker.get("incoming"), str)
                or not isinstance(marker.get("previous"), str)):
            raise ValueError("Invalid workspace recovery journal fields")
        match = re.fullmatch(r"\.lab-incoming-([0-9a-f]{32})", marker["incoming"])
        if not match or marker["previous"] != f".lab-previous-{match.group(1)}":
            raise ValueError("Unsafe workspace recovery journal names")
        return marker
    finally:
        os.close(fd)


def _write_journal(parent_fd: int, marker: dict) -> None:
    temporary = f".lab-workspace-journal-{uuid.uuid4().hex}.json"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(marker, separators=(",", ":")).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        # A project lock excludes legitimate competing swaps. Never overwrite an
        # existing marker, including a symlink, discovered before publication.
        try:
            os.stat(_JOURNAL, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Workspace recovery journal already exists")
        _retry_fs(os.rename, temporary, _JOURNAL, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try:
            _retry_fs(os.unlink, temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _recover_fd(parent_fd: int, workspace_name: str) -> None:
    marker = _read_journal(parent_fd, workspace_name)
    if marker is None:
        return
    incoming, previous = marker["incoming"], marker["previous"]
    current = _tree_exists(parent_fd, workspace_name)
    staged = _tree_exists(parent_fd, incoming)
    old = _tree_exists(parent_fd, previous)
    if current and staged and not old:
        _retry_fs(os.rename, workspace_name, previous, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        current, old = False, True
    if not current and staged and old:
        _retry_fs(os.rename, incoming, workspace_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        current, staged = True, False
    if not current and not staged and old:
        # A missing staging tree is corruption, but the original source is still
        # recoverable. Restore it rather than leaving the project inaccessible.
        _retry_fs(os.rename, previous, workspace_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        current, old = True, False
    if not current or staged:
        raise ValueError("Ambiguous workspace recovery state; directories preserved")
    if old:
        _remove_tree(parent_fd, previous)
        os.fsync(parent_fd)
    _retry_fs(os.unlink, _JOURNAL, dir_fd=parent_fd)
    os.fsync(parent_fd)


def recover_tree(workspace: Path) -> None:
    """Finish an interrupted journaled replacement while holding the project lock.

    No journal means no action, including on non-Linux development hosts. An
    existing marker is read and acted upon only through a no-follow parent FD.
    """
    workspace = Path(os.path.abspath(os.fspath(workspace)))
    _parts(workspace.name)
    if not os.path.lexists(workspace.parent / _JOURNAL):
        return
    parent_fd = _open_root(workspace.parent)
    try:
        _recover_fd(parent_fd, workspace.name)
    finally:
        os.close(parent_fd)


def replace_tree(fileobj, workspace: Path) -> None:
    """Validate a snapshot in a sibling directory, then replace the workspace.

    The caller must exclude concurrent project writers. Prefer atomic exchange;
    filesystems without exchange use a durable recovery journal. A rejected
    archive never replaces the old tree; interrupted swaps finish in recover_tree.
    """
    workspace = Path(os.path.abspath(os.fspath(workspace)))
    recover_tree(workspace)
    name = workspace.name
    _parts(name)
    parent_fd = _open_root(workspace.parent)
    token = uuid.uuid4().hex
    temporary = f".lab-incoming-{token}"
    previous = f".lab-previous-{token}"
    installed = False
    journal_active = False
    try:
        current_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        os.close(current_fd)
        os.mkdir(temporary, 0o700, dir_fd=parent_fd)
        incoming_fd = os.open(temporary, _directory_flags(), dir_fd=parent_fd)
        try:
            _unpack_fd(fileobj, incoming_fd)
            os.fchmod(incoming_fd, 0o755)
            _sync_directories(incoming_fd)
        finally:
            os.close(incoming_fd)
        # O_NOFOLLOW checked again immediately before descriptor-relative rename.
        current_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        os.close(current_fd)
        try:
            _retry_fs(_exchange, parent_fd, temporary, name)
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EXDEV}
            if exc.errno not in unsupported:
                raise
            journal_active = True
            _write_journal(parent_fd, {"version": 1, "workspace": name,
                                      "incoming": temporary, "previous": previous})
            _recover_fd(parent_fd, name)
            journal_active = False
            installed = True
        else:
            installed = True
            os.fsync(parent_fd)
            _remove_tree(parent_fd, temporary)
            os.fsync(parent_fd)
    finally:
        if not installed and not journal_active:
            try:
                _remove_tree(parent_fd, temporary)
            except FileNotFoundError:
                pass
        os.close(parent_fd)
