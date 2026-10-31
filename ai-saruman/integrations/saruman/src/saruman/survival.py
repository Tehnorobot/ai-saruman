"""Water rescue runs in the bridge, independently of model calls and task queues."""

import math
import time
from collections import deque
from copy import deepcopy

from py4j.protocol import Py4JError

from saruman.combat_pursuit import watch_target
from saruman.minecraft import _java_to_python
from saruman.world_actions import on_client


def water_state(gateway):
    player = gateway.jvm.adris.altoclef.AltoClef.getInstance().getPlayer()
    if player is None:
        return None
    return {
        "wet": bool(player.method_5799()),
        "submerged": bool(player.method_5869()),
        "air": int(player.method_5669()),
        "max_air": int(player.method_5748()),
        "position": [
            float(player.method_23317()),
            float(player.method_23318()),
            float(player.method_23321()),
        ],
        "grounded": bool(player.method_24828()),
    }


def buoyancy(gateway, enabled):
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    controls = mod.getInputControls()
    inputs = gateway.jvm.kaptainwutax.tungsten.path.movements.Input
    if enabled:
        on_client(
            gateway,
            controls,
            "adris.altoclef.control.InputControls",
            "release",
            ("kaptainwutax.tungsten.path.movements.Input",),
            (inputs.SNEAK,),
        )
    on_client(
        gateway,
        controls,
        "adris.altoclef.control.InputControls",
        "hold" if enabled else "release",
        ("kaptainwutax.tungsten.path.movements.Input",),
        (inputs.JUMP,),
    )


def swim_input(gateway, target=None):
    """Own only forward while rescuing; jump is maintained separately for breathing."""
    mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
    if target is not None:
        player = mod.getPlayer()
        aimed = gateway.entry_point.lookAt(
            target[0] + 0.5, float(player.method_23320()), target[2] + 0.5
        )
        if not aimed:
            target = None
    on_client(
        gateway,
        mod.getInputControls(),
        "adris.altoclef.control.InputControls",
        "hold" if target is not None else "release",
        ("kaptainwutax.tungsten.path.movements.Input",),
        (gateway.jvm.kaptainwutax.tungsten.path.movements.Input.MOVE_FORWARD,),
    )


def swim_paths(rules, center, radius=5, blocked=()):
    """Water is a volume, not a standing cell: swimming feet can be below the surface."""
    paths, pending = {center: []}, deque([center])
    while pending:
        p = pending.popleft()
        for dx, dy, dz in (
            (1, 0, 0),
            (-1, 0, 0),
            (0, 0, 1),
            (0, 0, -1),
            (0, 1, 0),
            (1, 1, 0),
            (-1, 1, 0),
            (0, 1, 1),
            (0, 1, -1),
        ):
            q = (p[0] + dx, p[1] + dy, p[2] + dz)
            if (
                q in paths
                or q in blocked
                or any(abs(q[i] - center[i]) > radius - 1 for i in range(3))
            ):
                continue
            wet = not rules.free(q) and rules.free(q, allow_water=True)
            # Surface_water identifies a water cell even if its head is also submerged.
            if not (
                rules.free(q, allow_water=True)
                and rules.free((q[0], q[1] + 1, q[2]), allow_water=True)
            ):
                continue
            if not wet and not getattr(rules, "shore_support", rules.support)(
                (q[0], q[1] - 1, q[2])
            ):
                continue
            if (
                (dx or dz)
                and dy
                and not rules.free((p[0], p[1] + 1, p[2]), allow_water=True)
            ):
                continue
            paths[q] = paths[p] + [q]
            pending.append(q)
    return paths


def nearest_dry_shore(gateway, failed):
    from saruman.trees import _position
    from saruman.world_rules import NativeWorldRules

    entry = gateway.entry_point
    snapshot = _java_to_python(entry.getGameState())
    player = snapshot.get("self") if isinstance(snapshot, dict) else None
    if not isinstance(player, dict) or not isinstance(player.get("pos"), str):
        return None
    center = _position(player["pos"])
    observed = {
        _position(b["position"]): b["block"]
        for b in _java_to_python(entry.getBlocksAround(5))
    }
    rules = NativeWorldRules(gateway, observed, center, 5)
    paths = swim_paths(rules, center, blocked=failed)
    paths = {p: path for p, path in paths.items() if not any(q in failed for q in path)}
    dry = [
        p
        for p in paths
        if p != center
        and p not in failed
        and rules.free(p)
        and getattr(rules, "shore_support", rules.support)((p[0], p[1] - 1, p[2]))
    ]
    if dry:
        goal = min(dry, key=lambda p: (len(paths[p]), math.dist(p, center)))
    else:
        # No shore in the five-block snapshot: swim into new observed water instead
        # of floating forever. Replan on the next snapshot, never invent terrain.
        frontier = [p for p in paths if p not in failed and math.dist(p, center) >= 3]
        if not frontier:
            return None
        goal = max(frontier, key=lambda p: (math.dist(p, center), -len(paths[p])))
    # Follow the observed corridor, not an unconstrained native route to its end.
    return next(
        (p for p in paths[goal] if p[0] != center[0] or p[2] != center[2]), None
    )


class WaterSafety:
    POLL_SECONDS = 0.2

    def __init__(self, clock=time.monotonic):
        self.clock, self.next_poll = clock, 0
        self.active = self.holding = False
        self.target, self.failed = None, set()
        self.next_shore = self.route_started = 0
        self.forward = False
        self.native_task = self.native_world = None
        self.land_stop_sent = False
        self.under_since = None
        self.resume_counts = {}
        self.interruption_id = None
        self.water_position = None
        self.water_progress = 0

    def _world(self, gateway):
        mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
        return int(gateway.jvm.java.lang.System.identityHashCode(mod.getWorld()))

    def _publish(self, game, status, **fields):
        game["survival"] = {
            "active": self.active,
            "status": status,
            "target": list(self.target) if self.target else None,
            "interruption_id": self.interruption_id,
            **fields,
        }

    def close(self, gateway):
        if self.forward:
            swim_input(gateway)
            self.forward = False
        if self.holding:
            buoyancy(gateway, False)
            self.holding = False

    def tick(self, gateway, game, navigation, *, enabled):
        now = self.clock()
        if now < self.next_poll:
            return not self.active
        self.next_poll = now + self.POLL_SECONDS
        try:
            if not enabled or game.get("alive") is not True:
                if self.holding:
                    self.close(gateway)
                self.active, self.target = False, None
                self.failed.clear()
                self.native_task = self.native_world = None
                self.under_since = None
                self.water_position = None
                self._publish(game, "paused" if not enabled else "waiting_for_respawn")
                return True
            state = water_state(gateway)
            if state is None:
                self._publish(game, "unavailable")
                return not self.active
            if state.get("position"):
                game["position"] = ",".join(map(str, state["position"]))
            try:
                current_world = self._world(gateway)
            except (Py4JError, OSError, ValueError, TypeError, RuntimeError):
                current_world = None
            game["world_session"] = current_world
            if (
                not state["wet"]
                and not state["submerged"]
                and (not self.active or state.get("grounded", True))
            ):
                if self.holding:
                    self.close(gateway)
                if self.active:
                    # Retire our rescue route before letting normal work resume.
                    self.active, self.target = False, None
                    if gateway.entry_point.hasActiveTask():
                        self.active = True
                        if not self.land_stop_sent:
                            self.land_stop_sent = True
                            navigation.interrupt_for_survival(gateway.entry_point, game)
                        self._publish(game, "waiting_for_task_stop", **state)
                        return False
                    if (
                        self.native_task is not None
                        and self.native_world is not None
                        and self.native_world == current_world
                    ):
                        native = self.native_task
                        self.native_task = (
                            None  # Unknown dispatch must never be replayed.
                        )
                        on_client(
                            gateway,
                            gateway.jvm.adris.altoclef.AltoClef.getInstance(),
                            "adris.altoclef.AltoClef",
                            "runUserTask",
                            ("adris.altoclef.tasksystem.Task",),
                            (native,),
                        )
                        game["hasActiveTask"] = True
                    self.native_task = None
                self.active, self.target = False, None
                self.failed.clear()
                self.under_since = None
                self.water_position = None
                self._publish(game, "safe", **state)
                return True
            # Merely touching water is not an emergency. Native travel may swim
            # across the surface; cancelling it every tick created the stop loop.
            if state["submerged"]:
                self.under_since = now if self.under_since is None else self.under_since
            else:
                self.under_since = None
            position = state.get("position")
            if position and (
                self.water_position is None
                or math.hypot(
                    position[0] - self.water_position[0],
                    position[2] - self.water_position[2],
                )
                >= 0.4
            ):
                self.water_position, self.water_progress = position, now
            stalled = position is not None and now - self.water_progress >= 4
            combat = game.get("self_defense", {})
            combat_route = bool(
                combat.get("active")
                or (
                    game.get("navigation", {}).get("purpose") == "combat"
                    and game.get("navigation", {}).get("status") == "running"
                )
            )
            if (
                not self.active
                and not combat_route
                and state["air"] > state["max_air"] / 2
                and (self.under_since is None or now - self.under_since < 2)
                and not stalled
                and gateway.entry_point.hasActiveTask()
            ):
                self.holding = True
                buoyancy(gateway, True)
                self._publish(game, "swimming", **state)
                return True
            if not self.active:
                self.active = True
                self.interruption_id = time.time_ns()
                self.land_stop_sent = False
                self._publish(game, "surfacing", **state)
                if combat_route:
                    # Never revive the same chase after it led us into water.
                    self.native_task = self.native_world = None
                    target = combat.get("target")
                    if target:
                        watch_target(game, target, "Combat approach entered water")
                    game["self_defense"] = {
                        **dict(combat.items()),
                        "active": False,
                        "approaching": False,
                        "ending": None,
                        "status": "water_interrupted",
                        "reason": "Rescue first; combat route will not be restored",
                    }
                task = game.get("tree_task", {})
                if (
                    task.get("status") != "active"
                    and not combat_route
                    and gateway.entry_point.hasActiveTask()
                ):
                    # Keep the actual native goal (not a stale command string).
                    try:
                        mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
                        self.native_task = mod.getUserTaskChain().getCurrentTask()
                        self.native_world = current_world
                        signature = (
                            current_world,
                            int(
                                gateway.jvm.java.lang.System.identityHashCode(
                                    self.native_task
                                )
                            ),
                        )
                        self.resume_counts[signature] = (
                            self.resume_counts.get(signature, 0) + 1
                        )
                        if self.resume_counts[signature] > 2:
                            self.native_task = None
                        self.resume_counts = dict(
                            list(self.resume_counts.items())[-16:]
                        )
                    except (Py4JError, OSError, ValueError, TypeError, RuntimeError):
                        self.native_task = self.native_world = None
                if task.get("status") == "active":
                    saved = deepcopy(dict(task.items()))
                    saved.update(
                        status="paused",
                        pause_reason="Water rescue"
                        if current_world is not None
                        else "Water rescue; verify world before resuming",
                        resume_world=current_world,
                    )
                    deferred = deepcopy(list(game.get("deferred_goals", [])))
                    if not any(
                        row.get("task", {}).get("id") == saved.get("id")
                        for row in deferred
                    ):
                        deferred.append(
                            {
                                "task": deepcopy(saved),
                                "search": deepcopy(
                                    dict(game.get("tree_search", {}).items())
                                ),
                            }
                        )
                    game["deferred_goals"] = deferred
                    game["tree_task"] = saved
                # Even an unconfirmed stop must not prevent the emergency ascent.
                try:
                    navigation.interrupt_for_survival(gateway.entry_point, game)
                except (Py4JError, OSError, ValueError, TypeError, RuntimeError):
                    pass
            self.holding = True  # Release even after an ambiguous native timeout.
            buoyancy(gateway, True)
            reached = (
                self.target
                and state.get("position")
                and math.hypot(
                    state["position"][0] - self.target[0] - 0.5,
                    state["position"][2] - self.target[2] - 0.5,
                )
                < 0.55
            )
            if self.target and (reached or now - self.route_started >= 2):
                if not reached:
                    self.failed.add(self.target)
                self.target = None
                self.next_shore = now
            if not self.target and now >= self.next_shore:
                self.next_shore = now + 0.5
                target = nearest_dry_shore(gateway, self.failed)
                if target:
                    self.target, self.route_started = target, now
            self.forward = True
            swim_input(gateway, self.target)
            self._publish(
                game,
                "surfacing"
                if state["submerged"]
                else "reaching_shore"
                if self.target
                else "afloat",
                **state,
            )
            return False
        except (Py4JError, OSError, ValueError, TypeError, RuntimeError) as error:
            self._publish(
                game, "unavailable", reason=f"Water safety: {type(error).__name__}"
            )
            return not self.active
