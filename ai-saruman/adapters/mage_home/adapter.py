"""Mage Home chat adapter. Planning and block actions belong to the Mage Home modules."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4


TERMINAL = {"READY", "STOPPED", "BLOCKED", "FAILED", "IDLE"}
JOURNAL_LIMIT = 12 * 1024 * 1024
RECORDS = {
    "attempt": ("attempt", "Попытка в Minecraft"),
    "route": ("route", "Маршрут"),
    "expert_decision": ("expert", "Решение эксперта"),
    "cleanup_obligation": ("cleanup", "Уборка временных блоков"),
    "restoration_obligation": ("cleanup", "Восстановление блока"),
    "reconciled_intent": ("attempt", "Сверка прерванного действия"),
    "design_revision": ("design", "Принята новая версия чертежа"),
    "painting": ("decoration", "Установка картины"),
}
COORDINATES = re.compile(r"\bX\s*[:=]?\s*(-?\d+)\s*[,; ]+Y\s*[:=]?\s*(-?\d+)\s*[,; ]+Z\s*[:=]?\s*(-?\d+)", re.I)
START = re.compile(r"^(?:построй(?:те)?|строить|build)\s+", re.I)
FOOTPRINT = re.compile(r"(?<![\d-])(\d{1,3})\s*[xх×]\s*(\d{1,3})(?![\d-])", re.I)
FLOORS = re.compile(r"\b(\d{1,2})(?:\s*-?\s*(?:х|x|ти))?\s*[- ]?\s*(?:этаж|storey|stor(?:ie|y)|floor)", re.I)
RESUME_PROJECT = re.compile(r"(?:дострой дом|продолжи стройку)\s+((?:prj-[0-9a-f-]{36}|saruman-[0-9a-f]{64}))", re.I)
CONTROLS = {"статус": "status", "как идёт стройка": "status", "status": "status",
            "пауза": "pause", "поставь стройку на паузу": "pause", "pause": "pause",
            "продолжи": "resume", "продолжи стройку": "resume", "дострой дом": "resume", "resume": "resume",
            "стоп": "stop", "останови": "stop", "останови стройку": "stop", "stop": "stop"}


@contextmanager
def open_journal(path):
    if os.name != "nt":
        with path.open("rb") as stream:
            yield stream
        return

    # Java replaces the published name. Windows can reject that replacement
    # even with FILE_SHARE_DELETE when a reader opens that very name.
    # A hard link pins the old immutable snapshot under a different name.
    snapshot = path.with_name(f".lab-read-{uuid4().hex}.tmp")
    os.link(path, snapshot)
    try:
        with open_shared(snapshot) as stream:
            yield stream
    finally:
        snapshot.unlink(missing_ok=True)


def open_shared(path):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create_file.restype = wintypes.HANDLE
    # GENERIC_READ with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE.
    # Java publishes a replacement journal while Lab may still read the old snapshot.
    handle = create_file(str(path), 0x80000000, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


class GameError(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


class MageHome:
    def __init__(self, context):
        self.context = context
        scripts = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        self.game_python = context.project / "integrations/saruman/.venv" / scripts
        self.observed_run = None
        self.last_status = None
        self.status_finished = False
        self.background_busy = False
        self.owner_source = None
        self.scheduler_paused = None
        self.file_versions = {}
        self.observed_records = set()
        self.design_check = (0, None)

    def model_module_path(self):
        source = self.context.project / "integrations/mage-home/python"
        if (source / "mage_home/__main__.py").is_file():
            return str(source.resolve())
        wheels = list(self.context.directory.glob("mage_home-*-py3-none-any.whl"))
        if len(wheels) == 1:
            return str(wheels[0].resolve())
        raise ValueError("Не найден пакет проектировщика Mage Home. Проверьте исходники в integrations/mage-home/python или установите один wheel в папку адаптера.")

    def call(self, op, payload=None, properties=None):
        if not self.game_python.is_file():
            raise GameError("bridge_setup", "Подготовьте Python-окружение игрового runtime Saruman.")
        try:
            proc = subprocess.run(
                [str(self.game_python), "-X", "utf8", str(self.context.directory / "bridge.py")],
                input=json.dumps({"op": op, "payload": payload or {}, "properties": properties or {}},
                                 ensure_ascii=False), capture_output=True, encoding="utf-8", timeout=9,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "PYTHONPATH": str(self.context.project / "integrations/saruman/src")},
            )
        except subprocess.TimeoutExpired as error:
            raise GameError("control_timeout", "Minecraft не подтвердил результат запроса вовремя.") from error
        try:
            reply = json.loads(proc.stdout)
        except ValueError as error:
            raise GameError("bridge_unavailable", "Мост UnionClef не вернул ответ. Проверьте клиент Minecraft.") from error
        if not reply.get("ok"):
            error = reply.get("error", {})
            raise GameError(error.get("code"), error.get("message", "Minecraft отклонил запрос"))
        if reply.get("api") != "mage_home.api/1" or reply.get("op") != op:
            raise GameError("bridge_unavailable", "Minecraft вернул несовместимый ответ Mage Home.")
        return reply["result"]

    @staticmethod
    def chat_action(text):
        normalized = text.strip().rstrip(".!? ").lower()
        if normalized == "повтори запуск":
            return "retry_start"
        if normalized in {"покажи мои дома", "список домов"}:
            return "objects"
        if RESUME_PROJECT.fullmatch(normalized):
            return "resume"
        if normalized in CONTROLS:
            return CONTROLS[normalized]
        if START.match(text.strip()):
            return "build"

    def handle(self, text, request_id):
        return self.handle_from(text, None, request_id, "operator")

    def handle_from(self, text, action, request_id, source):
        action = action or self.chat_action(text)
        return self.handle_routed(text, action, request_id, source=source) if action else self.prompt_for_build()

    @staticmethod
    def prompt_for_build():
        return {"status": "needs_input", "detail": "Опишите дом, например «Построй тестовый дом». Участок подбирается автоматически. Для точного места укажите угол пола в формате X 4, Y -60, Z 5."}

    def handle_routed(self, text, action, request_id, *, source="operator"):
        if action == "objects":
            rows = self.objects()[:10]
            lines = [f"{item['id']} · {item['status']} · {item['prompt'] or 'Сохранённый дом'}" for item in rows]
            return {"status": "answered", "detail": "\n".join(lines) or "Сохранённых домов пока нет."}
        if action in {"pause", "resume", "stop"}:
            self.scheduler_paused = None
        if action == "retry_start":
            state = self.context.state()
            if state.get("pending"):
                if state.get("properties"):
                    original_id = state["pending"]["requestId"].removeprefix("saruman-")
                    self.profile("design", original_id)
                    self.profile("expert", original_id)
                    state["model_request_id"] = original_id
                    self.context.save_state(state)
                return self.describe(self.send_start(state))
            return {"status": "answered", "detail": "Нет запуска без подтверждения."}
        if action in {"status", "pause", "resume", "stop"}:
            if action == "status" and self.context.state().get("pending"):
                return {"status": "unknown", "detail": "Ответ на запуск потерян. «Повтори запуск» повторит тот же запрос с тем же ID."}
            if action == "resume":
                target = RESUME_PROJECT.fullmatch(text.strip().rstrip(".!? ").lower())
                return self.resume_project(target[1] if target else None)
            result = self.stop(current_world=True) if action == "stop" else self.control(action)
            if action != "status":
                self.background_busy = result["state"] not in TERMINAL and result["state"] != "PAUSED"
            return self.describe(result)
        if action != "build":
            raise ValueError("Неизвестное действие Mage Home.")
        prompt = text.strip()
        text = START.sub("", prompt, count=1)
        coordinates = list(COORDINATES.finditer(text))
        if len(coordinates) > 1 or not coordinates and re.search(r"\b[XYZ]\s*[:=]?\s*-?\d", FOOTPRINT.sub("", text), re.I):
            return {"status": "needs_input", "detail": "Укажите один полный набор X, Y, Z или уберите координаты для автоматического поиска."}
        description = COORDINATES.sub("", text).strip(" .,:;")
        if not description:
            return {"status": "needs_input", "detail": "Добавьте описание дома."}
        anchor = dict(zip(("x", "y", "z"), map(int, coordinates[0].groups()))) if coordinates else None
        if anchor and not (-30_000_000 < anchor["x"] < 30_000_000 and -30_000_000 < anchor["z"] < 30_000_000
                           and -64 <= anchor["y"] <= 319):
            raise ValueError("Координаты участка вне границ Minecraft.")
        return self.start(description, anchor, request_id, prompt=prompt, switch=source == "operator")

    def profile(self, agent, request_id, *, continuous=True):
        self.context.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.context.data_dir / (sha256(request_id.encode()).hexdigest() + "-" + agent + ".json")
        value = {"schema": "mage_home.profile/1", "provider": "openai_compatible",
                 "endpoint": self.context.model_endpoint(agent, request_id), "allowLoopbackHttp": True,
                 "model": agent, "deadlineMillis": 0 if continuous or agent == "design" else 55_000,
                 "jsonResponseFormat": False}
        if agent == "design":
            value["designJournalDirectory"] = "design-history"
        draft = path.with_suffix(".tmp")
        draft.write_text(json.dumps(value), encoding="utf-8")
        draft.replace(path)
        return str(path.resolve())

    def start(self, description, anchor, request_id, *, prompt=None, switch=True):
        current = self.context.state()
        if current.get("pending"):
            raise RuntimeError("Предыдущий запрос остался без подтверждения. Проверьте «Статус» или отправьте «Стоп».")
        caps = self.call("capabilities")
        world = self.bound_world(caps)
        if not world:
            if caps.get("ui", {}).get("playerPresent"):
                raise ValueError("Minecraft вошёл в мир, но Mage Home не опознал сохранение. Проверьте версию JAR.")
            raise ValueError("Войдите в мир Minecraft, где нужно начать стройку.")
        if caps.get("engine") != "production-v5" or not caps.get("unionClef", {}).get("taskChain"):
            raise ValueError("Mage Home v5 или UnionClef ещё не готов к строительству.")

        known = description.lower() in {"тестовый дом", "тестовый дом у", "test house"}
        blueprint = json.loads((self.context.directory / "test-house.json").read_text(encoding="utf-8")) if known else None
        size = FOOTPRINT.search(description)
        width, depth = (blueprint["size"]["x"], blueprint["size"]["z"]) if known else (
            tuple(map(int, size.groups())) if size else (13, 13))
        site_bounds = None
        plots = self.occupied_plots(caps.get("gameDir"), world["worldUid"])
        if anchor is None:
            floors = FLOORS.search(description)
            height = blueprint["size"]["y"] if known else 4 * int(floors[1]) + 1 if floors else 8
            site = self.call("find_site", {"width": width, "depth": depth, "height": height,
                                           "occupied": plots})
            anchor = site["anchor"]
            site_bounds = {"width": width, "depth": depth}
            self.context.record("adapter.minecraft.site", request_id=request_id, status="selected",
                                details={"text": f"Выбран угол пола X {anchor['x']}, Y {anchor['y']}, Z {anchor['z']}.",
                                         "anchor": anchor, "surveyed_width": width, "surveyed_depth": depth,
                                         "preparation": site.get("survey", {})})
        elif caps.get("multiProject"):
            x, z = anchor["x"], anchor["z"]
            for xmin, zmin, xmax, zmax in (tuple(map(int, plot.split(","))) for plot in plots.split(";") if plot):
                if x <= xmax and x + width - 1 >= xmin and z <= zmax and z + depth - 1 >= zmin:
                    raise ValueError("Участок пересекается с сохранённым домом. Выберите другой участок или укажите ID дома для продолжения.")
        context = {"world": world, "anchor": anchor,
                   "capabilities": {"canBreak": True, "canPlace": True, "canUseItems": True,
                                    "canUseCommands": True, "supportsScaffolding": True}}
        if "resources" in caps:
            context["resources"] = caps["resources"]
        if site_bounds and not known:
            context["siteBounds"] = site_bounds
        continuous = not known and caps.get("recoveryProtocol") == 1
        options = {"mode": "blueprint_only" if known else "text_to_blueprint", "allowCommands": True,
                   "continuousRecovery": continuous,
                   "expert": {"enabled": not known}}
        properties = None
        request = {"requestId": "saruman-" + request_id, "context": context, "options": options}
        if known:
            request["blueprint"] = blueprint
            request["blueprint"]["projectId"] = "saruman-" + sha256(request_id.encode()).hexdigest()
        else:
            request["description"] = description
            properties = {"python": sys.executable, "modulePath": self.model_module_path(),
                          "designProfile": self.profile("design", request_id)}
            options["designProfile"] = properties["designProfile"]
            options["expert"].update(providerProfile=self.profile("expert", request_id, continuous=continuous),
                                     deadlineMillis=0 if continuous else 55_000)

        if switch and caps.get("multiProject"):
            current = self.park_current(current, world["worldUid"])
        state = {**current, "pending": request, "properties": properties, "previous": current,
                 "minecraft_game_dir": caps.get("gameDir"), "world_uid": world["worldUid"],
                 "trace_id": request_id,
                 "submission": {"prompt": prompt or description, "created_at": time.time(), "anchor": anchor,
                                "world_name": caps["world"].get("name") or world.get("server"),
                                "plot": {"width": width, "depth": depth}},
                 "model_request_id": None if known else request_id}
        self.context.save_state(state)
        return self.describe(self.send_start(state))

    def send_start(self, state):
        try:
            result = self.call("start", state["pending"], state.get("properties"))
        except GameError as error:
            if error.code not in {"control_timeout", "bridge_unavailable"}:
                self.context.save_state(state.get("previous") or {"minecraft_game_dir": state.get("minecraft_game_dir")})
            raise
        receipt = {
            **state.get("submission", {}),
            "run_id": result["runId"], "model_request_id": state["model_request_id"],
            "project_id": result.get("projectId"),
            "continuous_recovery": state["pending"].get("options", {}).get("continuousRecovery", False),
            "trace_id": state.get("trace_id") or state["pending"]["requestId"].removeprefix("saruman-"),
            "minecraft_game_dir": state.get("minecraft_game_dir"),
            "world_uid": result.get("worldUid") or state["pending"]["context"]["world"].get("worldUid"),
            "native_state": result["state"],
            "original_request": state["pending"],
        }
        self.save_selected(state, receipt)
        self.background_busy = result["state"] not in TERMINAL and (result["state"] != "PAUSED" or result.get("controlReleased") is False)
        return {**result, "anchor": state["pending"]["context"]["anchor"]}

    @staticmethod
    def projects(state):
        projects = state.get("projects", {}).copy()
        for receipt in state.get("world_runs", {}).values():
            if receipt.get("project_id"):
                projects.setdefault(receipt["project_id"], receipt)
        return projects

    def save_selected(self, state, receipt):
        projects = self.projects(state)
        if receipt.get("project_id"):
            projects[receipt["project_id"]] = receipt
        world_runs = state.get("world_runs", {}).copy()
        if receipt.get("world_uid"):
            world_runs[receipt["world_uid"]] = receipt
        self.context.save_state({**receipt, "projects": projects, "world_runs": world_runs})

    def park_current(self, state, world_uid, except_run=None):
        try:
            current = self.call("status", {"runId": ""})
        except GameError as error:
            if error.code == "unknown_run":
                return state
            raise
        run_id = current.get("runId")
        if not run_id or run_id == except_run:
            return state
        if current.get("worldUid") != world_uid:
            raise RuntimeError("Мир изменился перед сменой стройки.")
        snapshot = current
        if current["state"] not in TERMINAL and not (
                current["state"] == "PAUSED" and current.get("controlReleased")
                and str(current.get("waitingReason")).startswith("design_")):
            paused = self.call("pause", {"runId": run_id})
            if not paused.get("controlReleased"):
                raise RuntimeError("Ожидается освобождение управления прежней стройкой.")
            snapshot = {**current, **paused, "state": "PAUSED", "waitingReason": "pause_requested"}
        project_id = current.get("projectId")
        self.remember_status(state, project_id, snapshot)
        receipt = self.projects(state).get(project_id) or state
        if receipt.get("run_id") == run_id and receipt.get("trace_id"):
            reply = self.describe(snapshot)
            self.context.record("adapter.minecraft.state", request_id=receipt["trace_id"], status=reply["status"],
                                details={"text": reply["detail"], "state": snapshot},
                                key=f"mage-home:{run_id}:parked:{self.fingerprint(snapshot)}")
        self.scheduler_paused = None
        self.background_busy = False
        return self.context.state()

    def remember_status(self, state, project_id, result):
        projects = self.projects(state)
        receipt = projects.get(project_id)
        if not receipt:
            return
        updated = {**receipt, "native_state": result["state"], "waiting_reason": result.get("waitingReason")}
        if receipt == updated:
            return
        projects[project_id] = updated
        world_runs = state.get("world_runs", {}).copy()
        uid = receipt.get("world_uid")
        if world_runs.get(uid, {}).get("run_id") == receipt["run_id"]:
            world_runs[uid] = updated
        top = {"native_state": updated["native_state"], "waiting_reason": updated["waiting_reason"]} if state.get("run_id") == receipt["run_id"] else {}
        self.context.save_state({**state, **top, "projects": projects, "world_runs": world_runs})

    @staticmethod
    def bound_world(caps):
        world = caps.get("world", {})
        if not world.get("connected") or not world.get("dimension"):
            return None
        uid = world.get("worldUid")
        if not uid and world.get("server"):
            uid = world["server"] + "|" + world["dimension"]
        return {"server": world.get("server"), "dimension": world["dimension"], "worldUid": uid} if uid else None

    def current_status(self, state):
        try:
            return self.call("status", {"runId": ""})
        except GameError as error:
            if error.code != "unknown_run":
                raise
        caps = self.call("capabilities")
        world = self.bound_world(caps)
        if not world:
            return {"state": "IDLE", "waitingReason": "Откройте мир Minecraft, чтобы запросить его сохранённый прогресс"}
        game_dir = caps.get("gameDir") or state.get("minecraft_game_dir")
        if game_dir:
            saved = self.saved_status(game_dir, world["worldUid"])
            if saved:
                return saved
        return {"state": "IDLE"}

    def control(self, op):
        state = self.context.state()
        if state.get("pending"):
            raise RuntimeError("Запуск остался без подтверждения. Откройте «Статус» для информации.")
        current = self.current_status(state)
        if op == "resume" and current.get("archived"):
            restored = self.restore(current.get("worldUid"), state)
            if restored is not None:
                return restored
        if op == "resume" and current["state"] == "IDLE":
            world = self.bound_world(self.call("capabilities"))
            if world:
                restored = self.restore(world["worldUid"], state)
                if restored is not None:
                    return restored
        if op == "status" or current.get("archived") or not current.get("runId"):
            return current
        if op == "resume":
            receipt = state.get("world_runs", {}).get(current.get("worldUid"))
            if receipt:
                # Admit model callbacks for the resumed run before Minecraft can ask for them.
                self.context.save_state({**state, **receipt})
                callback = receipt.get("model_request_id")
                if callback:
                    self.profile("design", callback)
                    self.profile("expert", callback, continuous=receipt.get("continuous_recovery", False))
            try:
                return self.call(op, {"runId": current["runId"]})
            except GameError:
                if receipt:
                    self.context.save_state(state)
                raise
        result = self.call(op, {"runId": current["runId"]})
        if op == "pause":
            result = {**current, **result, "waitingReason": "pause_requested"}
            self.remember_status(state, current.get("projectId"), result)
        return result

    def resume_project(self, project_id):
        state = self.context.state()
        if state.get("pending"):
            raise RuntimeError("Запуск остался без подтверждения. Проверьте статус прежнего запроса.")
        projects = self.projects(state)
        if not projects and project_id is None:
            return self.describe(self.control("resume"))
        caps = self.call("capabilities")
        world = self.bound_world(caps)
        if not world:
            raise ValueError("Откройте мир выбранной стройки.")
        world_uid = world["worldUid"]
        if project_id is None:
            candidates = [item for item in projects.values() if item.get("world_uid") == world_uid
                          and item.get("native_state") not in TERMINAL]
            if len(candidates) != 1:
                detail = ("Выберите дом в «Моды и адаптеры → Mage Home → Объекты» или укажите его ID."
                          if candidates else "В открытом мире нет незавершённых домов.")
                return {"status": "needs_input", "detail": detail}
            receipt = candidates[0]
        else:
            receipt = projects.get(project_id)
            if not receipt:
                raise ValueError("Дом с таким ID не найден.")
            if receipt.get("world_uid") != world_uid:
                raise ValueError("Откройте мир выбранной стройки: " + (receipt.get("world_name") or receipt["world_uid"]))
        run_id = receipt["run_id"]
        try:
            current = self.call("status", {"runId": run_id})
        except GameError as error:
            if error.code != "unknown_run":
                raise
            if receipt.get("native_state") in TERMINAL:
                raise ValueError("У этого дома нет приостановленного запуска.")
            if caps.get("multiProject"):
                state = self.park_current(state, world_uid, run_id)
            result = self.restore(world_uid, state, receipt=receipt, caps=caps)
            if result is None:
                raise ValueError("Для этого дома нет сохранённого запуска, который можно продолжить.")
        else:
            if current["state"] in TERMINAL:
                raise ValueError("У этого дома нет приостановленного запуска.")
            if current["state"] != "PAUSED":
                self.save_selected(state, {**receipt, "native_state": current["state"], "waiting_reason": current.get("waitingReason")})
                return self.describe(current)
            callback = receipt.get("model_request_id")
            if callback:
                self.profile("design", callback)
                self.profile("expert", callback, continuous=receipt.get("continuous_recovery", False))
            if caps.get("multiProject"):
                state = self.park_current(state, world_uid, run_id)
            self.save_selected(state, receipt)
            try:
                result = {**current, **self.call("resume", {"runId": run_id}), "waitingReason": None}
            except GameError:
                self.context.save_state(state)
                raise
            self.save_selected(self.context.state(), {**receipt, "native_state": result["state"], "waiting_reason": None})
        self.background_busy = result["state"] not in TERMINAL and result["state"] != "PAUSED"
        self.status_finished = False
        return self.describe({**result, "runId": run_id, "projectId": receipt["project_id"], "worldUid": world_uid})

    def recovery_checkpoint(self, receipt):
        original = receipt.get("original_request")
        project = receipt.get("project_id")
        if not original or not project:
            return None
        context = original["context"]
        anchor = context["anchor"]
        coords = " ".join(str(anchor[key]) for key in ("x", "y", "z"))
        binding = f"{receipt['world_uid']}|{project}|{coords}|revision-history/1"
        if not receipt.get("minecraft_game_dir"):
            return {}
        file = Path(receipt["minecraft_game_dir"]) / "config/mage_home/build-journals" / (sha256(binding.encode()).hexdigest() + ".json")
        try:
            with open_journal(file) as stream:
                snapshot = json.loads(stream.read(JOURNAL_LIMIT + 1))
        except FileNotFoundError:
            return {}
        if (snapshot.get("binding") != binding or snapshot.get("runId") != receipt["run_id"]
                or snapshot.get("worldUid") != receipt["world_uid"]):
            raise ValueError("Сохранение стройки не совпадает с выбранным миром и запуском.")
        return snapshot

    def restore(self, world_uid, state, *, receipt=None, caps=None):
        receipt = receipt or state.get("world_runs", {}).get(world_uid) or {}
        saved = self.recovery_checkpoint(receipt)
        if saved is None or saved.get("lastState") in TERMINAL:
            return None
        callback = receipt.get("model_request_id")
        original = receipt["original_request"]
        if not callback and not original.get("blueprint"):
            return None
        caps = caps or self.call("capabilities")
        if caps.get("recoveryProtocol") != 1 or (self.bound_world(caps) or {}).get("worldUid") != world_uid:
            raise ValueError("Откройте сохранённый мир в клиенте с поддержкой восстановления Mage Home.")
        options = {**original["options"], "restoreRunId": receipt["run_id"], "restoreProjectId": receipt["project_id"]}
        properties = None
        if callback:
            design = self.profile("design", callback)
            options["designProfile"] = design
            options["expert"] = {**options["expert"], "providerProfile": self.profile("expert", callback)}
            properties = {"python": sys.executable, "modulePath": self.model_module_path(), "designProfile": design}
        request = {**original, "options": options}
        if saved.get("blueprint"):
            request["blueprint"] = saved["blueprint"]
        self.save_selected(state, receipt)
        try:
            result = self.call("start", request, properties)
        except GameError:
            self.context.save_state(state)
            raise
        if result.get("runId") != receipt["run_id"]:
            raise RuntimeError("Клиент вернул другой идентификатор сохранённой стройки.")
        self.save_selected(self.context.state(), {**receipt, "native_state": result["state"], "waiting_reason": None})
        self.status_finished = False
        self.last_status = None
        return result

    def observe(self, _enabled, state):
        """Project the client ledger and observed state into Lab without issuing actions."""
        run_id, trace = state.get("run_id"), state.get("trace_id")
        if not run_id or not trace:
            return
        if self.observed_run != run_id:
            self.observed_run = run_id
            self.last_status = None
            self.status_finished = False
            self.file_versions.clear()
            self.observed_records.clear()
            self.design_check = (0, None)
        try:
            self.observe_journal(state)
        except (OSError, ValueError) as error:
            detail = f"Не удалось прочитать журнал стройки: {error}"
            self.context.record("adapter.minecraft.journal", request_id=trace, status="error",
                                details={"text": detail}, key=f"mage-home:{run_id}:journal:{self.fingerprint(detail)}")
        if self.status_finished:
            return
        try:
            result = self.call("status", {"runId": run_id})
        except GameError as error:
            self.background_busy = False
            if error.code == "unknown_run":
                self.scheduler_paused = None
                saved = self.recovery_checkpoint(state)
                if saved is not None and saved.get("lastState") not in TERMINAL and state.get("model_request_id"):
                    result = {"state": "PAUSED", "runId": run_id, "worldUid": state.get("world_uid"),
                              "controlReleased": True, "waitingReason": "recovery_saved_after_restart"}
                    self.context.record("adapter.minecraft.state", request_id=trace, status="paused",
                                        details={"text": self.describe(result)["detail"], "state": result},
                                        key=f"mage-home:{run_id}:restorable")
                    return
                self.context.record("adapter.minecraft.state", request_id=trace, status="unavailable",
                                    details={"text": "Клиент больше не сообщает состояние этого запуска.",
                                             "run_id": run_id}, key=f"mage-home:{run_id}:unavailable")
                self.status_finished = True
                return
            raise
        if result.get("waitingReason") == "design_pending" and self.design_check[1] == "rejected":
            result = {**result, "waitingReason": "design_revising"}
        self.background_busy = result["state"] not in TERMINAL and result["state"] != "PAUSED"
        self.remember_status(state, state.get("project_id"), result)
        projection = {key: value for key, value in result.items() if key not in {"clientTick", "updatedAtTick"}}
        if projection != self.last_status:
            reply = self.describe(result)
            added = self.context.record("adapter.minecraft.state", request_id=trace, status=reply["status"],
                                        details={"text": reply["detail"], "state": result},
                                        key=f"mage-home:{run_id}:state:{self.fingerprint(result)}")
            if added:
                self.last_status = projection
        self.status_finished = result.get("state") in TERMINAL and projection == self.last_status
        if self.status_finished and result["state"] == "READY" and state.get("project_id"):
            self.capture_completed(state["project_id"], run_id, state["world_uid"])

    def capture_completed(self, project_id, run_id, world_uid):
        def eligible():
            state = self.context.state()
            receipt = self.projects(state).get(project_id, {})
            if state.get("pending") or state.get("run_id") != run_id or receipt.get("photo"):
                return False
            current = self.call("status", {"runId": ""})
            return (current.get("runId"), current.get("worldUid"), current.get("state")) == (run_id, world_uid, "READY")

        def save(photo):
            state = self.context.state()
            receipt = self.projects(state)[project_id]
            self.save_selected(state, {**receipt, "photo": photo["sha256"]})

        key = sha256(("mage-home-photo:" + project_id).encode()).hexdigest()
        self.context.capture_object(key, eligible, save,
                                    focus=lambda: self.photo_subject(run_id, world_uid))

    def photo_subject(self, run_id, world_uid):
        receipt = next((item for item in self.projects(self.context.state()).values()
                        if item.get("run_id") == run_id and item.get("world_uid") == world_uid), None)
        saved = self.recovery_checkpoint(receipt) if receipt else None
        if not saved or saved.get("lastState") != "READY":
            raise ValueError("Снимок дома ещё недоступен: нет подтверждённого сохранения этой стройки.")
        blueprint = saved["blueprint"]
        cells = blueprint["cells"]
        if not cells:
            raise ValueError("Снимок дома недоступен: в подтверждённой схеме нет блоков.")
        origin = blueprint.get("origin", {})
        offset = {axis: saved["anchor"][axis] + origin.get(axis, 0) for axis in ("x", "y", "z")}
        return {
            "run_id": run_id, "project_id": receipt["project_id"], "world_uid": world_uid,
            "min": [offset[axis] + min(cell[axis] for cell in cells) for axis in ("x", "y", "z")],
            "max": [offset[axis] + max(cell[axis] for cell in cells) for axis in ("x", "y", "z")],
            "facing": blueprint["entrance"]["facing"],
        }

    def yield_control(self):
        state = self.context.state()
        run = state.get("run_id")
        if not run:
            return
        result = self.call("status", {"runId": run})
        if result["state"] in TERMINAL:
            self.background_busy = False
            self.scheduler_paused = None
            return
        if result["state"] == "PAUSED" and not self.scheduler_paused:
            self.background_busy = result.get("controlReleased") is False
            return
        if result["state"] != "PAUSED":
            self.scheduler_paused = run
        result = self.call("pause", {"runId": run})
        self.background_busy = not result.get("controlReleased", False)
        if not self.background_busy and self.scheduler_paused == run:
            self.context.record("adapter.control", request_id=state.get("trace_id"), status="paused",
                                details={"text": "Стройка уступила управление приоритетной задаче.", "run_id": run})

    def reclaim_control(self):
        run = self.scheduler_paused
        if run and not self.background_busy and self.context.state().get("run_id") == run:
            result = self.call("resume", {"runId": run})
            self.background_busy = result["state"] not in TERMINAL and result["state"] != "PAUSED"
            self.scheduler_paused = None
            self.context.record("adapter.control", request_id=self.context.state().get("trace_id"),
                                status="running", details={"text": "Стройка продолжена с сохранённого прогресса.", "run_id": run})

    @staticmethod
    def fingerprint(value):
        return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def journals(self, game_dir, versions=None):
        game = Path(game_dir).resolve()
        folder = (game / "config/mage_home/build-journals").resolve()
        if not folder.is_relative_to(game):
            raise ValueError("Каталог журнала Mage Home выходит за пределы профиля Minecraft")
        for file in folder.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", file.name) or not file.resolve().is_relative_to(folder):
                continue
            try:
                info = file.stat()
                version = (info.st_mtime_ns, info.st_size)
                if (versions is not None and versions.get(file) == version) or info.st_size > JOURNAL_LIMIT:
                    continue
                with open_journal(file) as source:
                    raw = source.read(JOURNAL_LIMIT + 1)
                if len(raw) > JOURNAL_LIMIT:
                    continue
                snapshot = json.loads(raw)
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                continue
            if not isinstance(snapshot, dict) or snapshot.get("schema") != "mage_home.attempts/1":
                continue
            yield file, version, snapshot

    def saved_status(self, game_dir, world_uid):
        latest = max(
            ((version[0], data) for _, version, data in self.journals(game_dir) if data.get("worldUid") == world_uid),
            key=lambda item: item[0], default=None,
        )
        if latest is None:
            return None
        data = latest[1]
        return {"archived": True, "state": data.get("lastState"), "runId": data.get("runId"),
                "projectId": data.get("projectId"), "worldUid": world_uid, "anchor": data.get("anchor"),
                "progress": data.get("progress") or {}, "waitingReason": data.get("reason")}

    def occupied_plots(self, game_dir, world_uid):
        plots = set()
        for _, _, data in self.journals(game_dir) if game_dir else ():
            if data.get("worldUid") != world_uid:
                continue
            progress = data.get("progress") or {}
            if data.get("lastState") != "READY" and not (progress.get("confirmedCells") or data.get("confirmedFill")):
                continue
            blueprint = data.get("blueprint") or {}
            anchor, size, origin = data.get("anchor") or {}, blueprint.get("size") or {}, blueprint.get("origin") or {}
            try:
                x, z = anchor["x"] + origin.get("x", 0), anchor["z"] + origin.get("z", 0)
                width, depth = size["x"], size["z"]
                if all(type(value) is int for value in (x, z, width, depth)) and width > 0 and depth > 0:
                    plots.add((x, z, x + width - 1, z + depth - 1))
            except (TypeError, KeyError):
                continue
        for item in self.projects(self.context.state()).values():
            if item.get("world_uid") != world_uid or item.get("native_state") in TERMINAL:
                continue
            original = (item.get("original_request") or {}).get("context", {})
            anchor = item.get("anchor") or original.get("anchor")
            plot = item.get("plot") or original.get("siteBounds") or {"width": 13, "depth": 13}
            if anchor and plot:
                x, z = anchor["x"], anchor["z"]
                plots.add((x, z, x + plot["width"] - 1, z + plot["depth"] - 1))
        return ";".join(",".join(map(str, plot)) for plot in sorted(plots))

    def objects(self):
        state = self.context.state()
        projects = self.projects(state)
        directories = {item.get("minecraft_game_dir") for item in projects.values()}
        directories.add(state.get("minecraft_game_dir"))
        checkpoints = {}
        for directory in directories - {None, ""}:
            for _, version, data in self.journals(directory):
                project = data.get("projectId")
                if project and (project not in checkpoints or checkpoints[project][0] < version[0]):
                    checkpoints[project] = (version[0], data)
        rows = []
        for project_id in projects.keys() | checkpoints.keys():
            record = projects.get(project_id, {})
            written, saved = checkpoints.get(project_id, (0, {}))
            blueprint = saved.get("blueprint") or {}
            original = record.get("original_request") or {}
            ctx = original.get("context", {})
            phase = saved.get("lastState") if saved.get("lastState") in TERMINAL else record.get("native_state") or saved.get("lastState")
            reason = record.get("waiting_reason") or saved.get("reason")
            if phase in {None, "IDLE"} and record:
                phase = "PAUSED"
            rows.append({"id": project_id, "run_id": record.get("run_id") or saved.get("runId"),
                         "prompt": record.get("prompt") or original.get("description") or blueprint.get("description"),
                         "created_at": record.get("created_at"), "updated_at": written / 1e9 or None,
                         "world_uid": record.get("world_uid") or saved.get("worldUid"),
                         "world_name": record.get("world_name") or ctx.get("world", {}).get("server"),
                         "anchor": record.get("anchor") or saved.get("anchor") or ctx.get("anchor"),
                         "status": phase, "reason": reason, "photo": record.get("photo"),
                         "can_resume": bool(record and phase == "PAUSED")})
        return sorted(rows, key=lambda item: item["created_at"] or item["updated_at"] or 0, reverse=True)

    def observe_journal(self, state):
        project = state.get("project_id")
        if project:
            folder = self.context.data_dir / "design-history" / sha256(project.encode()).hexdigest()
            self.observe_archive(folder, state, design=True)
        if not state.get("minecraft_game_dir"):
            return
        root = Path(state["minecraft_game_dir"]).resolve() / "config/mage_home/build-journals"
        for folder in root.glob("*.events"):
            if re.fullmatch(r"[0-9a-f]{64}\.events", folder.name) and folder.resolve().parent == root.resolve():
                self.observe_archive(folder, state)
        for file, version, snapshot in self.journals(state["minecraft_game_dir"], self.file_versions):
            complete = True
            records = snapshot.get("records", [])
            if not isinstance(records, list):
                raise ValueError(f"Повреждён список попыток в {file.name}.")
            for record in records:
                if isinstance(record, dict) and record.get("runId") == state["run_id"]:
                    complete = self.observe_record(state, record) and complete
            if complete:
                self.file_versions[file] = version

    def observe_archive(self, folder, state, *, design=False):
        try:
            change = folder.stat().st_mtime_ns
            if self.file_versions.get(folder) == change:
                return
            all_saved = True
            for file in folder.glob("*.json"):
                if file in self.file_versions or not re.fullmatch(r"[0-9]{19,20}-[0-9a-f-]{32,36}\.json", file.name):
                    continue
                if file.resolve().parent != folder.resolve() or file.stat().st_size > JOURNAL_LIMIT:
                    continue
                data = json.loads(file.read_bytes())
                saved = True
                for record in data if isinstance(data, list) else [data]:
                    if not isinstance(record, dict) or record.get("runId") != state["run_id"]:
                        continue
                    if design:
                        if record.get("schema") != "mage_home.design_attempt/1":
                            continue
                        record = {**record, "type": "design_attempt"}
                    saved = self.observe_record(state, record) and saved
                if saved:
                    self.file_versions[file] = True
                all_saved = all_saved and saved
            if all_saved:
                self.file_versions[folder] = change
        except (OSError, ValueError):
            return

    def observe_record(self, state, record):
        digest = self.fingerprint(record)
        if digest in self.observed_records:
            return True
        kind, title = RECORDS.get(record.get("type"), ("record", "Запись журнала Mage Home"))
        if record.get("type") == "design_attempt":
            kind, title = "design", "Проверка чертежа"
            timestamp = record.get("timeNs", 0)
            if isinstance(timestamp, int) and timestamp > self.design_check[0]:
                self.design_check = (timestamp, record.get("outcome"))
        status = ("accepted" if record.get("accepted") else "rejected") if kind == "expert" else (
            record.get("outcome") or record.get("status") or record.get("state_of_obligation") or "info")
        issues = record.get("errors") or []
        reason = record.get("reason") or record.get("note") or record.get("detail") or (issues[0].get("detail") if issues else None)
        summary = " · ".join(filter(None, [title, record.get("method"), reason]))
        added = self.context.record("adapter.minecraft." + kind, request_id=state["trace_id"],
                                    status=status, details={"text": summary, "record": record},
                                    key="mage-home:record:" + digest)
        if added:
            self.observed_records.add(digest)
        return added

    def suspend(self):
        state = self.context.state()
        if state.get("run_id") and not state.get("pending"):
            self.park_current(state, state.get("world_uid"))

    def stop(self, *, current_world=False):
        self.scheduler_paused = None
        state = self.context.state()
        if state.get("pending"):
            state["model_request_id"] = None
            self.context.save_state(state)
            raise RuntimeError("Minecraft не подтвердил идентификатор стройки. Остановка не подтверждена.")
        current = self.current_status(state) if current_world else None
        if current and current.get("archived"):
            return current
        run_id = current.get("runId") if current is not None else state.get("run_id")
        if run_id == state.get("run_id") and state.get("model_request_id"):
            state["model_request_id"] = None
            uid = current.get("worldUid") if current else state.get("world_uid")
            receipt = state.get("world_runs", {}).get(uid)
            if receipt and receipt.get("run_id") == run_id:
                receipt["model_request_id"] = None
            project = state.get("projects", {}).get(state.get("project_id"))
            if project and project.get("run_id") == run_id:
                project["model_request_id"] = None
            self.context.save_state(state)
        if not run_id:
            return {"state": "IDLE"}
        if current is None:
            try:
                current = self.call("status", {"runId": run_id})
            except GameError as error:
                if error.code == "unknown_run":
                    return {"state": "IDLE"}
                raise
        if current["state"] in TERMINAL:
            return current
        result = self.call("stop", {"runId": run_id, "reason": "saruman_lab"})
        self.remember_status(self.context.state(), current.get("projectId") or state.get("project_id"),
                             {**result, "state": "STOPPED", "waitingReason": result.get("cancellationReason")})
        if not result.get("controlReleased"):
            raise RuntimeError("Остановка запрошена. Minecraft ещё не подтвердил освобождение управления.")
        return result

    @staticmethod
    def describe(result):
        phase = result.get("state")
        states = {"IDLE": ("answered", "Нет текущей стройки."), "PLANNING": ("running", "Проектировщик готовит чертёж."),
                  "PREPARING": ("running", "Подготовка участка."), "BUILDING": ("running", "Идёт стройка."),
                  "VERIFYING": ("running", "Сверка дома с чертежом."), "CLEANUP": ("running", "Уборка временных опор."),
                  "EXITING": ("running", "Выход из дома."), "READY": ("completed", "Дом построен и проверен."),
                  "PAUSED": ("paused", "Стройка на паузе."), "STOPPED": ("cancelled", "Стройка остановлена."),
                  "BLOCKED": ("failed", "Строитель не может продолжить."), "FAILED": ("failed", "Стройка завершилась ошибкой.")}
        status, detail = states.get(phase, ("accepted", "Поручение принято."))
        reason = result.get("waitingReason")
        if result.get("archived"):
            status, detail = "saved", f"Сохранённый статус этого мира: {phase}. Сейчас этот запуск не активен."
        elif phase == "PLANNING" and reason == "design_replanning":
            detail, reason = "Проектировщик исправляет проблемную часть дома. Подтверждённые шаги сохранены.", None
        elif phase == "PLANNING" and reason == "design_revising":
            detail, reason = "Проектировщик исправляет чертёж по замечаниям компилятора.", None
        elif phase == "PAUSED" and reason in {"journal_write_failed", "journal_commit_pending", "journal_read_pending"}:
            detail, reason = "Стройка на паузе. Ожидается доступ к журналу стройки.", None
        elif phase == "BLOCKED" and reason in {"journal_read_failed", "journal_write_failed"}:
            detail, reason = "Стройка остановлена из-за ошибки доступа к журналу. Подробности в журнале Minecraft.", None
        elif phase == "PAUSED" and reason in {"design_timeout", "design_provider_timeout", "design_provider_waiting", "design_provider_error", "design_provider_output_limit"}:
            detail, reason = "Стройка ждёт ответа модели. После восстановления доступа продолжите сохранённую задачу.", None
        elif phase == "PAUSED" and reason in {"design_stalled", "design_rejected_blueprint"}:
            detail, reason = "Стройка ждёт решения проблемы в чертеже. Все варианты и результаты проверки сохранены в журнале.", None
        elif phase == "PAUSED" and reason == "recovery_saved_after_restart":
            detail, reason = "Стройка сохранена после перезапуска. Откройте её мир и отправьте «Продолжи стройку».", None
        elif phase == "PAUSED" and reason and reason.startswith("recovery_waiting:"):
            detail, reason = "Стройка ждёт изменения условий. Прогресс и попытки сохранены. Причина: " + reason.removeprefix("recovery_waiting:"), None
        elif phase == "FAILED" and reason in {"design_provider_timeout", "design_timeout"}:
            detail = "Проектировщик не успел получить чертёж в отведённое время. Подготовка участка ещё не началась."
            reason = None
        elif phase == "FAILED" and reason == "design_provider_output_limit":
            detail = "Модель исчерпала лимит токенов до завершения чертежа. Подготовка участка ещё не началась."
            reason = None
        elif phase == "FAILED" and reason in {"design_inaccessible_room", "design_inaccessible_entrance",
                                               "design_inaccessible_furniture", "design_stair_collision"}:
            detail = "План отклонён. В чертеже есть недоступная зона, мебель или непроходимый вход. Подготовка участка ещё не началась."
            reason = None
        elif phase == "FAILED" and reason == "design_materials_unavailable":
            detail = "План отклонён. Материалов для чертежа недостаточно, а команды их получения недоступны."
            reason = None
        anchor = result.get("anchor")
        if isinstance(anchor, dict):
            detail += f" Угол пола: X {anchor.get('x')}, Y {anchor.get('y')}, Z {anchor.get('z')}."
        progress = result.get("progress", {})
        if progress.get("targetCells"):
            detail += f" Подтверждено блоков: {progress.get('confirmedCells', 0)} из {progress['targetCells']}."
        if reason:
            detail += " Состояние: " + reason + "."
        if phase == "PAUSED" and result.get("controlReleased") is False:
            detail += " Ожидается освобождение управления."
        return {"status": status, "detail": detail, "run_id": result.get("runId"),
                "world_uid": result.get("worldUid"), "project_id": result.get("projectId")}


def create(context):
    return MageHome(context)
