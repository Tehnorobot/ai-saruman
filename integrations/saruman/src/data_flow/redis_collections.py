"""Redis-backed collection wrappers that mimic Python list/dict/Queue interfaces.

Drop-in replacements for multiprocessing.Manager proxy objects.
All values are JSON-serialized for storage.

Pickle-safe: no Redis connection objects stored on instances.
Each access lazily gets a connection from the shared pool via get_redis().
"""

import json
import time
from typing import Any, Iterator, Optional, Union

import redis

from data_flow.redis_backend import get_redis, make_key

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: Union[bytes, str, None]) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def _r() -> redis.Redis:
    """Shorthand for getting a Redis client. Pickle-safe (no stored state)."""
    return get_redis()


# ---------------------------------------------------------------------------
# RedisList — replaces manager.list() used as FIFO queue (chat_queue, tts_queue)
# ---------------------------------------------------------------------------

class RedisList:
    """Redis List that mimics Python list. Pickle-safe (no stored connection)."""

    def __init__(self, name: str, **_kwargs):
        self._key = make_key(f"list:{name}")

    def append(self, value: Any):
        _r().rpush(self._key, _dumps(value))

    def put(self, value: Any, timeout: float = None):
        self.append(value)

    def pop(self, index: int = -1) -> Any:
        if index == 0:
            raw = _r().lpop(self._key)
        else:
            raw = _r().rpop(self._key)
        return _loads(raw)

    def get(self, timeout: float = 0) -> Any:
        """Blocking pop from left. Returns None on timeout."""
        if timeout <= 0:
            raw = _r().lpop(self._key)
        else:
            result = _r().blpop(self._key, timeout=int(max(1, timeout)))
            raw = result[1] if result else None
        return _loads(raw)

    def extend(self, values):
        if values:
            pipe = _r().pipeline()
            for v in values:
                pipe.rpush(self._key, _dumps(v))
            pipe.execute()

    def __len__(self) -> int:
        return _r().llen(self._key)

    def __getitem__(self, index_or_slice):
        if isinstance(index_or_slice, slice):
            start = index_or_slice.start or 0
            stop = index_or_slice.stop
            if stop is None:
                stop = -1
            else:
                stop = stop - 1
            return [_loads(x) for x in _r().lrange(self._key, start, stop)]
        raw = _r().lindex(self._key, index_or_slice)
        if raw is None:
            raise IndexError(f"list index {index_or_slice} out of range")
        return _loads(raw)

    def __setitem__(self, index_or_slice, value):
        if isinstance(index_or_slice, slice):
            r = _r()
            r.delete(self._key)
            if isinstance(value, list) and value:
                pipe = r.pipeline()
                for v in value:
                    pipe.rpush(self._key, _dumps(v))
                pipe.execute()
            return
        try:
            _r().lset(self._key, index_or_slice, _dumps(value))
        except redis.ResponseError:
            raise IndexError(f"list index {index_or_slice} out of range")

    def __iter__(self):
        for raw in _r().lrange(self._key, 0, -1):
            yield _loads(raw)

    def __reversed__(self):
        for raw in reversed(_r().lrange(self._key, 0, -1)):
            yield _loads(raw)

    def __bool__(self) -> bool:
        return len(self) > 0

    def copy(self) -> list:
        return list(self)

    def clear(self):
        _r().delete(self._key)


# ---------------------------------------------------------------------------
# RedisCtxChat — replaces manager.list() for ctx_chat (sorted set by timestamp)
# ---------------------------------------------------------------------------

class RedisCtxChat:
    """Redis Sorted Set for ctx_chat. Pickle-safe."""

    def __init__(self, name: str = "ctx_chat",
                 pubsub_channel: str = None, **_kwargs):
        self._key = make_key(name)
        self._pubsub_channel = pubsub_channel or make_key("events:ctx_chat")

    def append(self, event_dict: dict):
        r = _r()
        ts = event_dict.get("processing_timestamp", time.time_ns())
        r.zadd(self._key, {_dumps(event_dict): float(ts)})
        r.publish(self._pubsub_channel, str(ts))

    def __len__(self) -> int:
        return _r().zcard(self._key)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start = index.start or 0
            stop = -1 if index.stop is None else index.stop - 1
            return [_loads(x) for x in _r().zrange(self._key, start, stop)]
        raw_list = _r().zrange(self._key, index, index)
        if not raw_list:
            raise IndexError(f"ctx_chat index {index} out of range")
        return _loads(raw_list[0])

    def __setitem__(self, index_or_slice, value):
        r = _r()
        if isinstance(index_or_slice, slice):
            if isinstance(value, list):
                pipe = r.pipeline()
                pipe.delete(self._key)
                for item in value:
                    ts = item.get("processing_timestamp", time.time_ns())
                    pipe.zadd(self._key, {_dumps(item): float(ts)})
                pipe.execute()
            return
        old_list = r.zrange(self._key, index_or_slice, index_or_slice)
        if not old_list:
            raise IndexError(f"ctx_chat index {index_or_slice} out of range")
        ts = value.get("processing_timestamp", time.time_ns())
        pipe = r.pipeline()
        pipe.zrem(self._key, old_list[0])
        pipe.zadd(self._key, {_dumps(value): float(ts)})
        pipe.execute()
        r.publish(self._pubsub_channel, str(ts))

    def pop(self, index: int = -1) -> Any:
        r = _r()
        raw_list = r.zrange(self._key, index, index)
        if not raw_list:
            raise IndexError("pop from empty ctx_chat")
        r.zrem(self._key, raw_list[0])
        return _loads(raw_list[0])

    def get_by_timestamp(self, timestamp: int) -> Optional[dict]:
        results = _r().zrangebyscore(self._key, float(timestamp), float(timestamp))
        return _loads(results[0]) if results else None

    def modify_by_timestamp(self, timestamp: int, updates: dict) -> bool:
        r = _r()
        old = r.zrangebyscore(self._key, float(timestamp), float(timestamp))
        if not old:
            return False
        existing = _loads(old[0]) or {}
        existing.update(updates)
        new_ts = existing.get("processing_timestamp", timestamp)
        pipe = r.pipeline()
        pipe.zrem(self._key, old[0])
        pipe.zadd(self._key, {_dumps(existing): float(new_ts)})
        pipe.execute()
        r.publish(self._pubsub_channel, str(new_ts))
        return True

    def remove_by_timestamp(self, timestamp: int) -> bool:
        r = _r()
        old = r.zrangebyscore(self._key, float(timestamp), float(timestamp))
        if not old:
            return False
        r.zrem(self._key, old[0])
        return True

    def trim(self, keep_last_n: int):
        total = len(self)
        if total > keep_last_n:
            _r().zremrangebyrank(self._key, 0, total - keep_last_n - 1)

    def __iter__(self) -> Iterator:
        for raw in _r().zrange(self._key, 0, -1):
            yield _loads(raw)

    def __reversed__(self) -> Iterator:
        for raw in _r().zrevrange(self._key, 0, -1):
            yield _loads(raw)

    def __bool__(self) -> bool:
        return len(self) > 0

    def clear(self):
        _r().delete(self._key)


# ---------------------------------------------------------------------------
# RedisDict — replaces manager.dict() (env, states, voice, vtube, game, votes)
# ---------------------------------------------------------------------------

class RedisDict:
    """Redis Hash that mimics Python dict. Pickle-safe."""

    def __init__(self, name: str, defaults: dict = None, **_kwargs):
        self._key = make_key(f"dict:{name}")
        self._name = name
        self._nested_cache = {}
        if defaults and not _r().exists(self._key):
            self._set_all(defaults)

    def _set_all(self, data: dict):
        pipe = _r().pipeline()
        for k, v in data.items():
            pipe.hset(self._key, k, _dumps(v))
        pipe.execute()

    def __getitem__(self, field: str) -> Any:
        raw = _r().hget(self._key, field)
        if raw is None:
            raise KeyError(field)
        value = _loads(raw)
        if isinstance(value, dict):
            if field not in self._nested_cache:
                self._nested_cache[field] = RedisNestedDict(self._key, field)
            return self._nested_cache[field]
        return value

    def __setitem__(self, field: str, value: Any):
        if field in self._nested_cache:
            del self._nested_cache[field]
        _r().hset(self._key, field, _dumps(value))

    def get(self, field: str, default: Any = None) -> Any:
        try:
            return self[field]
        except KeyError:
            return default

    def __contains__(self, field: str) -> bool:
        return _r().hexists(self._key, field)

    def keys(self):
        return [k.decode() if isinstance(k, bytes) else k for k in _r().hkeys(self._key)]

    def values(self):
        return [_loads(v) for v in _r().hvals(self._key)]

    def items(self):
        raw = _r().hgetall(self._key)
        return [(k.decode() if isinstance(k, bytes) else k, _loads(v)) for k, v in raw.items()]

    def update(self, data: dict):
        if data:
            pipe = _r().pipeline()
            for k, v in data.items():
                pipe.hset(self._key, k, _dumps(v))
            pipe.execute()

    def __len__(self) -> int:
        return _r().hlen(self._key)

    def __bool__(self) -> bool:
        return len(self) > 0

    def to_dict(self) -> dict:
        return dict(self.items())


class RedisNestedDict:
    """Proxy for nested dict access. Pickle-safe (no stored connection)."""

    def __init__(self, parent_key: str, field: str):
        self._parent_key = parent_key
        self._field = field

    def _read(self) -> dict:
        raw = _r().hget(self._parent_key, self._field)
        return _loads(raw) if raw else {}

    def _write(self, data: dict):
        _r().hset(self._parent_key, self._field, _dumps(data))

    def __getitem__(self, key: str) -> Any:
        return self._read()[key]

    def __setitem__(self, key: str, value: Any):
        data = self._read()
        data[key] = value
        self._write(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._read().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._read()

    def keys(self):
        return self._read().keys()

    def values(self):
        return self._read().values()

    def items(self):
        return self._read().items()

    def update(self, updates: dict):
        data = self._read()
        data.update(updates)
        self._write(data)

    def __len__(self) -> int:
        return len(self._read())

    def __bool__(self) -> bool:
        return bool(self._read())

    def __repr__(self):
        return repr(self._read())

    def __str__(self):
        return str(self._read())


# ---------------------------------------------------------------------------
# RedisValue — replaces manager.Value() for float/bool/int primitives
# ---------------------------------------------------------------------------

class RedisValue:
    """Redis-backed single value. Pickle-safe."""

    def __init__(self, name: str, default: Any = 0, **_kwargs):
        self._key = make_key(f"value:{name}")
        self._type = type(default)
        if not _r().exists(self._key):
            _r().set(self._key, _dumps(default))

    def get(self) -> Any:
        raw = _r().get(self._key)
        return self._type(_loads(raw)) if raw else self._type()

    def set(self, value: Any):
        _r().set(self._key, _dumps(value))

    # Make it behave like a number for comparisons
    def __float__(self):
        return float(self.get())

    def __int__(self):
        return int(self.get())

    def __bool__(self):
        return bool(self.get())

    def __repr__(self):
        return repr(self.get())

    def __eq__(self, other):
        return self.get() == other

    def __lt__(self, other):
        return self.get() < other

    def __gt__(self, other):
        return self.get() > other
