# Redis Migration

## What changed

Replaced `multiprocessing.Manager()` proxy objects with Redis-backed collections.
Both backends work — use `--redis` flag to enable Redis mode.

## How to run

```bash
# Start Redis
docker compose up -d redis

# Run with Redis backend
python main.py --redis -c        # ChatAgent + Redis
python main.py --redis -m        # MultiAgent + Redis
python main.py --redis            # CoreAgent + Redis

# Run with legacy Manager backend (no Redis needed)
python main.py -m                 # Same as before
```

## Redis data model

| Python object | Redis structure | Key pattern |
|---------------|----------------|-------------|
| `ctx_chat` (event history) | Sorted Set (score=timestamp) | `nettyan:ctx_chat` |
| `chat_queue`, `tts_queue` | List (LPUSH/BRPOP) | `nettyan:list:{name}` |
| `fx_queue`, filter queues | List (LPUSH/BRPOP) | `nettyan:list:{name}` |
| `env`, `states`, `voice` | Hash | `nettyan:dict:{name}` |
| `mood` | String | `nettyan:value:mood` |
| Locks | No-op (Redis is atomic) | — |

## Architecture

```
RedisCtxSwarm (facade, dict-like)
  ├── RedisCtxChat      — Sorted Set, pub/sub on append
  ├── RedisList          — List with append/pop/get/put
  ├── RedisDict          — Hash with nested dict support
  │   └── RedisNestedDict — proxy for voice["is_speaking"] = True
  └── RedisValue         — single float/int/bool
```

CtxHandler auto-detects backend:
- Redis → subscribes to `nettyan:events:ctx_chat` channel (instant notifications)
- Manager → polls ctx_chat length every 50ms (legacy)

## Key files

| File | Purpose |
|------|---------|
| `data_flow/redis_backend.py` | Connection pool, key naming |
| `data_flow/redis_collections.py` | RedisList, RedisCtxChat, RedisDict, RedisValue |
| `data_flow/redis_ctx_swarm.py` | RedisCtxSwarm facade (drop-in for Manager ctx_swarm) |
| `data_flow/ctx_handler.py` | Updated with Redis pub/sub listener |

## API compatibility

All existing `ctx_swarm["key"]` access patterns work unchanged:
- `ctx_swarm["tts_queue"].append(dict)`
- `ctx_swarm["tts_queue"].pop(0)`
- `ctx_swarm["tts_queue"][0]`
- `ctx_swarm["tts_queue"][:] = []`
- `len(ctx_swarm["chat_queue"])`
- `ctx_swarm["fx_queue"].put("thinking")`
- `ctx_swarm["filter_input_queue"].get(timeout=3)`
- `ctx_swarm["voice"]["is_speaking"] = True`
- `ctx_swarm["voice"]["speak_entry"]["emotion"]`
- `ctx_swarm["env"]["actived"]`
- `with ctx_swarm["ctx_chat_lock"]:` (no-op with Redis)

## Environment variables

```
REDIS_URL=redis://localhost:6379/0   # default, override for remote Redis
```
