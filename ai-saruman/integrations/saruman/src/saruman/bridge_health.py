"""Connection health is separate from the process heartbeat and saved game facts."""
import logging
import time


NATIVE_MAX_AGE = 10.0


class BridgeHealth:
    def __init__(self, game, clock=time.time):
        self.game, self.clock = game, clock

    def unavailable(self, error):
        now = self.clock()
        previous = dict(self.game.get("mod_runtime", {}).items())
        last = previous.get("last_observation", previous.get("updated"))
        reason = f"{type(error).__name__}: {error}"
        # RedisDict intentionally accepts a mapping, not dict.update keywords.
        self.game.update({"ingame": False, "mc_started": False, "alive": None})
        self.game["mod_runtime"] = {**previous, "status": "unavailable", "in_world": False,
                                    "error": reason, "updated": now,
                                    "last_observation": last, "stale": True}
        self.game["bridge_health"] = {"status": "recovering", "updated": now, "error": reason}

    def observed(self):
        self.game["bridge_health"] = {"status": "connected", "updated": self.clock(), "error": None}

    def in_world(self, entry):
        # False is a valid title-screen observation. A failed read requires a
        # new gateway, callback registration and native guard, not an endless
        # wait inside the old JVM connection.
        value = bool(entry.inGame())
        self.game.update({"ingame": value, "mc_started": True})
        self.observed()
        return value

    def run(self, update, active):
        try:
            update()
            if active():
                self.unavailable(RuntimeError("Minecraft updater stopped unexpectedly; reconnecting"))
        except Exception as error:
            self.unavailable(error)
            logging.exception("Minecraft updater interrupted; connection will be rebuilt")


def close_bridge(gateway, *, native=None, water=None, combat=None, preserve_goals=False):
    """A failed JVM cleanup must not keep the callback port or transport open."""
    steps = []
    if native is not None:
        steps.append(("native", lambda: native.close(stop=not preserve_goals)))
    if water is not None:
        steps.append(("water", lambda: water.close(gateway)))
    if combat is not None:
        steps.append(("combat", lambda: combat.close(gateway)))
    steps.extend((("callback server", gateway.shutdown_callback_server), ("gateway", gateway.close)))
    for name, close in steps:
        try:
            close()
        except Exception:
            logging.exception("Cannot close Minecraft %s; continuing cleanup", name)


def native_runtime_status(game, running, clock=time.time):
    native = dict(game.get("mod_runtime", {}).items())
    updated = native.get("updated")
    age = clock()-updated if isinstance(updated, (int, float)) else None
    fresh = age is not None and 0 <= age <= NATIVE_MAX_AGE and not native.get("stale")
    available = fresh and native.get("status") != "unavailable"
    error = native.get("error")
    if not fresh and not error:
        error = "Игровые данные устарели; ожидание нового состояния Minecraft"
    return {
        "agent_running": running,
        "world_ready": bool(available and native.get("in_world")),
        "native_status": native.get("status", "connecting") if available else "unavailable",
        "native_error": error,
        "native_updated": updated,
        "native_fresh": bool(fresh),
        "native_activity": dict(native.get("activity", {}).items()) if available else {},
        "native_stop_kind": native.get("stop_kind") if available else None,
        "player_alive": native.get("player_alive") if available else None,
        "manual_stop_latched": bool(available and native.get("manual_stop_latched")),
    }
