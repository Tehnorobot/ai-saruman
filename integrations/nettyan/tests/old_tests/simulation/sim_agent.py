"""Agent simulation — run agents with fake events without starting the full system.

Usage:
    cd src
    python -m tests.simulation.sim_agent               # default: ChatAgent
    python -m tests.simulation.sim_agent --reaction     # ReactionAgent
    python -m tests.simulation.sim_agent --core         # CoreAgent

Initializes a minimal ctx_swarm (Manager-based), injects fake chat events,
triggers each agent, and prints timing + response quality.
"""

import multiprocessing as mp
import os
import sys
import time

# Ensure src is on the path
SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
SRC_CODE_DIR = os.path.join(SRC_DIR, "src")
if SRC_CODE_DIR not in sys.path:
    sys.path.insert(0, SRC_CODE_DIR)

from data_schema.structure_templates import create_ctx_swarm
from data_flow.ctx_handler import CtxHandler


# ---- Fake events ----

FAKE_EVENTS = [
    {
        "date": "2026-03-18 02:10:00",
        "env": "minecraft",
        "msg": "MurderMystery » Игрока pafa_2509 убили!",
        "processing_timestamp": int(time.time_ns()) - 60_000_000_000,
        "user": None,
        "type": "system",
        "filter_results": {"acceptable": True, "filtered_text": ""},
    },
    {
        "date": "2026-03-18 02:12:10",
        "env": "minecraft",
        "msg": "привет нетян!",
        "processing_timestamp": int(time.time_ns()) - 10_000_000_000,
        "user": "TestPlayer",
        "type": "chat",
        "server": "mc.example.net",
        "serverMode": "survival",
        "filter_results": {"acceptable": True, "filtered_text": ""},
    },
    {
        "date": "2026-03-18 02:12:15",
        "env": "minecraft",
        "msg": "ты бот или живая?",
        "processing_timestamp": int(time.time_ns()) - 5_000_000_000,
        "user": "CuriousUser",
        "type": "chat",
        "server": "mc.example.net",
        "serverMode": "survival",
        "filter_results": {"acceptable": True, "filtered_text": ""},
    },
    {
        "date": "2026-03-18 02:12:20",
        "env": "minecraft",
        "msg": "нетян, расскажи анекдот",
        "processing_timestamp": int(time.time_ns()),
        "user": "JokerFan",
        "type": "chat",
        "server": "mc.example.net",
        "serverMode": "survival",
        "filter_results": {"acceptable": True, "filtered_text": ""},
    },
]


def setup_ctx_swarm():
    """Create a minimal ctx_swarm with fake events preloaded."""
    manager = mp.Manager()
    ctx_swarm = create_ctx_swarm(manager)
    ctx_swarm["env"]["actived"] = True

    # Inject fake events
    for event in FAKE_EVENTS:
        ctx_swarm["ctx_chat"].append(event)

    return ctx_swarm, manager


def run_chat_agent(ctx_swarm, ctx_handler):
    """Run ChatAgent on the fake events."""
    from agent.multi.chat_agent import ChatAgent

    print("\n" + "=" * 60)
    print("=== CHAT AGENT SIMULATION ===")
    print("=" * 60)

    t0 = time.time()
    agent = ChatAgent(
        ctx_swarm=ctx_swarm,
        ctx_handler=ctx_handler,
    )
    print(f"[SIM ⏱️ {time.time()-t0:.2f}s] ChatAgent initialized")

    # Trigger on last event
    trigger = ctx_handler.get_ctx_chat(validate=False, raw=False)[-1]
    print(f"[SIM] Trigger event: {trigger.user}: {trigger.msg}")

    t1 = time.time()
    response = agent.respond(trigger)
    elapsed = time.time() - t1

    print(f"\n[SIM ⏱️ {elapsed:.2f}s] Response: {response}")
    print(f"[SIM] Response length: {len(response)} chars")
    _evaluate_response(response)
    return response, elapsed


def run_reaction_agent(ctx_swarm, ctx_handler):
    """Run ReactionAgent on the fake events."""
    from agent.multi.reaction_agent import ReactionAgent

    print("\n" + "=" * 60)
    print("=== REACTION AGENT SIMULATION ===")
    print("=" * 60)

    t0 = time.time()
    agent = ReactionAgent(
        ctx_swarm=ctx_swarm,
        ctx_handler=ctx_handler,
    )
    print(f"[SIM ⏱️ {time.time()-t0:.2f}s] ReactionAgent initialized")

    trigger = ctx_handler.get_ctx_chat(validate=False, raw=False)[-1]
    print(f"[SIM] Trigger event: {trigger.user}: {trigger.msg}")

    t1 = time.time()
    response = agent.react(trigger)
    elapsed = time.time() - t1

    print(f"\n[SIM ⏱️ {elapsed:.2f}s] Response: {response}")
    print(f"[SIM] Response length: {len(response)} chars")
    _evaluate_response(response)
    return response, elapsed


def _evaluate_response(response: str):
    """Basic quality checks on agent response."""
    issues = []
    if not response or not response.strip():
        issues.append("EMPTY RESPONSE")
    if len(response) > 500:
        issues.append(f"TOO LONG ({len(response)} chars)")
    if response.lower().startswith(("sure", "of course", "certainly", "i'd be happy")):
        issues.append("PREAMBLE DETECTED (English assistant-speak)")
    if "*" not in response:
        issues.append("NO *emotion* TAG")
    has_russian = any("\u0400" <= c <= "\u04ff" for c in response)
    if not has_russian:
        issues.append("NOT IN RUSSIAN")

    if issues:
        print(f"[SIM QUALITY] Issues: {', '.join(issues)}")
    else:
        print("[SIM QUALITY] OK")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent simulation")
    parser.add_argument("--reaction", action="store_true", help="Test ReactionAgent")
    parser.add_argument("--core", action="store_true", help="Test CoreAgent")
    parser.add_argument("--all", action="store_true", help="Test all agents")
    args = parser.parse_args()

    ctx_swarm, manager = setup_ctx_swarm()
    ctx_handler = CtxHandler(ctx_swarm)

    # Small delay for ctx_handler checker thread to pick up events
    time.sleep(0.2)

    results = {}

    if args.all or (not args.reaction and not args.core):
        resp, elapsed = run_chat_agent(ctx_swarm, ctx_handler)
        results["ChatAgent"] = {"response": resp, "time": elapsed}

    if args.all or args.reaction:
        resp, elapsed = run_reaction_agent(ctx_swarm, ctx_handler)
        results["ReactionAgent"] = {"response": resp, "time": elapsed}

    # Summary
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("=== COMPARISON ===")
        print("=" * 60)
        for name, data in results.items():
            resp_preview = data["response"][:100].replace("\n", " ")
            print(f"  {name}: {data['time']:.2f}s | {resp_preview}...")

    ctx_handler.shutdown()


if __name__ == "__main__":
    main()
