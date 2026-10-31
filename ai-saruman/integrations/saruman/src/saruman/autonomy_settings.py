"""Validated activity weights shared by the UI and agent processes."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "data/autonomy-settings.json"
CATALOG_PATH = ROOT / "resources/Minecraft/autonomous_activities.json"


def catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def validate_weights(weights, activities):
    if not isinstance(weights, dict) or set(weights) != {a["id"] for a in activities}:
        raise ValueError("Укажите вес каждого занятия из списка.")
    result = {}
    for name, value in weights.items():
        if (
            type(value) not in (int, float)
            or not 0 <= value <= 1_000_000
            or not math.isfinite(value)
        ):
            raise ValueError("Вес должен быть конечным числом от 0 до 1 000 000.")
        result[name] = float(value)
    return result


def load_weights(path=SETTINGS_PATH, activities=None):
    activities = catalog() if activities is None else activities
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        weights = {a["id"]: a["weight"] for a in activities}
    else:
        weights = json.loads(raw)["weights"]
        # Add the new activity to legacy three-activity settings, retaining every
        # operator value. An explicitly disabled autonomous schedule stays off.
        names = {a["id"] for a in activities}
        if (
            isinstance(weights, dict)
            and "trees" in names
            and set(weights) == names - {"trees"}
        ):
            default = next(a["weight"] for a in activities if a["id"] == "trees")
            weights = {**weights, "trees": default if any(weights.values()) else 0}
    return validate_weights(weights, activities)


def revision(weights):
    return hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()[:12]


def save_weights(weights, path=SETTINGS_PATH, activities=None):
    weights = validate_weights(weights, catalog() if activities is None else activities)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as stream:
            temporary = stream.name
            json.dump({"weights": weights}, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None and Path(temporary).exists():
            Path(temporary).unlink()
    return weights


def probabilities(activities, weights, cooldowns=None, now=0, unavailable=None):
    cooldowns = cooldowns or {}
    unavailable = unavailable or {}
    total = sum(weights.values())
    eligible = {
        a["id"]: weights[a["id"]]
        for a in activities
        if cooldowns.get(a["id"], 0) <= now and a["id"] not in unavailable
    }
    available = sum(eligible.values())
    return [
        {
            "id": a["id"],
            "label": a.get("label", a["id"]),
            "weight": weights[a["id"]],
            "probability": 100 * weights[a["id"]] / total if total else 0,
            "next_probability": 100 * eligible.get(a["id"], 0) / available
            if available
            else 0,
            "cooldown_seconds": max(0, math.ceil(cooldowns.get(a["id"], 0) - now)),
            "unavailable_reason": unavailable.get(a["id"]),
        }
        for a in activities
    ]
