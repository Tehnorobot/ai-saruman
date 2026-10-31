"""Small local execution guides, selected by task; never arbitrary file paths."""

import time
from pathlib import Path

DIRECTORY = (
    Path(__file__).resolve().parents[2] / "resources" / "Minecraft" / "procedures"
)
CATALOGUE = {
    "farm_crops": "Посадить урожай или собрать только зрелый для еды, без ухода и ожидания роста.",
    "hunt_hostiles": "Найти враждебного моба или ответить подтверждённому нападающему; мирных не трогать.",
    "scepter": "Получить основной посох, проверить его заклинания и применить магию с защитой мирных существ.",
    "grow_tree": "Вырастить дуб или берёзу: материалы → место → посадка → ограниченный расход муки → проверка ствола.",
    "plant_sapling": "Только найти подходящую точку и посадить саженец, без расходования костной муки.",
    "apply_bone_meal": "Применить муку к уже посаженному саженцу; различать расход и рост.",
}


def select_procedure(task):
    text = task.lower().strip()
    if text in CATALOGUE:
        return text
    if any(word in text for word in ("посох", "скипетр", "scepter", "заклинан")):
        return "scepter"
    if any(
        word in text
        for word in (
            "урож",
            "пшениц",
            "морков",
            "карто",
            "свёкл",
            "свекл",
            "гряд",
            "crop",
            "harvest",
        )
    ):
        return "farm_crops"
    if any(word in text for word in ("враждебн", "злых моб", "злых монстр", "hostile")):
        return "hunt_hostiles"
    if (
        "выраст" in text
        and any(word in text for word in ("дерев", "дуб", "берёз", "берез"))
    ) or ("grow" in text and "tree" in text):
        return "grow_tree"
    if (
        "посад" in text
        and any(word in text for word in ("сажен", "дерев", "дуб", "берёз", "берез"))
    ) or ("plant" in text and "sapling" in text):
        return "plant_sapling"
    if (
        ("костн" in text and "мук" in text)
        or "bone meal" in text
        or "bone_meal" in text
    ):
        return "apply_bone_meal"
    return None


def read_procedure(task=""):
    name = select_procedure(task)
    if name is None:
        return {
            "procedures": CATALOGUE.copy(),
            "hint": "Select a procedure ID matching the requested outcome; do not invent commands.",
        }
    return {
        "procedure": name,
        "instructions": (DIRECTORY / f"{name}.md").read_text(encoding="utf-8"),
    }


def procedure_prompt(task):
    result = read_procedure(task)
    if "instructions" not in result:
        return ""
    return (
        "\nLocal Minecraft execution procedure (apply within the current requested task):\n"
        + result["instructions"]
    )


class ProcedureContinuation:
    """Wake a bounded procedure after native work; no model calls while waiting."""

    def __init__(self, goal):
        self.goal = goal
        self.remaining = 4
        self.pending = False
        self.ready_after = self.deadline = 0
        self.done = False
        self.navigation_exhausted = False
        self.exploration_command = None
        self.pending_exploration = False
        self.batch = False
        self.batch_scheduled = False
        self.batch_expected = None
        self.collection_goal = None

    def blocks_task_start(self, name, arguments):
        command = arguments.get("action", "").strip().removeprefix("@").split()[:1]
        if self.batch and self.pending and self.batch_scheduled:
            native = name == "obtain_item" or (
                name == "agent_action" and command in (["get"], ["goto"])
            )
            return native and (name, arguments) != self.batch_expected
        return ((self.pending and not self.done) or self.navigation_exhausted) and (
            name == "obtain_item"
            or (self.batch and name == "manage_trees")
            or (name == "agent_action" and command in (["get"], ["goto"]))
        )

    def observe(self, name, arguments, result):
        if self.done:
            return
        if (
            name == "manage_trees"
            and isinstance(result, dict)
            and result.get("task_id")
        ):
            self.batch = True
            if result.get("status") in ("completed", "partial", "blocked", "cancelled"):
                self.done, self.pending = True, False
                return
            self.pending = True
            self.batch_scheduled = result.get("status") != "waiting_for_game_task"
            action = result.get("next_action") or result.get("resume") or {}
            self.batch_expected = (action.get("tool"), action.get("arguments", {}))
            self.pending_exploration = True
            now = time.monotonic()
            self.ready_after, self.deadline = (
                now + (0 if result.get("status") == "in_progress" else 2),
                now + 180,
            )
            return
        if name in ("grow_tree", "find_tree_planting_spots") and isinstance(
            result, dict
        ):
            scan = result.get("scan", result)
            self.exploration_command = (scan.get("exploration") or {}).get("command")
        if name == "agent_action" and arguments.get("action", "").strip().removeprefix(
            "@"
        ).split()[:1] in (["stop"], ["cancel"]):
            self.done = True
            self.pending = False
            return
        if (
            name == "grow_tree"
            and isinstance(result, dict)
            and result.get("status")
            in (
                "grown",
                "bone_meal_budget_exhausted",
                "attempts_exhausted",
                "interaction_not_confirmed",
                "placement_not_confirmed",
            )
        ):
            self.done = True
            self.pending = False
            return
        queued = (
            name == "obtain_item"
            and isinstance(result, str)
            and result.startswith("Queued obtaining/")
        )
        queued |= (
            name == "agent_action"
            and result is True
            and arguments.get("action", "").strip().removeprefix("@").split()[:1]
            in (["get"], ["goto"])
        )
        waiting = (
            isinstance(result, dict) and result.get("status") == "waiting_for_game_task"
        )
        if queued and self.batch_scheduled:
            self.pending = False
            self.batch_scheduled = False
        if (queued or waiting) and not self.pending and self.remaining:
            now = time.monotonic()
            self.pending = True
            self.pending_exploration = self.batch or (
                queued
                and name == "agent_action"
                and arguments.get("action") == self.exploration_command
                and self.exploration_command is not None
            )
            self.exploration_command = None
            self.ready_after, self.deadline = now + 2, now + 180
            if queued:
                parts = arguments.get("action", "").strip().removeprefix("@").split()
                if name == "obtain_item":
                    self.collection_goal = {
                        "item": arguments.get("item", "").removeprefix("minecraft:"),
                        "count": arguments.get("count", 1),
                    }
                elif parts[:1] == ["get"] and len(parts) == 3 and parts[2].isdigit():
                    self.collection_goal = {
                        "item": parts[1].removeprefix("minecraft:"),
                        "count": int(parts[2]),
                    }
                else:
                    self.collection_goal = None

    def navigation_failed(self, navigation):
        self.pending = False
        self.batch_scheduled = False
        self.remaining = max(0, self.remaining - 1)
        if not self.remaining or navigation.get("status") == "stop_failed":
            self.done = True
            self.navigation_exhausted = True
        return (
            "Minecraft route failed: "
            + str(navigation)
            + ". Do not repeat this goal or its neighbours. Check actual inventory and in-reach sites first; "
            + (
                "report the blockage without another route."
                if self.done
                else "use at most one different observed safe approach, or report the blockage."
            )
            + " Original request: "
            + self.goal
        )

    def poll(self, busy, collection=None):
        if self.done or not self.pending or time.monotonic() < self.ready_after:
            return None
        if (
            self.collection_goal
            and collection
            and collection.get("goal") == self.collection_goal
        ):
            status = collection.get("status")
            if status in (
                "blocked",
                "stop_failed",
                "ownership_lost",
                "ownership_unconfirmed",
            ):
                self.done, self.pending = True, False
                return (
                    "Native collection cannot continue safely: "
                    + str(collection)
                    + ". Preserve counted tree progress and targets; do not restart the collection blindly. Original request: "
                    + self.goal
                )
            if busy and status in ("running", "stopping", "observing", "suspended"):
                if time.monotonic() - collection.get("observed_at", 0) <= 10:
                    return None  # Bridge owns deadlines and confirms cancellation.
                self.done, self.pending = True, False
                return (
                    "Collection status is stale; cannot confirm native progress or cancellation. Preserve saved targets/progress and check the Minecraft bridge. Original request: "
                    + self.goal
                )
        if busy and time.monotonic() < self.deadline:
            return None
        self.pending = False
        self.batch_scheduled = False
        if not self.pending_exploration:
            self.remaining -= 1
        self.pending_exploration = False
        self.collection_goal = None
        if busy:
            self.done = True
            return (
                "Minecraft procedure timed out waiting for native work. Report current task and blockage; do not restart it blindly. Original request: "
                + self.goal
            )
        if not self.remaining:
            self.done = True
        if self.batch:
            return (
                "Resume manage_trees with its saved arguments and verified tree_task.progress. "
                "Continue the remaining quantity, not the original count from zero. "
                "Follow next_action; do not switch to untracked single-tree actions. Original request: "
                + self.goal
            )
        return (
            "Continue the previously requested Minecraft procedure now that native work is no longer active. Check actual inventory/results, perform the next tool step, do not merely announce progress. Original request: "
            + self.goal
        )
