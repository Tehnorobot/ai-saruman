"""Bounded exploration of observed ground, keeping memory between local scans."""

import math
import os
import time


def search_max_moves():
    value = int(os.getenv("SARUMAN_SEARCH_MAX_MOVES", "64"))
    if not 4 <= value <= 256:
        raise ValueError("SARUMAN_SEARCH_MAX_MOVES must be between 4 and 256")
    return value


def plan_exploration(paths, center, state, max_moves, failures=(), radius=5):
    """Return one proposed walk; this function never moves or changes the world."""
    visited = state.setdefault("visited", [])
    proposals = state.setdefault("proposals", [])
    trail = state.setdefault("trail", [])
    if trail and math.dist(center, trail[-1]) > 16:
        trail.clear()
    exhausted = [
        row for row in state.get("exhausted", []) if row["until"] > time.time()
    ]
    state["exhausted"] = exhausted
    retreat = state.get("retreat")
    if retreat and math.dist(center, retreat["position"]) < 1.5:
        # Pop the explored branch instead of appending A again and proposing B.
        index = next(
            (
                i
                for i in range(len(trail) - 1, -1, -1)
                if math.dist(trail[i], retreat["position"]) < 1.5
            ),
            None,
        )
        if index is not None:
            del trail[index + 1 :]
        state.pop("retreat", None)
    if not trail or math.dist(center, trail[-1]) >= 1:
        trail.append(list(center))
        del trail[:-16]
    if not any(math.dist(center, p) < 2 for p in visited):
        visited.append(list(center))
    if not max_moves:
        return {
            "status": "exploration_disabled",
            "moves_used": len(proposals),
            "max_moves": 0,
            "areas_scanned": len(visited),
            "hint": "Exploration is disabled; only the local snapshot was checked.",
        }

    def excluded(p):
        return any(
            f["until"] > time.time() and math.dist(p, f["target"]) <= 1.5
            for f in failures
        )

    def dead_end(p):
        return any(math.dist(p, row["position"]) < 1.5 for row in exhausted)

    covered = {
        (p[0] + dx, p[2] + dz)
        for p in visited
        for dx in range(-radius, radius + 1)
        for dz in range(-radius, radius + 1)
    }
    candidates, connectors = [], []
    for p, steps in paths.items():
        if (
            not steps
            or math.dist(p, center) < 1
            or math.dist(p, center) > 4.5
            or excluded(p)
        ):
            continue
        new_cells = sum(
            (p[0] + dx, p[2] + dz) not in covered
            for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)
        )
        if new_cells and not any(math.dist(p, previous) < 2.5 for previous in visited):
            candidates.append((new_cells, -steps, p))
        # Sometimes new terrain is only reachable through known ground. Do not
        # confuse lack of novel columns with lack of a safe connected walk.
        # Recent actual arrivals, rather than proposed commands, prevent A-B-A.
        if not dead_end(p) and not any(
            math.dist(p, previous) < 1.5 for previous in trail[:-1]
        ):
            visits = sum(math.dist(p, previous) < 2.5 for previous in visited)
            connectors.append((-visits, new_cells, math.dist(p, center), -steps, p))

    # The only exit from a cul-de-sac can be a recently visited position. A
    # deliberate retreat consumes the same movement budget, and its origin is
    # marked exhausted so a different activity cannot immediately send us back.
    backtracks = []
    if not candidates and not connectors:
        for previous_center in reversed(trail[:-1]):
            choices = [
                p
                for p, steps in paths.items()
                if steps
                and 1 <= math.dist(p, center) <= 4.5
                and not excluded(p)
                and not dead_end(p)
                and math.dist(p, previous_center)
                < math.dist(center, previous_center) - 0.5
            ]
            if choices:
                position = min(
                    choices, key=lambda p: (math.dist(p, previous_center), paths[p])
                )
                backtracks.append(position)
                break

    # Repeating a read at the same location does not spend another search step.
    previous = proposals[-1] if proposals else None
    if (
        previous
        and tuple(previous["from"]) == center
        and (
            any(c[2] == tuple(previous["position"]) for c in candidates)
            or any(c[-1] == tuple(previous["position"]) for c in connectors)
            or tuple(previous["position"]) in backtracks
        )
    ):
        proposal = previous
    elif len(proposals) >= max_moves:
        return {
            "status": "search_budget_exhausted",
            "moves_used": len(proposals),
            "max_moves": max_moves,
            "areas_scanned": len(visited),
            "hint": "Only these observed areas were searched; do not claim no suitable soil exists everywhere.",
        }
    elif not candidates and not connectors and not backtracks:
        return {
            "status": "no_safe_frontier",
            "moves_used": len(proposals),
            "max_moves": max_moves,
            "areas_scanned": len(visited),
            "safe_walk_positions": max(0, len(paths) - 1),
            "hint": "No untried safe local step remains after recent arrivals and failed routes are excluded. This is a local route limitation, not proof that soil is absent.",
        }
    else:
        if candidates:
            new_cells, _, position = max(candidates)
            route_kind = "frontier"
        elif connectors:
            _, new_cells, _, _, position = max(connectors)
            route_kind = "connector"
        else:
            position = backtracks[0]
            new_cells, route_kind = 0, "backtrack"
            exhausted.append({"position": list(center), "until": time.time() + 60})
            state["exhausted"] = exhausted[-64:]
            state["retreat"] = {"position": list(position)}
        proposal = {
            "from": list(center),
            "position": list(position),
            "new_columns_estimate": new_cells,
            "walk_steps": paths[position],
            "route_kind": route_kind,
        }
        proposals.append(proposal)
    command = "@goto " + " ".join(map(str, proposal["position"]))
    return {
        "status": "move_to_scan",
        **proposal,
        "command": command,
        "moves_used": len(proposals),
        "max_moves": max_moves,
        "areas_scanned": len(visited),
        "next_action": {"tool": "agent_action", "arguments": {"action": command}},
        "hint": "Walk once, wait for completion, then rescan/grow_tree. A proposal is not movement or discovery; do not repeat scans from the same position.",
    }
