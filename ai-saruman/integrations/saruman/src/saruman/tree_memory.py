"""Keep only tree checkpoints through a normal runtime reset, never old queues."""

import json
import time
from copy import deepcopy

from saruman.tree_tasks import ROUTE_BLOCKED


def _legacy_wait_failure(task):
    reason = str(task.get("reason", ""))
    return task.get("status") == "blocked" and (
        reason.startswith("Minecraft procedure timed out waiting for native work.")
        or (
            reason.startswith("Native collection stopped or was replaced:")
            and "'status': 'ownership_lost'" in reason
            and "'task_chain':" not in reason
            and "'reason':" not in reason
        )
    )


def capture_tree_memory(client, game_key):
    raw = client.hget(game_key, "tree_task")
    if not raw:
        return None
    try:
        task = json.loads(raw)
        search = json.loads(client.hget(game_key, "tree_search") or "{}")
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(task, dict)
        or not isinstance(task.get("config"), dict)
        or not isinstance(task.get("targets"), list)
    ):
        return None
    task = deepcopy(task)
    if task.get("status") == "active" or _legacy_wait_failure(task):
        task["previous_status"] = task["status"]
        task["status"] = "paused"
        task["pause_reason"] = (
            "Runtime restarted; resume this saved goal after checking the world"
        )
    try:
        deferred = json.loads(client.hget(game_key, "deferred_goals") or "[]")
    except (ValueError, TypeError):
        deferred = []
    valid = []
    for row in deferred if isinstance(deferred, list) else []:
        goal = row.get("task") if isinstance(row, dict) else None
        if (
            isinstance(goal, dict)
            and isinstance(goal.get("config"), dict)
            and isinstance(goal.get("targets"), list)
        ):
            checkpoint = deepcopy(row)
            checkpoint["task"].update(
                status="paused",
                pause_reason="Runtime restarted; verify the world before resuming",
            )
            valid.append(checkpoint)
    return {
        "task": task,
        "search": search if isinstance(search, dict) else {},
        "deferred_goals": valid,
    }


def resume_request(message):
    return (
        message.lower()
        .strip()
        .startswith(("продолж", "возобнов", "resume", "continue"))
    )


def prepare_resume(game, message):
    """An explicit continuation keeps the checkpoint, including after operator Stop."""
    if not resume_request(message) or not game.get("tree_task"):
        return False
    task = deepcopy(dict(game["tree_task"].items()))
    route_retry = (
        task.get("status") == "blocked" and task.get("reason") == ROUTE_BLOCKED
    )
    if task.get("status") == "cancelled" or _legacy_wait_failure(task) or route_retry:
        task["previous_status"] = task["status"]
        task["status"] = "paused"
        game["tree_task"] = task
    return True


def resume_paused(task):
    if task.get("status") == "paused":
        task["status"] = "active"
        task["reason"] = None
        task["created_at"] = time.time()
        task["resumed_at"] = time.time()
