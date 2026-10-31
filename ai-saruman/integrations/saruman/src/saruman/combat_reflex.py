"""Immediate bridge-owned defense: never waits for the chat model to finish."""

import time
from copy import deepcopy

from py4j.protocol import Py4JError

from saruman.combat_display import CombatDisplay
from saruman.hunting import defend_step
from saruman.navigation import NavigationGuard, check_recent_failure
from saruman.survival import water_state
from saruman.world_actions import on_client, set_bool_on_client


class CombatReflex:
    def __init__(self, clock=time.monotonic):
        self.clock, self.next_poll = clock, 0
        self.original_defense = None
        self.saved_task = None
        self.saved_context = None
        self.saved_guards = None
        self.navigation = None
        self.world_session = None
        self.display = CombatDisplay(clock)

    def _discard(self, game, reason):
        self.saved_task = self.saved_context = self.saved_guards = None
        game["combat_checkpoint"] = {"status": "discarded", "reason": reason}

    def _capture(self, gateway, game):
        if game.get("navigation", {}).get("status") in (
            "stop_requested",
            "stopping",
            "stop_failed",
        ) or game.get("collection", {}).get("status") in ("stopping", "stop_failed"):
            return  # Do not revive work that was already being cancelled.
        if (
            self.saved_task is not None
            or game.get("tree_task", {}).get("status") == "active"
            or game.get("autonomy", {}).get("activity")
        ):
            return  # Counted goals and hobbies have their own intent checkpoints.
        if (
            game.get("navigation", {}).get("purpose") == "combat"
            and game.get("navigation", {}).get("status") == "running"
        ):
            return
        mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
        task = mod.getUserTaskChain().getCurrentTask()
        if task is None:
            return
        context = {
            "world": int(gateway.jvm.java.lang.System.identityHashCode(mod.getWorld())),
            "command_revision": game.get("work_command_revision"),
            "player_intent": game.get("player_priority", {}).get("intent"),
        }
        guards = None
        if isinstance(self.navigation, NavigationGuard):
            guards = {
                "collection": deepcopy(vars(self.navigation.collection)),
                "navigation": {
                    key: deepcopy(getattr(self.navigation, key))
                    for key in ("target", "purpose", "started", "progress_at", "best")
                    if hasattr(self.navigation, key)
                },
            }
        self.saved_task, self.saved_context = task, context
        self.saved_guards = guards
        game["combat_checkpoint"] = {"status": "saved", **self.saved_context}

    def _resume(self, gateway, game):
        if (
            self.saved_task is None
            or game.get("self_defense", {}).get("active")
            or game.get("survival", {}).get("active")
        ):
            return
        context = self.saved_context
        mod = gateway.jvm.adris.altoclef.AltoClef.getInstance()
        world = int(gateway.jvm.java.lang.System.identityHashCode(mod.getWorld()))
        if (
            world != context["world"]
            or game.get("work_command_revision") != context["command_revision"]
            or game.get("player_priority", {}).get("intent") != context["player_intent"]
            or game.get("autonomy", {}).get("paused")
        ):
            self._discard(game, "World, operator intent or command changed")
            return
        if not gateway.entry_point.inGame():
            self._discard(game, "Disconnected")
            return
        if gateway.entry_point.hasActiveTask():
            if game.get("navigation", {}).get("status") not in (
                "stop_requested",
                "stopping",
                "stop_failed",
            ):
                self._discard(game, "Another native task owns the character")
            return
        priority = game.get("player_priority", {})
        if (
            priority.get("active")
            or priority.get("queue")
            or priority.get("awaiting_stop")
        ):
            return
        state = water_state(gateway)
        if not state or state["wet"] or state["submerged"] or not state["grounded"]:
            return
        if self.saved_guards:
            route = self.saved_guards["navigation"]
            collection = self.saved_guards["collection"]
            if route.get("target"):
                if time.monotonic() - route["started"] >= self.navigation.MAX_SECONDS:
                    self._discard(game, "Original route time limit expired")
                    return
                try:
                    check_recent_failure(route["target"], self.navigation.failures)
                except ValueError:
                    self._discard(game, "Original destination recently failed")
                    return
            if (
                collection.get("goal")
                and time.monotonic() - collection["started"]
                >= self.navigation.collection.MAX_SECONDS
            ):
                self._discard(game, "Original collection time limit expired")
                return
        task = self.saved_task
        guards = self.saved_guards
        self.saved_task = self.saved_context = None  # Never replay an unknown dispatch.
        self.saved_guards = None
        game["combat_checkpoint"] = {"status": "resume_requested", **context}
        on_client(
            gateway,
            mod,
            "adris.altoclef.AltoClef",
            "runUserTask",
            ("adris.altoclef.tasksystem.Task",),
            (task,),
        )
        if guards:
            route = guards["navigation"]
            if route.get("target"):
                for key, value in route.items():
                    setattr(self.navigation, key, value)
                self.navigation.progress_at = time.monotonic()
                self.navigation.stopping = False
                self.navigation._publish(
                    game,
                    "running",
                    reason="Resumed after combat; original route limit preserved",
                )
            collection = guards["collection"]
            if collection.get("goal"):
                vars(self.navigation.collection).update(collection)
                self.navigation.collection.progress_at = time.monotonic()
                self.navigation.collection.publish(
                    game,
                    "running",
                    reason="Resumed after combat; original collection limit preserved",
                )
        game["hasActiveTask"] = True

    def close(self, gateway):
        if self.original_defense is not None:
            settings = (
                gateway.jvm.adris.altoclef.AltoClef.getInstance().getModSettings()
            )
            set_bool_on_client(gateway, settings, "mobDefense", self.original_defense)
            self.original_defense = None

    def tick(self, gateway, game, navigation, *, enabled):
        now = self.clock()
        if now < self.next_poll:
            return
        self.next_poll = now + 0.5
        game["combat_pursuit_runtime"] = "ranged-defense-v2"
        world = game.get("world_session")
        if (
            world is not None
            and self.world_session is not None
            and world != self.world_session
        ):
            game["combat_watch"] = {}
            game["self_defense"] = {"active": False, "status": "world_changed"}
            if self.saved_task is not None:
                self._discard(game, "World changed")
        if world is not None:
            self.world_session = world
        controls = game.get("combat_controls", {})
        enabled = enabled and controls.get("defend_self", False)
        game["scepter_enabled"] = bool(controls.get("cast_scepter", False))
        try:
            if not enabled or game.get("alive") is not True:
                if self.saved_task is not None:
                    self._discard(
                        game, "Agent stopped, defense disabled or player died"
                    )
                self.close(gateway)
                if game.get("self_defense", {}).get("approaching"):
                    navigation.interrupt_for_combat(gateway.entry_point, game)
                game["self_defense"] = {"active": False, "status": "paused"}
                self.display.tick(gateway, game)
                return
            if self.original_defense is None:
                settings = (
                    gateway.jvm.adris.altoclef.AltoClef.getInstance().getModSettings()
                )
                self.original_defense = bool(settings.isMobDefense())
                # MobDefenseChain otherwise replaces exact attacks with native fleeing.
                # Eating/drowning/respawn chains and attack exclusions remain separate.
                set_bool_on_client(gateway, settings, "mobDefense", False)
            if game.get("survival", {}).get("active"):
                return
            with navigation.lock:
                self.navigation = navigation
                result = defend_step(game, navigation, before_stop=self._capture)
                game["self_defense"] = {
                    **dict(game.get("self_defense", {}).items()),
                    "result": result,
                    "observed_at": time.time(),
                    "owner": "bridge",
                }
                self._resume(gateway, game)
                self.display.tick(gateway, game)
        except (Py4JError, OSError, ValueError, TypeError, RuntimeError) as error:
            game["self_defense"] = {
                **dict(game.get("self_defense", {}).items()),
                "status": "retrying",
                "error": type(error).__name__,
            }
            self.display.tick(gateway, game)
