"""Persisted counted tree work, advanced in bounded steps through existing tools."""

import math
import time
import uuid
from copy import deepcopy

from saruman import trees
from saruman.minecraft import _java_to_python

ROUTE_BLOCKED = (
    "No confirmed observed route towards the saved site; target and progress preserved"
)


def task_config(
    count=1,
    mode="plant",
    sapling="oak_sapling",
    max_bone_meal=None,
    candidate_limit=128,
    search_steps=4,
):
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("count must be an integer from 1 to 32")
    if mode not in ("plant", "grow"):
        raise ValueError("mode must be plant or grow")
    sapling = trees._sapling_name(sapling)
    if max_bone_meal is None:
        max_bone_meal = count * trees.MAX_GROWTH_ATTEMPTS if mode == "grow" else 0
    if type(max_bone_meal) is not int or not 0 <= max_bone_meal <= 256:
        raise ValueError("max_bone_meal must be from 0 to 256, TOTAL for this task")
    if mode == "grow" and not max_bone_meal:
        raise ValueError(
            "grow requires an allowed bone meal budget; use plant for natural growth"
        )
    if type(candidate_limit) is not int or not 24 <= candidate_limit <= 256:
        raise ValueError("candidate_limit must be from 24 to 256")
    if type(search_steps) is not int or not 0 <= search_steps <= 8:
        raise ValueError("search_steps must be from 0 to 8")
    return {
        "count": count,
        "mode": mode,
        "sapling": sapling,
        "max_bone_meal": max_bone_meal if mode == "grow" else 0,
        "candidate_limit": candidate_limit,
        "search_steps": search_steps,
    }


def new_task(config):
    return {
        "id": uuid.uuid4().hex,
        "config": config,
        "status": "active",
        "created_at": time.time(),
        "steps": 0,
        "targets": [],
        "bone_meal_charged": 0,
    }


def cancel_task(game):
    task = deepcopy(dict(game.get("tree_task", {}).items()))
    if task.get("status") == "active":
        task["status"] = "cancelled"
        task["reason"] = "Stopped by operator/agent"
        game["tree_task"] = task


def progress(task):
    targets = task["targets"]
    planted = sum(t["stage"] in ("planted", "fertilizing", "grown") for t in targets)
    grown = sum(t["stage"] == "grown" for t in targets)
    skipped = sum(bool(t.get("exhausted")) and t["stage"] != "grown" for t in targets)
    completed = (
        sum(t["stage"] == "planted" and t.get("owned") for t in targets)
        if task["config"]["mode"] == "plant"
        else grown
    )
    return {
        "requested": task["config"]["count"],
        "completed": completed,
        "remaining": task["config"]["count"] - completed,
        "planted": planted,
        "grown": grown,
        "skipped": skipped,
        "actionable_remaining": task["config"]["count"] - completed - skipped,
    }


def result(task, status, **fields):
    return {
        "status": status,
        "task_id": task["id"],
        "progress": progress(task),
        "goal": (
            f"Grow {task['config']['count']} trees"
            if task["config"]["mode"] == "grow"
            else f"Plant {task['config']['count']} saplings"
        )
        + f" ({task['config']['sapling']})",
        "bone_meal_charged": task["bone_meal_charged"],
        "bone_meal_limit": task["config"]["max_bone_meal"],
        "resume": {"tool": "manage_trees", "arguments": task["config"]},
        **fields,
    }


def _block(position):
    gateway = trees._gateway()
    try:
        block = _java_to_python(gateway.entry_point.getBlockAt(*position)).get("id")
        if not block:
            raise RuntimeError(
                "Minecraft did not return a block ID for the tracked position"
            )
        return block
    finally:
        gateway.close()


def advance(task, save, search_state, failed_goals=()):
    """At most one target, five lifetime attempts; checkpoint before every action."""
    config = task["config"]
    sapling_id = "minecraft:" + config["sapling"]
    log_id = "minecraft:" + config["sapling"].replace("_sapling", "_log")

    def finish(status, reason=None, **fields):
        if status in ("completed", "partial", "blocked", "cancelled"):
            task["status"] = status
            task["reason"] = reason
        save(task)
        return result(task, status, reason=reason, **fields)

    def next_step():
        if not progress(task)["remaining"]:
            return finish("completed")
        if not progress(task)["actionable_remaining"]:
            return finish(
                "partial",
                "Five-attempt limit reached for remaining saplings; report verified results and continue another goal",
            )
        return finish(
            "in_progress", next_action={"tool": "manage_trees", "arguments": config}
        )

    if task["status"] != "active":
        return result(task, task["status"], reason=task.get("reason"))
    if (
        time.time() - task["created_at"] > 1200
        or task["steps"] >= config["count"] * 12 + config["search_steps"] * 2 + 16
    ):
        return finish(
            "blocked",
            "Task time/step budget exhausted; confirmed progress is preserved",
        )
    if trees._game_busy():
        return result(task, "waiting_for_game_task")
    task["steps"] += 1
    save(task)

    # Recover an interrupted interaction by reading the reserved exact target.
    for target in task["targets"]:
        stage = target["stage"]
        if (
            stage == "planted"
            and config["mode"] == "grow"
            and _block(target["position"]) == log_id
        ):
            target["stage"] = "grown"
            save(task)
            continue
        if stage not in ("planting", "fertilizing"):
            continue
        block = _block(target["position"])
        if stage == "planting" and block == sapling_id:
            target["stage"] = "planted"
            target["owned"] = True
        elif config["mode"] == "grow" and block == log_id:
            target["stage"] = "grown"
        else:
            return finish(
                "blocked",
                "Previous interaction result is uncertain; inspect this position before retrying",
                current_target=target["position"],
            )
        save(task)
    if not progress(task)["remaining"]:
        return finish("completed")

    if not progress(task)["actionable_remaining"]:
        return next_step()
    inventory = trees.read_player_inventory()
    counts = {}
    for item in inventory["items"]:
        counts[item["item"]] = counts.get(item["item"], 0) + item["count"]
    active = [t for t in task["targets"] if t["stage"] != "abandoned"]

    def unfinished(target):
        return not target.get("exhausted") and (
            target["stage"] == "planned"
            or (config["mode"] == "grow" and target["stage"] == "planted")
        )

    current = next((t for t in active if unfinished(t)), None)
    scan = None
    if current is None:
        scan = trees.find_tree_spots(
            config["sapling"],
            candidate_limit=config["candidate_limit"],
            search_steps=config["search_steps"],
            search_state=search_state,
            failed_goals=failed_goals,
            allow_exploration=True,
            avoid_positions=[t["position"] for t in task["targets"]],
            min_spacing=4,
        )
        positions = []
        if config["mode"] == "grow":
            positions.extend((p, "planted", False) for p in scan["existing_saplings"])
        positions.extend((s["position"], "planned", False) for s in scan["spots"])
        for position, stage, owned in positions:
            if len(active) >= config["count"]:
                break
            if any(
                math.hypot(
                    position[0] - t["position"][0], position[2] - t["position"][2]
                )
                < 4
                for t in task["targets"]
            ):
                continue
            target = {"position": list(position), "stage": stage, "owned": owned}
            task["targets"].append(target)
            active.append(target)
        current = next((t for t in active if unfinished(t)), None)
        save(task)

    # Absolute inventory target for ALL remaining new plantings, not +1 per call.
    need_saplings = config["count"] - sum(
        t["stage"] in ("planted", "fertilizing", "grown") for t in active
    )
    if counts.get(sapling_id, 0) < need_saplings:
        missing = {"item": config["sapling"], "count": need_saplings}
        return finish(
            "needs_materials",
            missing=missing,
            next_action={"tool": "obtain_item", "arguments": missing},
        )
    budget = config["max_bone_meal"] - task["bone_meal_charged"]
    if config["mode"] == "grow":
        if budget <= 0:
            return finish("blocked", "Total bone meal budget exhausted")
        if not counts.get("minecraft:bone_meal"):
            missing = {
                "item": "bone_meal",
                "count": min(
                    budget,
                    max(
                        1,
                        progress(task)["actionable_remaining"]
                        * trees.MAX_GROWTH_ATTEMPTS
                        - sum(
                            t.get("attempts", 0)
                            for t in active
                            if unfinished(t)
                        ),
                    ),
                ),
            }
            return finish(
                "needs_materials",
                missing=missing,
                next_action={"tool": "obtain_item", "arguments": missing},
            )
    if current is None:
        exploration = (scan or {}).get("exploration") or {}
        action = exploration.get("next_action")
        if action:
            return finish("needs_move", scan=scan, next_action=action)
        return finish(
            "blocked",
            exploration.get(
                "status", "No sufficiently separated valid sites in this scan"
            ),
            scan=scan,
        )

    player = tuple(float(p) for p in inventory["position"].split(","))

    def approach_to(target):
        return trees.tree_approach(
            target["position"],
            failed_goals,
            allow_partial=True,
            previous_waypoints=target.get("return_waypoints", []),
        )

    # A reserved site is not an order to ignore other reachable trees in this job.
    pending_targets = sorted(
        (t for t in active if unfinished(t)),
        key=lambda t: math.dist(player, t["position"]),
    )
    approach = None
    for target in pending_targets:
        if math.dist(player, target["position"]) <= 3.5:
            current = target
            break
        approach = approach_to(target)
        if approach:
            current = target
            break
    else:
        # Only replace a plan that has never been planted. Keep all physical trees,
        # checkpoints and confirmed counts; retain the old plan unless a new site works.
        replaceable = next(
            (t for t in pending_targets if t["stage"] == "planned"), None
        )
        alternatives = None
        if replaceable:
            alternatives = trees.find_tree_spots(
                config["sapling"],
                candidate_limit=config["candidate_limit"],
                search_steps=config["search_steps"],
                search_state=search_state,
                failed_goals=failed_goals,
                allow_exploration=True,
                avoid_positions=[t["position"] for t in task["targets"]],
                min_spacing=4,
            )
            for spot in alternatives["spots"]:
                p = spot["position"]
                if any(
                    math.hypot(p[0] - t["position"][0], p[2] - t["position"][2]) < 4
                    for t in task["targets"]
                ):
                    continue
                candidate = {"position": list(p), "stage": "planned", "owned": False}
                if math.dist(player, p) > 3.5 and not approach_to(candidate):
                    continue
                replaceable["stage"] = "abandoned"
                replaceable["reason"] = (
                    "Unplanted plan replaced by a confirmed accessible site"
                )
                task["targets"].append(candidate)
                return next_step()
            action = (alternatives.get("exploration") or {}).get("next_action")
            if action:
                return finish("needs_move", scan=alternatives, next_action=action)
        return finish(
            "blocked",
            ROUTE_BLOCKED,
            current_target=current["position"],
            scan=alternatives,
        )

    position = current["position"]
    if approach:
        if approach.get("standing_position"):
            current.setdefault("return_waypoints", []).append(
                approach["standing_position"]
            )
        return finish(
            "needs_move",
            current_target=position,
            next_action={
                "tool": "agent_action",
                "arguments": {"action": approach["command"]},
            },
        )

    block = _block(position)
    if current["stage"] == "planned":
        if block != "minecraft:air":
            current["stage"] = "abandoned"  # Existing saplings are not NEW plantings.
            return next_step()
        current["stage"] = "planting"
        save(task)
        placement = trees.plant_sapling_at(*position, config["sapling"])
        if placement.get("already_present"):
            current["stage"] = "abandoned"
            return next_step()
        if not placement.get("planted"):
            if placement.get("status") in (
                "invalid_soil_or_occupied",
                "protected_or_obstructed",
            ):
                current["stage"] = "abandoned"
                return next_step()
            return finish(
                "blocked",
                "Placement was not confirmed; do not retry blindly",
                interaction=placement,
            )
        current["stage"], current["owned"] = "planted", True
        save(task)
        if config["mode"] == "plant":
            return next_step()
        block = sapling_id
    if block == log_id:
        current["stage"] = "grown"
        return next_step()
    if block != sapling_id:
        return finish(
            "blocked",
            "Tracked sapling disappeared or changed; no growth is confirmed",
            current_target=position,
        )

    attempts_left = max(0, trees.MAX_GROWTH_ATTEMPTS - current.get("attempts", 0))
    for _ in range(min(attempts_left, budget, counts.get("minecraft:bone_meal", 0))):
        current["stage"] = "fertilizing"
        task["bone_meal_charged"] += 1  # Reserve before a click that may time out.
        current["attempts"] = current.get("attempts", 0) + 1
        save(task)
        dose = trees.apply_bone_meal_at(*position)
        safe_no_action = (
            dose.get("reason")
            in ("active_game_task", "no_bone_meal", "target_not_sapling")
            and "interaction" not in dose
        )
        if safe_no_action:
            task["bone_meal_charged"] -= 1
            current["attempts"] -= 1
            current["stage"] = "planted"
            if dose.get("reason") == "active_game_task":
                return finish("waiting_for_game_task")
            return finish("in_progress", interaction=dose)
        if not dose.get("applied") and not dose.get("retry_safe"):
            return finish(
                "blocked",
                "Bone meal interaction unconfirmed; reserved dose counts against the total budget",
                interaction=dose,
            )
        current["stage"] = "grown" if dose.get("tree_grown") is True else "planted"
        save(task)
        if current["stage"] == "grown":
            return next_step()
    if current.get("attempts", 0) >= trees.MAX_GROWTH_ATTEMPTS:
        current["exhausted"] = True
        current["reason"] = (
            "Five attempts without confirmed growth; leave the sapling and try the next target"
        )
        return next_step()
    if task["bone_meal_charged"] >= config["max_bone_meal"]:
        return finish("blocked", "Total bone meal budget exhausted")
    return next_step()
