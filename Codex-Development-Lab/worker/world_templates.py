"""Trusted, immutable Minecraft world templates and fresh per-execution copies.

The store is a verifier-owned directory, never a writable project mount. The
generation callback must run real Minecraft and close it before returning. File
validation here establishes a usable saved-world container, never product PASS.
Only the returned clone is exposed to the tested client.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import ctypes
from typing import Callable
import uuid


STORE_VERSION = 1
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL = frozenset({"level.dat", "region", "entities", "poi", "data", "datapacks",
                        "dimensions", "DIM-1", "DIM1", "generated"})
_PRIVATE_NAMES = frozenset({"playerdata", "advancements", "stats", "session.lock",
                            "servers.dat", "servers.dat_old", "usercache.json",
                            "whitelist.json", "ops.json", "banned-players.json",
                            "banned-ips.json", "options.txt", "config", "auth.json"})


class WorldTemplateError(ValueError):
    """Invalid/mutated saved world or an unsafe filesystem entry."""


@dataclass(frozen=True)
class Limits:
    """Filesystem safety limits, not limits on agent work or product behavior."""
    max_files: int = 32768
    max_file_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    max_level_bytes: int = 32 * 1024 * 1024
    max_depth: int = 32


def normalized_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise WorldTemplateError("World profile must be an object")
    value = {"worldgen_fingerprint": "vanilla", "dependency_fingerprint": "vanilla", **profile}
    for key in ("minecraft_version", "world_profile", "bootstrap_revision"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise WorldTemplateError("Missing world profile field: " + key)
    if "allow_commands" in value and not isinstance(value["allow_commands"], bool):
        raise WorldTemplateError("allow_commands must be a boolean")
    if "display_name" in value:
        name = value["display_name"]
        if (not isinstance(name, str) or not name.strip() or len(name) > 80
                or any(ord(character) < 32 or ord(character) == 127 for character in name)):
            raise WorldTemplateError("display_name must be a nonempty string of at most 80 characters without controls")
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorldTemplateError("World profile is not JSON data") from exc
    if len(raw) > 65536:
        raise WorldTemplateError("World profile is too large")
    return json.loads(raw)


def profile_key(profile: dict) -> str:
    raw = json.dumps({"store_version": STORE_VERSION, "profile": normalized_profile(profile)},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _flags(directory=False):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise WorldTemplateError("World template storage requires POSIX no-follow directory operations")
    return os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)


def _absolute(path) -> Path:
    p = Path(path)
    if ".." in p.parts:
        raise WorldTemplateError("Parent traversal is not a world path")
    p = p.absolute()
    for part in [*reversed(p.parents), p]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise WorldTemplateError("World path contains a symlink")
    return p


def _directory(path) -> int:
    return os.open(_absolute(path), _flags(True))


def _child_dir(parent: int, name: str, *, create=False, mode=0o700) -> int:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise WorldTemplateError("Invalid directory component")
    if create:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, _flags(True), dir_fd=parent)
    if create:
        os.fchmod(fd, mode)
    return fd


def _read(fd: int, limit: int) -> bytes:
    parts = []
    total = 0
    while True:
        part = os.read(fd, min(1024 * 1024, limit + 1 - total))
        if not part:
            return b"".join(parts)
        total += len(part)
        if total > limit:
            raise WorldTemplateError("World file exceeds filesystem safety budget")
        parts.append(part)


def _read_file(parent: int, name: str, limit: int) -> bytes:
    fd = os.open(name, _flags(), dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise WorldTemplateError("World files must be independent regular files")
        if before.st_size > limit:
            raise WorldTemplateError("World file exceeds filesystem safety budget")
        value = _read(fd, limit)
        after = os.fstat(fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise WorldTemplateError("World file changed while reading")
        return value
    finally:
        os.close(fd)


def _write_file(parent: int, name: str, raw: bytes, mode=0o600):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 mode, dir_fd=parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def _remove_owned_tree(parent: int, name: str):
    """Unlink entries through descriptors; never follow a cleanup-time symlink."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    fd = _child_dir(parent, name)
    try:
        for child in os.listdir(fd):
            _remove_owned_tree(fd, child)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def _allowed(parts: tuple[str, ...]) -> bool:
    return bool(parts and parts[0] in _TOP_LEVEL and not any(
        p.casefold() in _PRIVATE_NAMES or p.startswith(".") for p in parts))


def _walk(fd: int, limits: Limits, prefix=(), *, strict=False, budget=None):
    if len(prefix) > limits.max_depth:
        raise WorldTemplateError("World directory depth exceeds safety budget")
    budget = [0] if budget is None else budget
    names = os.listdir(fd)
    if len(names) > limits.max_files:
        raise WorldTemplateError("World directory exceeds filesystem safety budget")
    for name in sorted(names):
        parts = (*prefix, name)
        if not _allowed(parts):
            if strict:
                raise WorldTemplateError("Unexpected file inside published world template")
            continue
        budget[0] += 1
        if budget[0] > limits.max_files:
            raise WorldTemplateError("World entry count exceeds filesystem safety budget")
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise WorldTemplateError("Symlink inside saved world")
        if stat.S_ISDIR(info.st_mode):
            child = _child_dir(fd, name)
            try:
                yield from _walk(child, limits, parts, strict=strict, budget=budget)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise WorldTemplateError("Hardlink inside saved world")
            yield parts, _read_file(fd, name, limits.max_file_bytes)
        else:
            raise WorldTemplateError("Special file inside saved world")


class _NBT:
    """Small bounded NBT reader/writer so embedded singleplayer identity is removed."""
    def __init__(self, raw: bytes):
        self.stream = io.BytesIO(raw)
        self.nodes = 0

    def read(self, count):
        if count < 0 or count > len(self.stream.getbuffer()):
            raise WorldTemplateError("Invalid NBT length")
        raw = self.stream.read(count)
        if len(raw) != count:
            raise WorldTemplateError("Truncated NBT")
        return raw

    def number(self, fmt):
        return struct.unpack(">" + fmt, self.read(struct.calcsize(">" + fmt)))[0]

    def string(self):
        try:
            return self.read(self.number("H")).decode("utf-8")
        except UnicodeError as exc:
            raise WorldTemplateError("Invalid NBT string") from exc

    def payload(self, kind, depth=0):
        self.nodes += 1
        if depth > 64 or self.nodes > 1000000:
            raise WorldTemplateError("NBT complexity exceeds safety budget")
        if kind in (1, 2, 3, 4, 5, 6):
            return self.number({1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[kind])
        if kind in (7, 11, 12):
            count = self.number("i")
            width = {7: 1, 11: 4, 12: 8}[kind]
            return self.read(count * width) if count >= 0 else self.read(-1)
        if kind == 8:
            return self.string()
        if kind == 9:
            subtype, count = self.number("B"), self.number("i")
            if count < 0 or count > 1000000 or (subtype == 0 and count):
                raise WorldTemplateError("Invalid NBT list")
            return subtype, [self.payload(subtype, depth + 1) for _ in range(count)]
        if kind == 10:
            values = {}
            while True:
                subtype = self.number("B")
                if subtype == 0:
                    return values
                name = self.string()
                if name in values:
                    raise WorldTemplateError("Duplicate NBT compound key")
                values[name] = (subtype, self.payload(subtype, depth + 1))
        raise WorldTemplateError("Unknown NBT tag")

    @staticmethod
    def encode_string(value):
        raw = value.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw

    @classmethod
    def encode(cls, kind, value):
        if kind in (1, 2, 3, 4, 5, 6):
            return struct.pack(">" + {1: "b", 2: "h", 3: "i", 4: "q", 5: "f", 6: "d"}[kind], value)
        if kind in (7, 11, 12):
            return struct.pack(">i", len(value) // {7: 1, 11: 4, 12: 8}[kind]) + value
        if kind == 8:
            return cls.encode_string(value)
        if kind == 9:
            subtype, items = value
            return struct.pack(">Bi", subtype, len(items)) + b"".join(cls.encode(subtype, i) for i in items)
        if kind == 10:
            return b"".join(bytes([tag]) + cls.encode_string(name) + cls.encode(tag, v)
                            for name, (tag, v) in value.items()) + b"\0"
        raise WorldTemplateError("Unknown NBT tag")


def _level(raw: bytes, profile: dict, limits: Limits, *, sanitize=False) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(limits.max_level_bytes + 1)
    except (OSError, EOFError) as exc:
        raise WorldTemplateError("level.dat is not complete gzip NBT") from exc
    if len(decoded) > limits.max_level_bytes:
        raise WorldTemplateError("Expanded level.dat exceeds safety budget")
    parser = _NBT(decoded)
    if parser.number("B") != 10:
        raise WorldTemplateError("level.dat root must be a compound")
    name = parser.string()
    root = parser.payload(10)
    if parser.stream.read(1):
        raise WorldTemplateError("Trailing data in level.dat")
    data = root.get("Data")
    if not data or data[0] != 10:
        raise WorldTemplateError("level.dat has no world Data compound")
    data = data[1]
    version = data.get("Version")
    if not version or version[0] != 10 or version[1].get("Name") != (8, profile["minecraft_version"]):
        raise WorldTemplateError("Saved-world Minecraft version does not match profile")
    if data.get("DataVersion", (None,))[0] != 3:
        raise WorldTemplateError("level.dat has no DataVersion")
    if sanitize:
        data.pop("Player", None)
        if "allow_commands" in profile:
            data["allowCommands"] = (1, int(profile["allow_commands"]))
        if "display_name" in profile:
            data["LevelName"] = (8, profile["display_name"])
        return gzip.compress(bytes([10]) + _NBT.encode_string(name) + _NBT.encode(10, root), mtime=0)
    if "Player" in data:
        raise WorldTemplateError("Template contains embedded player data")
    if "allow_commands" in profile and data.get("allowCommands") != (1, int(profile["allow_commands"])):
        raise WorldTemplateError("World command permissions do not match profile")
    if "display_name" in profile and data.get("LevelName") != (8, profile["display_name"]):
        raise WorldTemplateError("World display name does not match profile")
    return raw


def _region(raw: bytes):
    if len(raw) < 8192 or len(raw) % 4096:
        raise WorldTemplateError("Truncated region file")
    allocated = set()
    chunks = 0
    for start in range(0, 4096, 4):
        location = int.from_bytes(raw[start:start + 4], "big")
        if location == 0:
            continue
        sector, count = location >> 8, location & 255
        if sector < 2 or not count or sector + count > len(raw) // 4096:
            raise WorldTemplateError("Region chunk allocation is outside the file")
        indexes = set(range(sector, sector + count))
        if indexes & allocated:
            raise WorldTemplateError("Overlapping region chunks")
        allocated.update(indexes)
        position = sector * 4096
        length = int.from_bytes(raw[position:position + 4], "big")
        compression = raw[position + 4]
        if not 1 < length <= count * 4096 - 4 or compression not in (1, 2, 3, 4):
            raise WorldTemplateError("Invalid region chunk payload")
        chunks += 1
    return chunks


def _inventory(fd: int, profile: dict, limits: Limits) -> list[dict]:
    entries = []
    total = chunks = 0
    has_level = False
    for parts, raw in _walk(fd, limits, strict=True):
        total += len(raw)
        if len(entries) >= limits.max_files or total > limits.max_total_bytes:
            raise WorldTemplateError("World exceeds filesystem safety budget")
        if parts == ("level.dat",):
            _level(raw, profile, limits)
            has_level = True
        if len(parts) == 2 and parts[0] == "region" and parts[1].endswith(".mca"):
            chunks += _region(raw)
        entries.append({"path": "/".join(parts), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    if not has_level or not chunks:
        raise WorldTemplateError("World must contain level.dat and generated Overworld chunks")
    return entries


def _publish_noreplace(source_parent: int, source: str, target_parent: int, target: str):
    """Linux atomic publication that cannot replace even an empty existing directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise WorldTemplateError("Atomic no-replace world publication is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_parent, os.fsencode(source), target_parent, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), target)


def _copy(source: int, destination: int, profile: dict, limits: Limits, *, sanitize=False,
          file_mode=0o600, directory_mode=0o700) -> list[dict]:
    entries = []
    total = 0
    for parts, raw in _walk(source, limits):
        if sanitize and parts == ("level.dat",):
            raw = _level(raw, profile, limits, sanitize=True)
        total += len(raw)
        if len(entries) >= limits.max_files or total > limits.max_total_bytes:
            raise WorldTemplateError("World exceeds filesystem safety budget")
        target = os.dup(destination)
        try:
            for component in parts[:-1]:
                child = _child_dir(target, component, create=True, mode=directory_mode)
                os.close(target)
                target = child
            _write_file(target, parts[-1], raw, mode=file_mode)
        finally:
            os.close(target)
        entries.append({"path": "/".join(parts), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return entries


class TemplateStore:
    def __init__(self, root, *, limits: Limits | None = None):
        _flags()
        self.root = _absolute(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.limits = limits or Limits()
        fd = _directory(self.root)
        try:
            os.fchmod(fd, 0o700)
            for name in ("templates", "locks", "staging", "quarantine"):
                child = _child_dir(fd, name, create=True)
                os.close(child)
        finally:
            os.close(fd)

    @contextmanager
    def _locked(self, key):
        import fcntl
        if not _HEX.fullmatch(key):
            raise WorldTemplateError("Invalid template key")
        root = _directory(self.root)
        locks = _child_dir(root, "locks")
        lock = os.open(key + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=locks)
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_nlink != 1:
                raise WorldTemplateError("Invalid template lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield root
        finally:
            os.close(lock)
            os.close(locks)
            os.close(root)

    def _validate(self, templates: int, key: str, profile: dict):
        template = _child_dir(templates, key)
        try:
            raw = _read_file(template, "manifest.json", 8 * 1024 * 1024)
            try:
                record = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise WorldTemplateError("Invalid template manifest") from exc
            if (not isinstance(record, dict) or record.get("store_version") != STORE_VERSION or record.get("key") != key
                    or record.get("profile") != profile):
                raise WorldTemplateError("Template profile mismatch")
            world = _child_dir(template, "world")
            try:
                actual = _inventory(world, profile, self.limits)
            finally:
                os.close(world)
            if record.get("files") != actual:
                raise WorldTemplateError("Template files changed since publication")
            return record, hashlib.sha256(raw).hexdigest()
        finally:
            os.close(template)

    def _quarantine(self, root: int, templates: int, key: str):
        quarantine = _child_dir(root, "quarantine")
        try:
            name = key + "-" + uuid.uuid4().hex
            os.rename(key, name, src_dir_fd=templates, dst_dir_fd=quarantine)
            previous = sorted((n for n in os.listdir(quarantine) if n.startswith(key + "-")))
            # Retain a few forensic copies, without accumulating corrupt worlds forever.
            for old in previous[:-3]:
                _remove_owned_tree(quarantine, old)
        finally:
            os.close(quarantine)

    def _descriptor(self, key, profile, record, digest, **extra):
        return {"store_version": STORE_VERSION, "key": key, "profile": profile,
                "path": str(self.root / "templates" / key / "world"),
                "manifest_sha256": digest, "files": len(record["files"]),
                "bytes": sum(f["bytes"] for f in record["files"]),
                "provenance": "Verifier-owned saved-world template; not product acceptance evidence.", **extra}

    def ensure(self, profile: dict, prepare: Callable[[Path], None]) -> dict:
        """Return a validated cached template, or generate and atomically publish one."""
        profile = normalized_profile(profile)
        key = profile_key(profile)
        with self._locked(key) as root:
            templates = _child_dir(root, "templates")
            staging = _child_dir(root, "staging")
            token = None
            try:
                # A killed generator cannot execute its finally block. The key's
                # exclusive lock means no surviving owner may publish these names.
                for stale in os.listdir(staging):
                    if re.fullmatch(re.escape(key) + r"-[0-9a-f]{32}", stale):
                        _remove_owned_tree(staging, stale)
                try:
                    record, digest = self._validate(templates, key, profile)
                    return self._descriptor(key, profile, record, digest, reused=True)
                except FileNotFoundError:
                    if key in os.listdir(templates):
                        self._quarantine(root, templates, key)
                except (WorldTemplateError, OSError):
                    self._quarantine(root, templates, key)
                token = key + "-" + uuid.uuid4().hex
                temporary = _child_dir(staging, token, create=True)
                try:
                    generated = _child_dir(temporary, "generated", create=True)
                    os.close(generated)
                    prepare(self.root / "staging" / token / "generated")
                    generated = _child_dir(temporary, "generated")
                    world = _child_dir(temporary, "world", create=True)
                    try:
                        _copy(generated, world, profile, self.limits, sanitize=True)
                        entries = _inventory(world, profile, self.limits)
                    finally:
                        os.close(generated)
                        os.close(world)
                    record = {"store_version": STORE_VERSION, "key": key, "profile": profile,
                              "files": entries, "proof_kind": "saved-world-structure"}
                    raw = _canonical(record)
                    _write_file(temporary, "manifest.json", raw)
                    _remove_owned_tree(temporary, "generated")
                    os.fsync(temporary)
                    _publish_noreplace(staging, token, templates, key)
                    os.fsync(templates)
                    token = None
                    return self._descriptor(key, profile, record, hashlib.sha256(raw).hexdigest(), reused=False)
                finally:
                    os.close(temporary)
            finally:
                if token:
                    _remove_owned_tree(staging, token)
                os.close(staging)
                os.close(templates)

    def clone(self, profile: dict, destination, *, file_mode=0o600, directory_mode=0o700) -> dict:
        """Copy a validated template into a new run directory; never replace a world."""
        if file_mode not in (0o600, 0o640, 0o644, 0o660, 0o666) or directory_mode not in (
                0o700, 0o750, 0o755, 0o770, 0o777):
            raise WorldTemplateError("Unsupported clone permissions")
        profile = normalized_profile(profile)
        key = profile_key(profile)
        destination = _absolute(destination)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", destination.name):
            raise WorldTemplateError("Invalid clone name")
        if destination == self.root or self.root in destination.parents:
            raise WorldTemplateError("A run world cannot replace template storage")
        with self._locked(key) as root:
            templates = _child_dir(root, "templates")
            parent = _directory(destination.parent)
            template = source = target = None
            token = None
            try:
                record, digest = self._validate(templates, key, profile)
                if destination.name in os.listdir(parent):
                    raise FileExistsError("Run world already exists: " + str(destination))
                template = _child_dir(templates, key)
                source = _child_dir(template, "world")
                token = ".world-clone-" + uuid.uuid4().hex
                target = _child_dir(parent, token, create=True, mode=directory_mode)
                copied = _copy(source, target, profile, self.limits,
                               file_mode=file_mode, directory_mode=directory_mode)
                if copied != record["files"]:
                    raise WorldTemplateError("Template changed during clone")
                os.fsync(target)
                _publish_noreplace(parent, token, parent, destination.name)
                os.fsync(parent)
                token = None
                return self._descriptor(key, profile, record, digest,
                                        path=str(destination), template_reused=True, copy_kind="independent-files")
            finally:
                for fd in (target, source, template):
                    if fd is not None:
                        os.close(fd)
                if token:
                    _remove_owned_tree(parent, token)
                os.close(parent)
                os.close(templates)
