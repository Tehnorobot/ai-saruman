"""Prompt caching latency benchmark for LM Studio.

Uses the SAME LLM pipeline as ChatAgent (get_llm_chain → ChatLiteLLM → .stream()).
Measures TTFT and total time to detect KV cache hits.

Usage:
    python tests/test_prompt_cache.py
    python tests/test_prompt_cache.py --size 5k --runs 3
    python tests/test_prompt_cache.py --size 1k --mutate start
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm_clients.lc_clients import get_llm_chain
from agent.tools import tools_config

# --- Config ---

PROMPT_FILLER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "resources", "Characters", "Private", "NetTyan", "personalities.yml",
)
CHARS_PER_TOKEN = 3

# --- Prompt building ---

_filler_cache = None

def _get_filler() -> str:
    global _filler_cache
    if _filler_cache is None:
        with open(PROMPT_FILLER_PATH, "r", encoding="utf-8") as f:
            _filler_cache = f.read()
    return _filler_cache


def build_messages(target_tokens: int, mutation: str = "none", mutation_pos: str = "none"):
    """Build langchain messages of ~target_tokens size.

    Returns (messages, est_tokens).
    mutation: "none" | "tiny"
    mutation_pos: "start" | "middle" | "end" | "none"
    """
    filler = _get_filler()
    target_chars = target_tokens * CHARS_PER_TOKEN

    system_base = (
        "You are NetTyan, a minecraft-playing AI streamer girl.\n"
        "Respond BRIEFLY in Russian. 1 sentence max.\n"
        "End with *emotion* tag. Available: happy, sad, angry, neutral.\n"
    )
    system_padding = filler[:max(0, target_chars // 3)]

    situation = (
        "# Current Situation:\n"
        "## Game Status: Playing survival, no tasks.\n"
        "## Mood: neutral (+0.0/10)\n"
        "## Players: Chochok (nearby, aggressive), NetTyan (you)\n"
    )
    situation_padding = filler[len(system_padding):len(system_padding) + max(0, target_chars // 3)]

    events = (
        "\n--- Happening now ---\n"
        "MINECRAFT CHAT [now] Chochok: привет тян\n"
        "\nRespond to the latest events. Be BRIEF and in-character.\n"
    )
    events_padding = filler[
        len(system_padding) + len(situation_padding):
        len(system_padding) + len(situation_padding) + max(0, target_chars // 3)
    ]

    mutation_marker = ""
    if mutation == "tiny":
        mutation_marker = f"\n[sid:{time.time_ns()}]\n"

    system_content = system_base + system_padding
    user_content = situation + situation_padding + events + events_padding

    if mutation_pos == "start":
        system_content = mutation_marker + system_content
    elif mutation_pos == "middle":
        mid = len(user_content) // 2
        user_content = user_content[:mid] + mutation_marker + user_content[mid:]
    elif mutation_pos == "end":
        user_content = user_content + mutation_marker

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]
    total_chars = len(system_content) + len(user_content)
    return messages, total_chars // CHARS_PER_TOKEN


# --- Streaming call with timing ---

def stream_llm(llm, messages, max_tokens: int = 20) -> dict:
    """Stream LLM response, measure TTFT.

    max_tokens kept tiny — we only care about prompt processing time (TTFT),
    not generation speed.
    """
    t_start = time.perf_counter()
    t_first_token = None
    full_text = ""

    try:
        for chunk in llm.stream(messages, max_tokens=max_tokens):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            if content:
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                full_text += content
    except Exception as e:
        t_end = time.perf_counter()
        return {
            "ttft": t_end - t_start, "total": t_end - t_start,
            "gen_time": 0, "text": f"ERROR: {e}",
        }

    t_end = time.perf_counter()
    ttft = (t_first_token - t_start) if t_first_token else (t_end - t_start)
    return {
        "ttft": ttft,
        "total": t_end - t_start,
        "gen_time": (t_end - t_start) - ttft,
        "text": full_text.strip(),
    }


# --- Benchmark ---

SIZE_PRESETS = {"1k": 1000, "5k": 5000, "20k": 20000}

MUTATION_MODES = [
    ("none", "none"),
    ("tiny", "end"),
    ("tiny", "middle"),
    ("tiny", "start"),
]


def run_benchmark(llm, target_tokens: int, runs: int = 3, mutations: list = None):
    if mutations is None:
        mutations = MUTATION_MODES

    print(f"\n{'='*70}")
    print(f"  PROMPT CACHE BENCHMARK  ~{target_tokens} tokens, {runs} runs each")
    print(f"  LLM: {llm.model}")
    print(f"{'='*70}")

    for mut_type, mut_pos in mutations:
        label = f"mutation={mut_type}" if mut_type == "none" else f"mutation={mut_type}@{mut_pos}"
        print(f"\n--- {label} ---")
        print(f"{'Run':>4} | {'TTFT':>8} | {'Gen':>8} | {'Total':>8} | Response")
        print(f"{'-'*4}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*30}")

        for i in range(runs):
            messages, est_tokens = build_messages(target_tokens, mut_type, mut_pos)
            if i == 0:
                total_chars = sum(len(m.content) for m in messages)
                print(f"     (est. {est_tokens} tokens, {total_chars} chars)")

            result = stream_llm(llm, messages)

            preview = result["text"][:45].replace("\n", " ")
            print(
                f"{i+1:>4} | {result['ttft']:>7.2f}s | {result['gen_time']:>7.2f}s | "
                f"{result['total']:>7.2f}s | {preview}"
            )
            time.sleep(0.3)

    print(f"\n{'='*70}")
    print("  EXPECTED BEHAVIOR:")
    print("  mutation=none     run 2+ TTFT << run 1  (cache hit)")
    print("  tiny@end          TTFT ~ none            (prefix cached)")
    print("  tiny@middle       TTFT > none            (partial break)")
    print("  tiny@start        TTFT ~ run 1           (full recompute)")
    print(f"{'='*70}\n")


def _bind_chat_agent_tools(llm):
    """Bind the same tools as ChatAgent does — to test cache with tool definitions."""
    from agent.tools.tools import ALL_TOOLS, DEFAULT_DISABLED_TOOLS
    from agent.tools.status_tools import get_langchain_tool

    DIRECT_TEXT_TOOLS = {"speak", "speak_list"}
    available = []
    for func in ALL_TOOLS:
        if func in DEFAULT_DISABLED_TOOLS:
            continue
        if func.__name__ in DIRECT_TEXT_TOOLS:
            continue
        try:
            lc_tool = get_langchain_tool(func)
            available.append(lc_tool)
        except Exception:
            pass

    print(f"[INIT] Binding {len(available)} tools: {[t.name for t in available]}")
    return llm.bind_tools(available)


def main():
    parser = argparse.ArgumentParser(description="Prompt cache latency benchmark")
    parser.add_argument("--size", choices=list(SIZE_PRESETS.keys()) + ["all"], default="1k")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--mutate", choices=["none", "start", "middle", "end", "all"], default="all")
    parser.add_argument("--tools", action="store_true", help="Bind ChatAgent tools (test cache with tool definitions)")
    args = parser.parse_args()

    print("[INIT] Loading LLM via get_llm_chain()...")
    llm = get_llm_chain()
    print(f"[INIT] Model: {llm.model}, API: {getattr(llm, 'api_base', 'default')}")

    if args.tools:
        llm = _bind_chat_agent_tools(llm)
        print(f"[INIT] Tools bound — testing with tool definitions in prompt")

    if args.mutate == "all":
        mutations = MUTATION_MODES
    else:
        mutations = [(m, p) for m, p in MUTATION_MODES if p == args.mutate or m == args.mutate]
        if not mutations:
            mutations = [("none", "none")]

    sizes = list(SIZE_PRESETS.keys()) if args.size == "all" else [args.size]
    for size in sizes:
        run_benchmark(llm, SIZE_PRESETS[size], args.runs, mutations)


if __name__ == "__main__":
    main()
