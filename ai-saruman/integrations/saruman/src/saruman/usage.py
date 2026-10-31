"""Process-local Codex telemetry; polling the UI never calls the model or account API."""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime

TOKEN_FIELDS = (
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = {}
        self._recent = deque(maxlen=20)
        self._totals = dict.fromkeys(TOKEN_FIELDS, 0)
        self._missing = dict.fromkeys(TOKEN_FIELDS, 0)
        self._finished = self._failed = 0
        self._limits = {}
        self._limits_at = None
        self._limits_failed = False

    def begin(self, model, effort):
        identifier = uuid.uuid4().hex
        with self._lock:
            self._active[identifier] = {
                "model": model,
                "effort": effort or "default",
                "usage": {},
                "started": time.monotonic(),
                "status": "выполняется",
            }
        return identifier

    def update(self, identifier, trace):
        with self._lock:
            row = self._active[identifier]
            if trace.get("model"):
                row["model"] = trace["model"]
            if trace.get("context_window") is not None:
                row["context_window"] = trace["context_window"]
            # Notifications contain cumulative thread totals, never deltas.
            for key, value in (trace.get("usage") or {}).items():
                if key in TOKEN_FIELDS and type(value) is int and value >= 0:
                    row["usage"][key] = value

    def finish(self, identifier, *, failed=False):
        with self._lock:
            row = self._active.pop(identifier)
            row["seconds"] = time.monotonic() - row["started"]
            row["status"] = "ошибка" if failed else "завершён"
            self._finished += 1
            self._failed += int(failed)
            for key in TOKEN_FIELDS:
                self._totals[key] += row["usage"].get(key, 0)
                self._missing[key] += int(key not in row["usage"])
            self._recent.appendleft(row)

    def update_limits(self, payload):
        buckets = payload.get("rateLimitsByLimitId") or {}
        if not buckets and isinstance(payload.get("rateLimits"), dict):
            bucket = payload["rateLimits"]
            buckets = {bucket.get("limitId") or "codex": bucket}
        with self._lock:
            for key, bucket in buckets.items():
                if not isinstance(bucket, dict):
                    continue
                # Notifications can be sparse and update just one bucket/window.
                target = self._limits.setdefault(key, {})
                for name in ("primary", "secondary", "planType", "limitName"):
                    if name in bucket:
                        value = bucket[name]
                        if isinstance(value, dict) and isinstance(
                            target.get(name), dict
                        ):
                            target[name].update(copy.deepcopy(value))
                        else:
                            target[name] = copy.deepcopy(value)
            if buckets:
                self._limits_at = time.time()
                self._limits_failed = False

    def limits_unavailable(self):
        with self._lock:
            self._limits_failed = True

    def snapshot(self):
        with self._lock:
            totals, missing = self._totals.copy(), self._missing.copy()
            for row in self._active.values():
                for key in TOKEN_FIELDS:
                    totals[key] += row["usage"].get(key, 0)
                    missing[key] += int(key not in row["usage"])
            return copy.deepcopy(
                {
                    "totals": totals,
                    "missing": missing,
                    "finished": self._finished,
                    "failed": self._failed,
                    "active": list(self._active.values()),
                    "recent": list(self._recent),
                    "limits": self._limits,
                    "limits_at": self._limits_at,
                    "limits_failed": self._limits_failed,
                }
            )


usage_tracker = UsageTracker()


def _timestamp(value):
    try:
        return datetime.fromtimestamp(value).astimezone().strftime("%d.%m %H:%M:%S %Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return "неизвестно"


def usage_view():
    """Gradio callback: one snapshot shared by counters and recent-request table."""
    state = usage_tracker.snapshot()
    active, recent = state["active"], state["recent"]
    last = (active + recent + [{}])[0]
    selected = os.getenv("CORE_LLM_MODEL_NAME", "codex/auto")
    effort = os.getenv("CODEX_REASONING_EFFORT") or "default"
    lines = [
        f"**Настройка:** `{selected}` · reasoning `{effort}`",
        (
            f"**Фактическая модель:** `{last.get('model', 'ещё нет вызовов')}` · "
            f"reasoning `{last.get('effort', effort)}`"
        ),
        (
            f"**Запросы за текущий запуск:** {state['finished'] + len(active)} · "
            f"в работе: {len(active)} · завершено: {state['finished'] - state['failed']} · "
            f"ошибок: {state['failed']}"
        ),
        "| Токены за текущий запуск | Количество |",
        "| --- | ---: |",
    ]
    for key, label in zip(
        TOKEN_FIELDS,
        (
            "Входные",
            "Из них из кэша",
            "Выходные",
            "Из них рассуждения",
            "Всего",
        ),
    ):
        value = f"{state['totals'][key]:,}".replace(",", " ")
        if state["missing"][key]:
            value = "≥ " + value + " (есть запросы без данных)"
        lines.append(f"| {label} | {value} |")
    lines.append("")
    if last.get("context_window"):
        lines.append(f"Окно контекста модели: {last['context_window']:,} токенов.")
    lines.append(
        "Кэш входит во входные токены, рассуждения — в выходные; повторно они не суммируются."
    )
    lines.append("")
    lines.append(
        "**Лимиты аккаунта ChatGPT** (включают использование этого аккаунта в других сессиях):"
    )
    if not state["limits"]:
        lines.append(
            "Данные пока не получены. Запрашиваются при обращении агента к Codex."
        )
    for key, bucket in state["limits"].items():
        lines.append("")
        lines.append(f"Лимит `{key}` · план `{bucket.get('planType') or 'не указан'}`:")
        lines.append("")
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if not isinstance(window, dict):
                continue
            percent = window.get("usedPercent")
            remaining = (
                f"{max(0, 100 - percent):g}%"
                if type(percent) in (int, float)
                else "неизвестно"
            )
            used = f"{percent:g}%" if type(percent) in (int, float) else "неизвестно"
            lines.append(
                f"- Окно {window.get('windowDurationMins', '?')} мин: использовано {used}, "
                f"осталось {remaining}; сброс {_timestamp(window.get('resetsAt'))}."
            )
    lines.append("")
    if state["limits_at"]:
        lines.append(f"Последние данные лимитов: {_timestamp(state['limits_at'])}.")
    if state["limits_failed"]:
        lines.append(
            "Обновить лимиты не удалось; предыдущие значения, если есть, могут быть устаревшими."
        )
    lines.append("")
    lines.append(
        "Подписка ChatGPT: точная денежная стоимость запросов недоступна. "
        "Токены не переводятся в проценты лимита. UI обновляется раз в секунду; "
        "данные появляются по уведомлениям Codex, иногда только к концу ответа. "
        "Счётчики токенов обнуляются при перезапуске Python."
    )
    rows = []
    for row in active + recent:
        seconds = row.get("seconds", time.monotonic() - row["started"])
        rows.append(
            [
                row["status"],
                row["model"],
                row["effort"],
                *[str(row["usage"].get(key, "—")) for key in TOKEN_FIELDS],
                f"{seconds:.1f}",
            ]
        )
    return "\n\n".join(lines[:3]) + "\n\n" + "\n".join(lines[3:]), rows


def add_usage_panel():
    """Mount inside an existing Gradio Blocks/Tab; no agent or API starts here."""
    import gradio as gr

    with gr.Accordion("Использование модели и лимиты ChatGPT", open=True):
        initial_usage, initial_requests = usage_view()
        summary = gr.Markdown(value=initial_usage)
        requests = gr.Dataframe(
            headers=[
                "Статус",
                "Модель",
                "Reasoning",
                "Вход",
                "Кэш",
                "Выход",
                "Рассуждения",
                "Всего",
                "Секунды",
            ],
            value=initial_requests,
            interactive=False,
            label="Последние 20 запросов и текущие",
        )
        timer = gr.Timer(value=1)
        timer.tick(fn=usage_view, outputs=[summary, requests], queue=False)
