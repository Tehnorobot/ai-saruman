"""Goal-level native mod tools. Source and deduplication belong to runtime, not LLM."""

import time

from agent.tools import tools_config
from saruman.autonomy_settings import load_weights, save_weights
from saruman.mod_goals import GoalStore, player_context, using_mods


def grow_trees(count: int = 1) -> dict:
    """Grow N trees using native TreeGrow, without further model calls.

    Current mod chooses oak or birch itself and grows one per native run.
    Strict species, plant-only and custom bone-meal budgets are unsupported.
    Check get_mod_status for verified progress; acceptance is not growth.

    Args:
        count: Number of trees, from 1 to 100.
    """
    game = tools_config.ctx_swarm.get("game", {})
    context = player_context(game)
    if (
        not using_mods(game)
        or context.get("until", 0) < time.time()
        or not context.get("id")
    ):
        return {
            "status": "rejected",
            "reason": "Requires mod runtime and current player/operator request",
        }
    goal = GoalStore().submit(
        "trees",
        source=context.get("source", "player"),
        count=count,
        request_id=f"{context['id']}:trees:{count}",
    )
    return {"status": "accepted", "goal": goal, "executor": "TreeGrow"}


def get_mod_status() -> dict:
    """Read mod control ownership and durable goals/progress. Does not start actions."""
    game = tools_config.ctx_swarm.get("game", {})
    return {
        "runtime": dict(game.get("mod_runtime", {}).items()),
        "goals": GoalStore().list(limit=30),
        "background_trees_enabled": load_weights().get("trees", 0) > 0,
    }


def cancel_mod_goal(goal_id: str = "") -> dict:
    """Cancel a native goal, retaining verified progress. Empty ID cancels all goals.

    Args:
        goal_id: Goal ID from grow_trees/get_mod_status, or empty to stop all goals.
    """
    context = player_context(tools_config.ctx_swarm.get("game", {}))
    if context.get("until", 0) < time.time():
        return {"status": "rejected", "reason": "No player/operator request"}
    store = GoalStore()
    matches = [
        g
        for g in store.list(active=True, limit=-1)
        if not goal_id or goal_id in {g["id"], g.get("run_id")}
    ]
    if not matches:
        return {
            "status": "rejected",
            "reason": "No active goal matches this goal ID or run ID; nothing cancelled",
        }
    for goal in matches:
        store.cancel(goal["id"])
    return {"status": "cancel_requested", "goal_ids": [g["id"] for g in matches]}


def stop_tree_growing() -> dict:
    """Stop all pending/running tree goals AND disable autonomous tree planting.

    Use when a player says to stop planting/growing trees, not just to pause one
    run. Other skills/commands are unaffected. The operator can enable background
    trees again in Gradio. A later explicit grow_trees request is still possible.
    """
    context = player_context(tools_config.ctx_swarm.get("game", {}))
    if not context.get("id") or context.get("until", 0) < time.time():
        return {"status": "rejected", "reason": "No player/operator request"}
    weights = load_weights()
    save_weights({**weights, "trees": 0})
    store = GoalStore()
    targets = [g for g in store.list(active=True, limit=-1) if g["kind"] == "trees"]
    for goal in targets:
        store.cancel(goal["id"])
    return {
        "status": "cancel_requested",
        "background_trees_enabled": False,
        "goal_ids": [g["id"] for g in targets],
    }
