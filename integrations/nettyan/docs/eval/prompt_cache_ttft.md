# Prompt Cache TTFT Benchmark

Date: 2026-03-29

## Setup

| Parameter | Value |
|---|---|
| LLM Server | LM Studio (llama.cpp backend) |
| Model | Qwen (via `lm_studio/` litellm provider) |
| GPU | Local |
| Concurrent Requests | 4 (also tested with 1, no difference) |
| Prompt Caching | ON (LM Studio default) |
| LLM Pipeline | `get_llm_chain()` → `ChatLiteLLM` → `.stream()` (same as ChatAgent) |
| Benchmark script | `tests/test_prompt_cache.py` |
| max_tokens | 20 (measuring TTFT, not generation) |

## Mutation modes

| Mode | Description |
|---|---|
| `none` | Identical prompt each run. Should fully cache after run 1. |
| `tiny@end` | Unique `[sid:timestamp]` injected at the end of user message. Prefix stays identical. |
| `tiny@middle` | Unique string injected in the middle of user message. Cache valid up to mutation point. |
| `tiny@start` | Unique string injected at the start of system message. Breaks cache from the first token. |

## Results: 1k tokens

| Mode | Run 1 | Run 2 | Run 3 | Cache effect |
|---|---|---|---|---|
| none | 3.05s | **0.16s** | **0.14s** | 20x speedup after cold start |
| tiny@end | 0.79s | 0.75s | 0.73s | Prefix cached, recompute tail only |
| tiny@middle | 0.84s | 0.84s | 0.84s | Cache up to midpoint |
| tiny@start | 1.19s | 1.09s | 3.33s | Full recompute (cache broken) |

## Results: 5k tokens (concurrent=4)

| Mode | Run 1 | Run 2 | Run 3 | Cache effect |
|---|---|---|---|---|
| none | 6.39s | **0.41s** | **0.41s** | 15.6x speedup |
| tiny@end | 0.84s | 0.85s | 0.86s | Prefix cached, ~13% of cold |
| tiny@middle | 3.67s | 3.67s | 3.83s | ~57% of cold (recompute 2nd half) |
| tiny@start | 4.04s | 4.25s | 4.03s | ~63% of cold (nearly full recompute) |

## Results: 5k tokens (concurrent=1)

| Mode | Run 1 | Run 2 | Run 3 | Cache effect |
|---|---|---|---|---|
| none | 6.22s | **0.39s** | **0.40s** | 15.9x speedup |
| tiny@end | 0.84s | 0.84s | 0.83s | Same as concurrent=4 |
| tiny@middle | 3.64s | 3.65s | 3.65s | Same |
| tiny@start | 3.71s | 3.76s | 3.84s | Same |

## Analysis

### Cache works
KV cache in LM Studio is fully functional. Identical prompts get **15-20x TTFT reduction** on second request.

### Concurrent requests don't break cache
`n_parallel=4` vs `n_parallel=1` shows no measurable difference. LM Studio's Unified KV Cache shares cache across slots effectively.

### Mutation position matters linearly
TTFT scales proportionally with how early in the prompt the mutation occurs:

```
Position:    [system ....... | user ...........]
@end:         cached          cached    ^mut    → 0.85s  (13% of cold)
@middle:      cached          ^mut  recompute   → 3.67s  (57% of cold)
@start:      ^mut  recompute  recompute         → 4.04s  (63% of cold)
Full cache:   cached          cached            → 0.41s  (6% of cold)
```

### Implications for ChatAgent

ChatAgent prompt structure:
```
[STABLE — cached between calls]
system: character prompt + format instructions    ~800 tokens
tools:  JSON schemas from bind_tools              ~1500 tokens

[DYNAMIC — recomputed every call]
user:   situation + tool statuses + events         ~2000-4000 tokens
assistant/user: conversation history               ~variable
```

Expected TTFT savings:
- Cold start (first call): ~6s for 5k prompt
- Warm (same conversation, new event): ~1-2s (stable prefix cached, dynamic tail recomputed)
- Identical prompt (retry/interrupt): ~0.4s (full cache hit)

### Recommendations
- Keep system prompt **static** (already done — cached at init, rebuilt only on personality change)
- Tool definitions stay stable (bind_tools at init, not per-call)
- Dynamic data (mood, players, events, tool statuses) goes in user message after stable prefix
- `n_parallel` can stay at 4 — no cache penalty
