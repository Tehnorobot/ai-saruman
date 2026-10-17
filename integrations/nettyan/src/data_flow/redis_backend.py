"""Redis connection pool and client factory.

Usage:
    from data_flow.redis_backend import get_redis
    r = get_redis()  # returns cached redis.Redis instance from pool
"""

import os
import time

import redis

_pool: redis.ConnectionPool = None
_client: redis.Redis = None
_PREFIX = "nettyan:"


def get_redis(url: str = None) -> redis.Redis:
    """Get a cached Redis client from the shared connection pool.

    Thread-safe: redis-py clients are thread-safe.
    Pickle note: don't store the return value — call get_redis() each time.
    The pool is process-local (each child process gets its own after fork/spawn).
    """
    global _pool, _client
    if _client is not None:
        return _client

    t = time.time()
    if url is None:
        url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    _pool = redis.ConnectionPool.from_url(url, decode_responses=False)
    _client = redis.Redis(connection_pool=_pool)
    # Test connection
    try:
        _client.ping()
        print(f"[REDIS ⏱️ {time.time()-t:.2f}s] Connected to {url}")
    except redis.ConnectionError as e:
        print(f"[REDIS ⏱️ {time.time()-t:.2f}s] Connection FAILED: {e}")
        raise
    return _client


def get_prefix() -> str:
    return _PREFIX


def make_key(name: str) -> str:
    return f"{_PREFIX}{name}"


def close_pool():
    global _pool, _client
    if _client is not None:
        _client.close()
        _client = None
    if _pool is not None:
        _pool.disconnect()
        _pool = None
