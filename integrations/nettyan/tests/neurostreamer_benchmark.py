"""NetTyan agent benchmark — simulate real game sessions and measure behavior.

Replays real session logs from data/logs/sessions/ through ChatAgent,
measuring response quality, timing, tool usage, and two-channel output.

Usage:
    python tests/neurostreamer_benchmark.py                    # auto-pick best session
    python tests/neurostreamer_benchmark.py --session 2026-03-27_22-52-11.jsonl
    python tests/neurostreamer_benchmark.py --max-events 20    # limit events to replay
    python tests/neurostreamer_benchmark.py --list             # list available sessions
"""

import argparse
import glob
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from data_schema.structure_templates import create_ctx_swarm
from data_schema.chat_structures import CtxEventBase
from data_flow.ctx_handler import CtxHandler

SESSIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "logs", "sessions"
)
SELF_NAME = "NetTyan"


# --- Session loading ---

def list_sessions() -> List[dict]:
    """List available session files with stats."""
    sessions = []
    for path in sorted(glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl"))):
        user_msgs = 0
        users = set()
        total = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                total += 1
                try:
                    e = json.loads(line)
                    if e.get("user") and e["user"] != SELF_NAME and e.get("type") == "chat":
                        users.add(e["user"])
                        user_msgs += 1
                except json.JSONDecodeError:
                    pass
        sessions.append({
            "path": path,
            "name": os.path.basename(path),
            "total": total,
            "user_msgs": user_msgs,
            "users": len(users),
        })
    return sessions


def find_best_session() -> str:
    """Find session with most player interaction."""
    sessions = list_sessions()
    if not sessions:
        raise FileNotFoundError(f"No session files in {SESSIONS_DIR}")
    best = max(sessions, key=lambda s: s["user_msgs"] * 10 + s["users"] * 50)
    return best["path"]


def load_session_events(path: str, max_events: int = 0) -> List[dict]:
    """Load events from JSONL session file."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
                # Ensure filter_results for validation
                if "filter_results" not in event:
                    event["filter_results"] = {"acceptable": True}
                events.append(event)
            except json.JSONDecodeError:
                continue
    if max_events > 0:
        events = events[:max_events]
    return events


# --- Benchmark result ---

@dataclass
class ResponseResult:
    """Result of a single agent response."""
    trigger_user: str
    trigger_msg: str
    response_text: str
    ttft: float  # time to first meaningful output
    total_time: float
    tool_calls: List[str] = field(default_factory=list)
    used_chat_simple: bool = False
    voice_length: int = 0
    chat_length: int = 0
    has_emotion: bool = False
    emotion: str = ""
    issues: List[str] = field(default_factory=list)


# --- Agent setup ---

def create_test_environment(events: List[dict]):
    """Create ctx_swarm + ctx_handler with session events preloaded."""
    manager = mp.Manager()
    ctx_swarm = create_ctx_swarm(manager)
    ctx_swarm["env"]["actived"] = True
    ctx_swarm["game"]["server"] = "agicraft.ru"
    ctx_swarm["game"]["ingame"] = True
    ctx_swarm["game"]["mc_started"] = True

    ctx_handler = CtxHandler(ctx_swarm)
    return ctx_swarm, ctx_handler, manager


def preload_events(ctx_handler: CtxHandler, events: List[dict]):
    """Add events to ctx_chat as history."""
    for event in events:
        ctx_handler.add_message(event, duplicate_check=False)


# --- Quality evaluation ---

def evaluate_response(result: ResponseResult):
    """Check response quality."""
    text = result.response_text
    if not text or not text.strip():
        result.issues.append("EMPTY")
        return

    # Emotion check
    import re
    emotions = re.findall(r"\*{1,2}(\w+)\*{1,2}", text)
    if emotions:
        result.has_emotion = True
        result.emotion = emotions[0]
    else:
        result.issues.append("NO_EMOTION")

    # Language check
    has_russian = any("\u0400" <= c <= "\u04ff" for c in text)
    if not has_russian:
        result.issues.append("NOT_RUSSIAN")

    # Command leak check
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("@", "/")) and not stripped.startswith("*"):
            result.issues.append("COMMAND_LEAK")
            break

    # Length analysis
    clean = re.sub(r"\*{1,2}\w+\*{1,2}", "", text).strip()
    result.voice_length = len(clean)

    # Repetition check (same start as previous — crude)
    if text.startswith(("Sure", "Of course", "Certainly", "I'd")):
        result.issues.append("ENGLISH_PREAMBLE")


# --- Main benchmark ---

def run_benchmark(
    session_path: str,
    max_events: int = 0,
    max_responses: int = 10,
    trigger_filter: str = "user_chat",
):
    """Run benchmark: replay session, trigger agent on player messages."""
    print(f"\n{'='*70}")
    print(f"  NEUROSTREAMER BENCHMARK")
    print(f"  Session: {os.path.basename(session_path)}")
    print(f"{'='*70}")

    # Load events
    all_events = load_session_events(session_path, max_events)
    print(f"[BENCH] Loaded {len(all_events)} events")

    # Find trigger points (player messages, not self, not server)
    trigger_indices = []
    for i, ev in enumerate(all_events):
        if trigger_filter == "user_chat":
            if ev.get("user") and ev["user"] != SELF_NAME and ev.get("type") == "chat":
                trigger_indices.append(i)
        elif trigger_filter == "all":
            if ev.get("user") != SELF_NAME:
                trigger_indices.append(i)

    if not trigger_indices:
        print("[BENCH] No trigger events found!")
        return

    print(f"[BENCH] Found {len(trigger_indices)} trigger points, running up to {max_responses}")
    trigger_indices = trigger_indices[:max_responses]

    # Setup environment
    ctx_swarm, ctx_handler, manager = create_test_environment(all_events)

    # Init agent
    print("[BENCH] Initializing ChatAgent...")
    from agent.multi.chat_agent import ChatAgent
    t0 = time.time()
    agent = ChatAgent(ctx_swarm=ctx_swarm, ctx_handler=ctx_handler)
    print(f"[BENCH] Agent initialized in {time.time()-t0:.2f}s")

    # Preload history up to first trigger
    first_trigger = trigger_indices[0]
    if first_trigger > 0:
        preload_events(ctx_handler, all_events[:first_trigger])
        print(f"[BENCH] Preloaded {first_trigger} history events")

    time.sleep(0.3)  # Let ctx_handler threads pick up events

    # Run responses
    results: List[ResponseResult] = []
    last_loaded_idx = first_trigger

    print(f"\n{'Run':>4} | {'TTFT':>7} | {'Total':>7} | {'Voice':>6} | {'Chat':>5} | {'Emo':>8} | {'Tools':>15} | Trigger → Response")
    print(f"{'-'*4}-+-{'-'*7}-+-{'-'*7}-+-{'-'*6}-+-{'-'*5}-+-{'-'*8}-+-{'-'*15}-+-{'-'*40}")

    for run_idx, trigger_idx in enumerate(trigger_indices):
        trigger_event = all_events[trigger_idx]

        # Load events between last trigger and this one
        if trigger_idx > last_loaded_idx:
            for ev in all_events[last_loaded_idx:trigger_idx]:
                ctx_handler.add_message(ev, duplicate_check=False)
        last_loaded_idx = trigger_idx + 1

        # Add the trigger event itself
        ctx_handler.add_message(trigger_event, duplicate_check=False)

        # Create CtxEventBase for trigger
        from data_schema.event_convert import convert_to_chat_message
        trigger = convert_to_chat_message(trigger_event)

        # Measure response
        t_start = time.perf_counter()
        response_text = agent.respond(trigger)
        t_total = time.perf_counter() - t_start

        # Collect tool call info from chat_queue and tts_queue
        tool_calls = []
        used_chat = False
        chat_len = 0

        # Check if chat_simple was called (messages in chat_queue)
        try:
            cq_len = len(ctx_swarm["chat_queue"])
            if cq_len > 0:
                used_chat = True
                # Drain chat queue to count chars
                for _ in range(min(cq_len, 10)):
                    msg = ctx_swarm["chat_queue"].pop(0)
                    if isinstance(msg, str):
                        chat_len += len(msg)
        except Exception:
            pass

        # Build result
        result = ResponseResult(
            trigger_user=trigger_event.get("user", "?"),
            trigger_msg=trigger_event.get("msg", "")[:60],
            response_text=response_text,
            ttft=t_total,  # approximate — real TTFT is inside _stream_response
            total_time=t_total,
            used_chat_simple=used_chat,
            chat_length=chat_len,
        )
        evaluate_response(result)
        results.append(result)

        # Print row
        trigger_preview = f"{result.trigger_user}: {result.trigger_msg[:25]}"
        resp_preview = response_text[:30].replace("\n", " ") if response_text else "(empty)"
        issues_str = ",".join(result.issues) if result.issues else "ok"
        chat_str = str(result.chat_length) if result.used_chat_simple else "-"

        print(
            f"{run_idx+1:>4} | {result.total_time:>6.2f}s | {result.total_time:>6.2f}s | "
            f"{result.voice_length:>6} | {chat_str:>5} | {result.emotion or '-':>8} | "
            f"{issues_str:>15} | {trigger_preview} -> {resp_preview}"
        )

        # Drain queues (no real TTS/chat consumer in benchmark)
        try:
            while len(ctx_swarm["tts_queue"]) > 0:
                ctx_swarm["tts_queue"].pop(0)
            while len(ctx_swarm["chat_queue"]) > 0:
                ctx_swarm["chat_queue"].pop(0)
        except Exception:
            pass

        time.sleep(0.3)

    # Summary
    print(f"\n{'='*70}")
    print(f"  SUMMARY ({len(results)} responses)")
    print(f"{'='*70}")

    if results:
        times = [r.total_time for r in results]
        voice_lens = [r.voice_length for r in results]
        with_emotion = sum(1 for r in results if r.has_emotion)
        with_chat = sum(1 for r in results if r.used_chat_simple)
        with_issues = sum(1 for r in results if r.issues)
        empty = sum(1 for r in results if "EMPTY" in r.issues)

        print(f"  Timing:   avg {sum(times)/len(times):.2f}s, min {min(times):.2f}s, max {max(times):.2f}s")
        print(f"  Voice:    avg {sum(voice_lens)/len(voice_lens):.0f} chars")
        print(f"  Chat:     {with_chat}/{len(results)} used chat_simple")
        print(f"  Emotion:  {with_emotion}/{len(results)} had *emotion* tag")
        print(f"  Empty:    {empty}/{len(results)}")
        print(f"  Issues:   {with_issues}/{len(results)}")

        # Issue breakdown
        all_issues = [i for r in results for i in r.issues]
        if all_issues:
            from collections import Counter
            print(f"  Issue breakdown: {dict(Counter(all_issues))}")

    print(f"{'='*70}\n")

    ctx_handler.shutdown()
    return results


def _force_exit():
    """Force exit — multiprocessing Manager threads can block shutdown on Windows."""
    import os
    os._exit(0)


def main():
    parser = argparse.ArgumentParser(description="NetTyan agent benchmark")
    parser.add_argument("--session", type=str, help="Session JSONL filename")
    parser.add_argument("--max-events", type=int, default=0, help="Max events to load (0=all)")
    parser.add_argument("--max-responses", type=int, default=10, help="Max agent responses")
    parser.add_argument("--list", action="store_true", help="List available sessions")
    args = parser.parse_args()

    if args.list:
        sessions = list_sessions()
        print(f"{'Name':<40} {'Events':>7} {'Users':>6} {'Msgs':>6}")
        print("-" * 65)
        for s in sorted(sessions, key=lambda x: -x["user_msgs"]):
            print(f"{s['name']:<40} {s['total']:>7} {s['users']:>6} {s['user_msgs']:>6}")
        return

    if args.session:
        session_path = os.path.join(SESSIONS_DIR, args.session)
        if not os.path.exists(session_path):
            print(f"Session not found: {session_path}")
            return
    else:
        session_path = find_best_session()

    try:
        run_benchmark(
            session_path=session_path,
            max_events=args.max_events,
            max_responses=args.max_responses,
        )
    except KeyboardInterrupt:
        print("\n[BENCH] Interrupted by user")
    finally:
        _force_exit()


if __name__ == "__main__":
    main()
