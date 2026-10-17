"""RedisCtxSwarm — drop-in replacement for multiprocessing Manager-based ctx_swarm.

Pickle-safe: when passed to multiprocessing.Process on Windows (spawn),
each child process recreates its own Redis connection from the stored URL.

Usage:
    ctx_swarm = RedisCtxSwarm()
    # Works the same as Manager-based ctx_swarm:
    ctx_swarm["env"]["actived"] = False
    ctx_swarm["tts_queue"].append({"text": "hi", "emotion": "happy"})
"""

import os
import time

from data_flow.redis_backend import make_key, close_pool
from data_flow.redis_collections import (
    RedisCtxChat,
    RedisDict,
    RedisList,
    RedisValue,
)


# Default values from CTX_SWARM_EMPTY in structure_templates.py
_DICT_DEFAULTS = {
    "env": {"actived": True, "filter_enabled": True, "youtube_social_enabled": True},
    "vtube": {
        "NeedX": 0, "NeedY": 0, "eyeX": 0, "eyeY": 0,
        "state": "idle", "game_yawspeed": 0, "game_pitchspeed": 0,
    },
    "voice": {
        "text_chunk": "", "is_speaking": False, "interrupt": False,
        "speak_entry": {"text": "", "emotion": ""},
    },
    "states": {
        "thoughts": {
            "summary": "short overall summary of situation understanding",
            "focus_topic": "1 topic that we should focus on",
            "restricted_topics": "",
            "plan": "",
            "criticism": "",
        },
        "summary": "",
        "plan": "",
        "mood": "0",
        "mood_reason": "",
        "personality": "streamer_abstract",
        "game_state": "System started, no info got in game, situation: neutral. Needs update!",
        "chat_state": "System started so chat is empty, situation in chat: neutral. Needs update!",
        "emotional_state": "Needs update!",
    },
    "game": {
        "mc_last_chat_interact": "2023-04-01 00:00:00",
        "mc_started": False, "ingame": False, "hasActiveTask": False,
        "near_threats": [], "held_item": "", "ground_block": "",
        "task_chain": "", "nearby_players": [],
    },
    "votes": {},
}

_LIST_KEYS = [
    "chat_queue",
    "tts_queue",
    "fx_queue",
    "internal_command_queue",
    "filter_input_queue",
    "filter_output_queue",
    "audio_feed_queue",
]

_VALUE_DEFAULTS = {
    "mood": 0.0,
}


class _RedisLock:
    """No-op lock for API compatibility. Redis operations are atomic."""
    def __enter__(self, *a, **kw):
        return self
    def __exit__(self, *a, **kw):
        pass
    def acquire(self, *a, **kw):
        return True
    def release(self, *a, **kw):
        pass


class RedisCtxSwarm:
    """Dict-like facade over Redis. Pickle-safe for multiprocessing on Windows.

    On pickle: stores only the redis_url string.
    On unpickle: recreates Redis connection and all collection wrappers.
    """

    def __init__(self, redis_url: str = None, flush_on_start: bool = False):
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self._collections = {}

        # Initialize the shared connection pool with our URL
        from data_flow.redis_backend import get_redis
        get_redis(self._redis_url)

        if flush_on_start:
            self._flush()

        self._init_collections(populate_defaults=flush_on_start)

    def _flush(self):
        """Clear all nettyan: keys."""
        from data_flow.redis_backend import get_redis
        r = get_redis(self._redis_url)
        prefix = make_key("*")
        keys = r.keys(prefix)
        if keys:
            r.delete(*keys)

    def _init_collections(self, populate_defaults: bool = False):
        """Create all Redis-backed collection wrappers.

        Each wrapper creates its own Redis connection internally via get_redis().
        No shared connection object = no pickle issues.
        """
        t = time.time()

        # ctx_chat — sorted set
        self._collections["ctx_chat"] = RedisCtxChat()

        # FIFO queues — Redis lists
        for name in _LIST_KEYS:
            self._collections[name] = RedisList(name)

        # Dicts — Redis hashes
        for name, defaults in _DICT_DEFAULTS.items():
            self._collections[name] = RedisDict(
                name, defaults=defaults if populate_defaults else None
            )

        # Values — Redis strings
        for name, default in _VALUE_DEFAULTS.items():
            self._collections[name] = RedisValue(name, default=default)

        # Locks — no-ops
        all_keys = list(self._collections.keys())
        for key in all_keys:
            self._collections[f"{key}_lock"] = _RedisLock()
        self._collections["lock"] = _RedisLock()
        self._collections["ctx_host_lock"] = _RedisLock()

        print(f"[REDIS CTX ⏱️ {time.time()-t:.2f}s] Collections initialized ({len(self._collections)} keys)")

    # --- Pickle support for multiprocessing ---

    def __getstate__(self):
        """Only serialize the URL — connections are recreated in child processes."""
        return {"_redis_url": self._redis_url}

    def __setstate__(self, state):
        """Recreate collections from URL in child process."""
        self._redis_url = state["_redis_url"]
        self._collections = {}
        self._init_collections(populate_defaults=False)

    # --- Dict-like interface ---

    def __getitem__(self, key: str):
        if key in self._collections:
            return self._collections[key]
        raise KeyError(f"ctx_swarm key '{key}' not found")

    def __setitem__(self, key: str, value):
        existing = self._collections.get(key)
        if isinstance(existing, RedisValue):
            existing.set(value)
        elif isinstance(existing, RedisDict) and isinstance(value, dict):
            existing._set_all(value)
        elif isinstance(existing, RedisCtxChat) and isinstance(value, list):
            existing.clear()
            for item in value:
                existing.append(item)
        else:
            self._collections[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._collections

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return self._collections.keys()

    def close(self):
        close_pool()

    def __repr__(self):
        return f"<RedisCtxSwarm url={self._redis_url}>"
