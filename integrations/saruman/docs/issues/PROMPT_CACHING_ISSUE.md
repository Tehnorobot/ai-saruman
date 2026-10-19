# Prompt Caching Issue — Investigation Log

## Problem

ChatAgent TTFT stays at ~3s even with append-only prefix cache.
Benchmark proves cache works (0.2s cache hit). Production doesn't.

## Proven Facts

### Cache works in isolation (benchmark)
```
Without tools:  cold 3.7s → cache hit 0.22s
With tools:     cold 5.9s → cache hit 0.23s
With tools+20k: cold 10.6s → cache hit 0.32s
```

### Cache requires FULL stream drain
```python
# BREAKS cache — 2.5s every time:
for chunk in llm.stream(msgs):
    if chunk.content:
        break  # early exit

# WORKS — 0.2s cache hit:
for chunk in llm.stream(msgs):
    pass  # read all chunks
```
LM Studio only saves KV cache when stream is fully consumed.
`gen.close()` does NOT help — must read all chunks.

### Cache is token-prefix based
Any change in the first N tokens = cache miss on everything after.
Changes in the LAST message only = cache hit on prefix.

## Root Causes Found

### 1. _ensure_alternation merged history with tail
`_ensure_alternation` merged consecutive HumanMessages. When history's last
HumanMessage got merged with tail, the merged content changed between calls
(tail has dynamic situation context). Cache miss.

**Status**: Fixed — removed _ensure_alternation from assembly.

### 2. Stream not fully drained
When stream was interrupted (SmartEventWaiter) or tool loop broke early,
remaining chunks weren't consumed. LM Studio didn't save KV cache.

**Status**: Fixed — `finally: for _ in stream_gen: pass`

### 3. Relative timestamps in cached history
`[5.2m ago]` changes to `[6.3m ago]` next call. Different tokens = cache miss.

**Status**: Fixed — switched to absolute `[HH:MM:SS]` in cached history.

### 4. "WAKED YOU UP" marker in cached history
Added on every event, changed between calls.

**Status**: Fixed — removed from cached events, only in tail.

### 5. Tool statuses in tail (~800 tokens)
Dynamic tool status info in tail adds processing overhead.

**Status**: Temporarily disabled. Needs to be re-enabled with stable format.

## Current Architecture (after rewrite)

```python
# _cached_prefix: append-only, objects never replaced
self._cached_prefix = [
    SystemMessage(system_prompt),        # stable
    HumanMessage(event1),                # appended once
    AIMessage(response1, tool_calls=[]), # appended once
    ToolMessage(result1),                # appended once
    HumanMessage(event2),                # appended once
    ...
]

# Prompt = prefix + tail
lc_messages = self._cached_prefix + [HumanMessage(tail)]
```

## Remaining Issue

TTFT oscillates: sometimes 1.2s (cache hit), usually 3.2s (miss).
After clean rewrite, prefix hash shows MISS even when prefix should be identical.

### Hypothesis: litellm serialization instability

Litellm interceptor showed hashes changing between calls even when our
message content was identical. Possible causes:

1. **AIMessage.tool_calls serialization** — langchain AIMessage stores tool_calls
   as dicts. Litellm may serialize them with unstable key ordering.

2. **ToolMessage.tool_call_id** — we use processing_timestamp (nanoseconds) as
   tool_call_id. Different each response → different tokens in prefix.

3. **Litellm adds metadata** to API request that changes between calls
   (request_id, timestamps, etc.)

### Key diagnostic that was removed (RESTORE IT)

The litellm interceptor that hashed the actual API request payload:
```python
import litellm
_orig = litellm.completion
def _patched(*args, **kwargs):
    msgs = kwargs.get('messages', [])
    tools = kwargs.get('tools', [])
    h = hashlib.md5(json.dumps(msgs + tools, sort_keys=True).encode()).hexdigest()[:12]
    # Compare with previous hash
    ...
    return _orig(*args, **kwargs)
litellm.completion = _patched
```
This showed the ACTUAL bytes sent to LM Studio, not our internal representation.

## ROOT CAUSE FOUND: LM Studio KV cache eviction timeout

**Proven by delay test:**
```
0.3s pause → 0.198s  ✓ cache hit
5.0s pause → 2.640s  ✗ cache EVICTED
10s pause  → 2.554s  ✗
30s pause  → 2.785s  ✗
0.3s pause → 0.163s  ✓ cache hit (re-warmed)
```

LM Studio evicts KV cache after ~2-5 seconds of inactivity.
ChatAgent waits 15-30 seconds between calls (trigger wait loop).
Cache is ALWAYS evicted by the time the next request arrives.

**Our append-only code is CORRECT.** Cache test (0.3s pause) = 0.185s hit.

## Options

1. **LM Studio setting** — check if there's a KV cache TTL config.
   Look in Model Settings → Advanced → "Prompt Cache" or "Context Caching".

2. **Keepalive ping** — send a minimal dummy request every 2-3s during
   wait loop to keep the cache warm. Downside: wastes GPU time.

3. **Accept 3s baseline** — this is cold prompt processing time for ~2000 tokens.
   Focus optimization elsewhere (reduce prompt size, streaming TTS, etc.)

4. **Switch to vLLM or llama.cpp server** — may have configurable cache TTL.
   LM Studio wraps llama.cpp but may not expose all settings.

## UPDATE: Precise cache eviction measurements

Without tools (plain LLM):
```
0.5s wait → 0.234s  ✓   cache holds for 5+ seconds
5.0s wait → 0.319s  ✓
```

With tools (bind_tools):
```
0.5s wait → 0.198s  ✓
3.0s wait → 0.349s  ✓
5.0s wait → 2.329s  ✗   cache evicted ~3-5s for tool requests
```

Tool-augmented requests have shorter cache TTL (~3-5s) vs plain (~30s+).
ChatAgent waits 15-30s between calls → always cache miss.

Our code is correct. This is LM Studio's cache management behavior.

## SOLUTION: KV cache keepalive (WORKING)

**Keepalive ping every 3s reduces TTFT from 3s to ~1.1s.**

Proof:
```
Without keepalive (15s gap): TTFT = 2.497s  (cache evicted)
With keepalive (ping/3s):    TTFT = 0.196s  (isolated test, same prompt)
Production with keepalive:   TTFT = 1.14s   (real ChatAgent, growing prefix)
```

Why 1.1s and not 0.2s: keepalive pings with `HumanMessage(".")` as tail.
Real request has AIMessage (LLM response) at that position instead.
Cache hits on system prompt + early history (~1100 tok), misses on
the last few messages (~500 tok new). 500 tok / 1000 tok/s = ~0.5s + overhead.

Implementation in ChatAgent:
- `_keepalive_loop()`: daemon thread, sends `prefix + [HumanMessage(".")]`
  with `max_tokens=1` every 3s. Fully drains stream (required for cache save).
- `_stop_keepalive()`: paused before real LLM request (avoid slot conflict)
- `_start_keepalive()`: resumed after LLM response completes
- GPU overhead: ~0.18s every 3s = ~6% utilization

**Status: DEPLOYED AND WORKING. TTFT improved 3x (3s → 1.1s).**

Further optimization possible: make ping tail closer to real tail
(e.g. include situation context skeleton) to reduce remaining cache miss.

## Benchmark Commands

```bash
# Cache baseline (no tools)
python tests/test_prompt_cache.py --size 1k --runs 5 --mutate none

# Cache with tools
python tests/test_prompt_cache.py --size 1k --runs 5 --mutate none --tools

# Full agent benchmark
python tests/neurostreamer_benchmark.py --max-responses 5
```
