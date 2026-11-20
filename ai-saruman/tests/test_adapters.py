from copy import deepcopy
from hashlib import sha256
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import shutil
import subprocess
import sys
import time
from threading import Event, Thread
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from saruman.roles import settings
from saruman.extensions import atomic_json
from saruman.web import Application, make_server
from test_lab import Fixture, PROJECT


CAPS = {"engine": "production-v5", "recoveryProtocol": 1, "world": {"connected": True, "server": "127.0.0.1:25565",
        "dimension": "minecraft:overworld"}, "unionClef": {"taskChain": True}}
ADAPTER = "mage_home"
DESIGN = "adapter:mage_home:design"
EXPERT = "adapter:mage_home:expert"


class AdapterTests(Fixture):
    def setUp(self):
        super().setUp()
        shutil.copytree(PROJECT / "adapters", self.root / "adapters",
                        ignore=shutil.ignore_patterns("__pycache__", "*.whl"))
        package = "integrations/mage-home/python/mage_home"
        shutil.copytree(PROJECT / package, self.root / package,
                        ignore=shutil.ignore_patterns("__pycache__"))
        env = patch.dict(os.environ, {"SARUMAN_ROLE_DB": str(self.root / "roles.sqlite"),
                                     "MINIMAX_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        self.provider = Mock()
        self.app = Application(self.root, provider=self.provider)
        self.server = make_server(self.app, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def request(self, path, payload=None, token=True):
        headers = {"X-Saruman-Token": self.app.token} if token else {}
        url = path if path.startswith("http://") else self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(url, data, headers), timeout=7) as response:
            return json.load(response)

    def toggle(self, enabled):
        return self.request("/api/adapters", {"id": ADAPTER, "enabled": enabled})

    def chat(self, text, identifier):
        return self.request("/api/admin", {"adapter": ADAPTER, "text": text, "request_id": identifier})

    def test_discover_toggle_route_and_cancel_only_owned_run(self):
        overview = self.request("/api/overview")
        self.assertFalse(overview["adapters"]["items"][0]["enabled"])
        self.assertEqual(overview["models"]["roles"][DESIGN]["adapter"], ADAPTER)
        self.assertIn(EXPERT, overview["models"]["profiles"])
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/adapters", {"id": ADAPTER, "enabled": True}, token=False)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        self.assertEqual(self.chat("Статус", "disabled")["status"], "error")

        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        running = {"runId": "run-user", "state": "BUILDING"}
        stopped = {"runId": "run-user", "state": "STOPPED", "controlReleased": True}
        paused = {"runId": "run-user", "state": "PAUSED", "controlReleased": True}
        with patch.object(bridge, "call",
                          side_effect=[CAPS, running, running, paused, paused, running, running, stopped]) as gateway:
            reply = self.chat("Построй тестовый дом. X 4, Y -60, Z 5", "start")
            self.assertEqual(reply["status"], "running")
            self.assertEqual(self.chat(reply["input"], "start")["result"], reply["result"])
            payload = gateway.call_args_list[1].args[1]
            self.assertEqual(len(payload["blueprint"]["cells"]), 180)
            self.assertFalse(payload["options"]["expert"]["enabled"])
            self.assertEqual(self.chat("Пауза", "pause")["status"], "paused")
            self.assertEqual(gateway.call_args.args, ("pause", {"runId": "run-user"}))
            self.assertEqual(self.chat("Продолжи", "resume")["status"], "running")
            self.assertEqual(gateway.call_args.args, ("resume", {"runId": "run-user"}))
            self.assertIsNone(self.toggle(False)["warning"])
            self.assertEqual(gateway.call_args.args, ("stop", {"runId": "run-user", "reason": "saruman_lab"}))
        self.provider.complete.assert_not_called()
        self.assertFalse(self.app.adapters.state(ADAPTER)[0])
        self.assertEqual(self.request("/api/admin")["messages"][0]["adapter"], ADAPTER)

    def test_standalone_house_command_routes_to_mage_home_without_admin_model(self):
        text = "Построй тестовый дом. X 4, Y -60, Z 5"
        self.assertEqual(self.app.adapters.chat_target(text), ADAPTER)
        self.assertEqual(self.app.adapters.chat_target(text + ", затем опубликуй пост"), "")
        self.assertEqual(self.app.adapters.chat_target("Покажи, где стоит дом"), "")
        disabled = self.request("/api/admin", {"text": text, "request_id": "routed-off"})
        self.assertEqual((disabled["adapter"], disabled["status"]), (ADAPTER, "error"))
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        active = {"runId": "run-routed", "state": "PREPARING"}
        with patch.object(bridge, "call", side_effect=[CAPS, active]) as gateway:
            message = self.request("/api/admin", {"text": text, "request_id": "routed-on", "adapter": ""})
            repeated = self.request("/api/admin", {"text": text, "request_id": "routed-on", "adapter": ""})
            self.assertEqual((message["adapter"], message["status"]), (ADAPTER, "running"))
            self.assertEqual(repeated["result"], message["result"])
            self.assertEqual(gateway.call_args.args[0], "start")
            self.assertEqual(gateway.call_count, 2)
        bridge.context.save_state({})
        self.provider.complete.assert_not_called()

    def test_admin_routes_natural_requests_and_preserves_user_coordinates(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        original = "Саруман, мне нужен здесь небольшой еловый дом с печью. X 12, Y 64, Z -7"

        def interpret(_instructions, prompt, _model, _trace, **kwargs):
            request = json.loads(prompt)
            self.assertIn("adapter_task", kwargs["output_schema"]["properties"]["operation"]["enum"])
            self.assertIn("build", request["adapters"][0]["actions"])
            if request["message"] == original:
                return {"operation": "adapter_task", "adapter": ADAPTER, "text": "build", "steps": []}
            self.assertEqual(request["recent_adapter_commands"][0]["adapter"], ADAPTER)
            return {"operation": "adapter_task", "adapter": ADAPTER, "text": "pause", "steps": []}

        self.provider.complete.side_effect = interpret
        preparing = {"runId": "run-routed", "state": "PREPARING"}
        paused = {"runId": "run-routed", "state": "PAUSED", "controlReleased": True}
        with patch.object(bridge, "call", side_effect=[CAPS, preparing, preparing, paused]) as gateway:
            created = self.request("/api/admin", {"text": original, "request_id": "natural"})
            self.assertEqual((created["adapter"], created["status"]), (ADAPTER, "running"))
            payload = gateway.call_args.args[1]
            self.assertEqual(payload["context"]["anchor"], {"x": 12, "y": 64, "z": -7})
            self.assertIn("еловый дом с печью", payload["description"])
            self.assertEqual(self.request("/api/admin", {"text": original, "request_id": "natural"})["result"], created["result"])
            followup = self.request("/api/admin", {"text": "Приостанови пока работы над ним.", "request_id": "pause-natural"})
            self.assertEqual((followup["adapter"], followup["status"]), (ADAPTER, "paused"))
            self.assertEqual(gateway.call_args.args, ("pause", {"runId": "run-routed"}))
        self.assertEqual(self.provider.complete.call_count, 2)
        bridge.context.save_state({})

    def test_model_cannot_supply_build_coordinates_or_an_unknown_adapter_action(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        self.provider.complete.side_effect = [
            {"operation": "adapter_task", "adapter": ADAPTER, "text": "build", "steps": []},
            {"operation": "adapter_task", "adapter": ADAPTER, "text": "build X 12 Y 64 Z 7", "steps": []},
        ]
        with patch.object(bridge, "call") as gateway:
            first = self.request("/api/admin", {"text": "Мне нужен коттедж с камином. X 12", "request_id": "incomplete-coordinates"})
            second = self.request("/api/admin", {"text": "Хочу попробовать другой дом.", "request_id": "invalid-action"})
            self.assertEqual((first["adapter"], first["status"]), (ADAPTER, "needs_input"))
            self.assertEqual((second["adapter"], second["status"]), (ADAPTER, "error"))
        gateway.assert_not_called()

    def test_house_without_coordinates_uses_survey_in_the_open_world(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        anchor = {"x": 18, "y": 64, "z": -10}
        running = {"runId": "run-site", "state": "PREPARING"}
        with patch.object(bridge, "call", side_effect=[CAPS, {"anchor": anchor}, running]) as gateway:
            message = self.request("/api/admin", {"text": "Построй тестовый дом", "request_id": "survey"})
        self.assertEqual(message["status"], "running")
        survey, start = gateway.call_args_list[1:]
        self.assertEqual(survey.args[0], "find_site")
        self.assertEqual(survey.args[1]["width"], start.args[1]["blueprint"]["size"]["x"])
        self.assertEqual(start.args[1]["context"]["anchor"], anchor)
        self.assertEqual(start.args[1]["context"]["world"]["dimension"], "minecraft:overworld")
        self.assertIn("X 18", json.loads(message["result"])["detail"])

    def test_new_house_keeps_old_project_and_only_explicit_resume_selects_it(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        game_error = sys.modules[type(bridge).__module__].GameError
        world = "local:houses|minecraft:overworld"
        caps = {**CAPS, "multiProject": True, "world": {**CAPS["world"], "worldUid": world, "name": "houses"}}
        first = "prj-00000000-0000-0000-0000-000000000001"
        second = "prj-00000000-0000-0000-0000-000000000002"
        builds = {}
        selected = None

        def native(op, payload=None, properties=None):
            nonlocal selected
            if op == "capabilities":
                return caps
            if op == "find_site":
                if builds:
                    self.assertIn("0,0,12,12", payload["occupied"])
                return {"anchor": {"x": 26 if builds else 0, "y": 64, "z": 0}}
            if op == "start":
                selected = "run-" + str(len(builds) + 1)
                project = first if not builds else second
                builds[selected] = {"runId": selected, "projectId": project, "worldUid": world, "state": "PLANNING"}
                return builds[selected].copy()
            if op == "status":
                run = payload["runId"] or selected
                if run is None:
                    raise game_error("unknown_run", "empty")
                return builds[run].copy()
            if op == "pause":
                builds[payload["runId"]].update(state="PAUSED", controlReleased=True, waitingReason="pause_requested")
                return builds[payload["runId"]].copy()
            if op == "resume":
                selected = payload["runId"]
                builds[selected].update(state="PLANNING", controlReleased=False, waitingReason=None)
                return {"state": "PLANNING", "accepted": True}
            self.fail(op)

        with patch.object(bridge, "call", side_effect=native) as gateway:
            text = "Построй трёхэтажный дом"
            self.assertEqual(self.chat(text, "new-first")["status"], "running")
            reply = self.chat(text, "new-second")
            self.assertEqual(reply["status"], "running")
            self.assertEqual(self.chat(text, "new-second")["result"], reply["result"])
            self.assertEqual(len(builds), 2)
            self.assertEqual(builds["run-1"]["state"], "PAUSED")
            events = self.request("/api/activity?trace=admin-new-first")["events"]
            self.assertTrue(any(row["kind"] == "adapter.minecraft.state" and row["status"] == "paused" for row in events))
            catalog = self.request("/api/adapters/objects?id=mage_home")["items"]
            self.assertEqual([row["id"] for row in catalog], [second, first])
            self.assertEqual(catalog[1]["prompt"], text)
            self.assertEqual(catalog[1]["world_name"], "houses")
            self.assertTrue(catalog[1]["created_at"] and catalog[1]["can_resume"])
            self.assertEqual(self.chat("Продолжи стройку", "unspecified")["status"], "needs_input")
            self.assertFalse(any(call.args[0] == "resume" for call in gateway.call_args_list))
            target = self.request("/api/admin", {"text": f"Дострой дом {first}", "request_id": "resume-first"})
            self.assertEqual(json.loads(target["result"])["run_id"], "run-1")
            self.assertEqual(bridge.context.state()["trace_id"], "new-first")
            self.assertEqual(builds["run-2"]["state"], "PAUSED")
            self.assertEqual(gateway.call_args.args, ("resume", {"runId": "run-1"}))
            self.app.adapters.stop_all(preserve_projects=True)
            self.assertEqual(builds["run-1"]["state"], "PAUSED")
            self.assertEqual(bridge.context.state()["projects"][first]["model_request_id"], "new-first")
            builds["run-1"].update(state="READY", waitingReason=None)
            checkpoint = {
                "lastState": "READY", "anchor": {"x": 0, "y": 64, "z": 0},
                "blueprint": {"origin": {"x": 2, "y": 0, "z": -1},
                              "cells": [{"x": 0, "y": 0, "z": 0}, {"x": 7, "y": 8, "z": 5}],
                              "entrance": {"facing": "north"}},
            }
            with (patch.object(self.app.adapters, "object_capture", return_value={"sha256": "a" * 64}) as photo,
                  patch.object(bridge, "recovery_checkpoint", return_value=checkpoint)):
                self.app.adapters.observe()
                deadline = time.monotonic() + 2
                while not bridge.context.state()["projects"][first].get("photo") and time.monotonic() < deadline:
                    time.sleep(.02)
            self.assertEqual(photo.call_args.kwargs["focus"], {
                "run_id": "run-1", "project_id": first, "world_uid": world,
                "min": [2, 64, -1], "max": [9, 72, 4], "facing": "north",
            })
            rows = {row["id"]: row for row in self.request("/api/adapters/objects?id=mage_home")["items"]}
            self.assertEqual(rows[first]["photo"], "a" * 64)
            self.assertNotIn("photo", rows[second])

    def test_extension_switches_and_background_offer_only_dispatch_once(self):
        manager = self.app.extensions
        def update(**payload):
            return self.request("/api/extensions", payload)

        update(action="toggle", id="treegrow", enabled=False)
        update(action="toggle", id=ADAPTER, enabled=True)
        self.assertFalse(json.loads((manager.directory / "extensions.json").read_text())["native"]["treegrow"])
        manager.observed = {"world": True, "installed": {"altoclef": True, "mage_home": True},
                            "compatible": ["altoclef"], "connected": True}
        adapter_status = next(item for item in self.request("/api/extensions")["items"] if item["id"] == ADAPTER)
        self.assertFalse(adapter_status["available"])
        manager.observed["apis"] = {self.app.adapters.entry(ADAPTER).minecraft_api: True}
        self.assertEqual(update(action="interval", seconds=7)["interval"], 7)
        items = update(action="queue", id=ADAPTER, text="Построй тестовый дом")["items"]
        job = next(item["queued"] for item in items if item["id"] == ADAPTER)
        script = json.loads((manager.directory / "extensions.json").read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in script["tasks"]}, {"follow", "adapter:mage_home"})
        game = manager.runtime.game
        game.worker_id, game.requested, manager.runtime.active = "worker", True, True
        atomic_json(manager.directory / "status.json", {"worker_id": "worker", "updated": time.time(),
                    "agent_running": True, "world_session": "world", "world_ready": True})
        offer = {"adapter": ADAPTER, "id": job["id"], "updated": time.time(),
                 "revision": manager.revision, "worker_id": "worker", "world": "other"}
        with patch.object(self.app.adapters, "handle", return_value={"status": "running", "detail": "accepted"}) as handle:
            atomic_json(manager.directory / "extension-offer.json", offer)
            manager.dispatch()
            handle.assert_not_called()
            atomic_json(manager.directory / "extension-offer.json", {**offer, "world": "world"})
            manager.dispatch()
            manager.dispatch()
            handle.assert_called_once_with(ADAPTER, job["text"], job["id"], action="build", source="planner")
        with patch.object(manager, "refresh") as refresh:
            self.assertNotEqual(update(action="replan")["revision"], offer["revision"])
            refresh.assert_called_once()

    def test_running_house_yields_and_resumes_only_when_higher_priority_leaves(self):
        self.toggle(True)
        manager = self.app.extensions
        game = manager.runtime.game
        game.worker_id, game.requested, manager.runtime.active = "worker", True, True
        status = {"worker_id": "worker", "updated": time.time(), "agent_running": True,
                  "world_ready": True, "world_session": "world", "request_source": "player"}
        bridge = self.app.adapters.instance(ADAPTER)
        bridge.context.save_state({"run_id": "house"})
        bridge.owner_source = "planner"
        bridge.background_busy = True
        with patch.object(bridge, "call", side_effect=[
            {"state": "BUILDING"}, {"state": "PAUSED", "controlReleased": True}, {"state": "BUILDING"},
        ]) as call:
            manager.coordinate(status)
            self.assertEqual(bridge.scheduler_paused, "house")
            manager.coordinate({**status, "request_source": None, "defense_busy": True})
            self.assertEqual(call.call_count, 2)
            manager.coordinate({**status, "request_source": None, "native_busy": False})
            self.assertEqual(call.call_args.args, ("resume", {"runId": "house"}))
        self.assertIsNone(bridge.scheduler_paused)
        self.assertTrue(bridge.background_busy)
        bridge.owner_source = "operator"
        with patch.object(bridge, "yield_control") as relinquish:
            manager.coordinate(status)
            relinquish.assert_not_called()

    def test_extension_status_distinguishes_menu_from_an_unavailable_addon(self):
        self.toggle(True)
        manager = self.app.extensions
        api = self.app.adapters.entry(ADAPTER).minecraft_api
        menu = {"connected": True, "world": False, "installed": {ADAPTER: True},
                "compatible": ["altoclef"], "apis": {api: True}}
        scenarios = [({}, "checking"), ({"connected": False}, "client_disconnected"),
                     ({**menu, "installed": {}}, "mod_missing"), ({**menu, "apis": {}}, "api_missing"),
                     ({**menu, "compatible": []}, "unionclef_unavailable"), (menu, "waiting_world"),
                     ({**menu, "world": True}, "ready")]
        for observed, expected in scenarios:
            with self.subTest(availability=expected):
                manager.observed = observed
                item = next(row for row in self.request("/api/extensions")["items"] if row["id"] == ADAPTER)
                self.assertEqual(item["availability"], expected)
                self.assertEqual(item["available"], expected == "ready")

    def test_weight_validation_and_hunt_off_by_default(self):
        initial = self.request("/api/extensions")["background_tasks"]
        self.assertEqual(next(t for t in initial if t["id"] == "hunt")["selection_weight"], 0)
        updated = self.request("/api/extensions", {"action": "weights", "weights": {"hunt": .2, "trees": 0}})
        self.assertEqual(next(t for t in updated["background_tasks"] if t["id"] == "hunt")["selection_weight"], .2)
        with self.assertRaises(HTTPError) as rejected:
            self.request("/api/extensions", {"action": "weights", "weights": {"hunt": 1.01}})
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()

    def test_operator_adapter_dispatch_waits_for_matching_worker_grant(self):
        self.toggle(True)
        manager = self.app.extensions
        game = manager.runtime.game
        game.worker_id, game.requested, manager.runtime.active = "worker", True, True
        status = {"worker_id": "worker", "updated": time.time(), "agent_running": True, "world_ready": True}
        bridge = self.app.adapters.instance(ADAPTER)
        with patch.object(game, "read", side_effect=[
            status, {**status, "adapter_grant": "old"}, status, {**status, "adapter_grant": "new"},
        ]), patch.object(bridge, "handle_routed", return_value={"status": "running", "detail": "built"}) as execute:
            result = manager.operator_handle(ADAPTER, "Построй дом", "new", "build")
            execute.assert_called_once_with("Построй дом", "build", "new", source="operator")
        self.assertEqual(result["status"], "running")
        self.assertIsNone(manager.operator_adapter)
        self.assertEqual(bridge.owner_source, "operator")
        bridge.owner_source = "planner"
        with patch.object(bridge, "handle_routed", return_value={"status": "running", "detail": "working"}):
            self.app.adapters.handle(ADAPTER, "Статус", "read")
        self.assertEqual(bridge.owner_source, "planner")

    def test_handoff_reports_loaded_legacy_jar_and_manual_pause(self):
        self.toggle(True)
        manager = self.app.extensions
        game = manager.runtime.game
        game.worker_id, game.requested, manager.runtime.active = "worker", True, True
        status = {"worker_id": "worker", "updated": time.time(), "agent_running": True,
                  "world_ready": True, "native_status": "paused"}
        manager.checked = time.time()
        api = self.app.adapters.entry(ADAPTER).minecraft_api
        legacy_jar = "mage_home-0.1.0_outdated_no_llm.jar"
        manager.observed = {"connected": True, "world": True, "installed": {ADAPTER: True},
                            "jars": {ADAPTER: legacy_jar}, "apis": {api: False}}
        with patch.object(game, "read", return_value=status), patch.object(self.app.adapters, "handle") as call:
            with self.assertRaisesRegex(RuntimeError, legacy_jar):
                manager.operator_handle(ADAPTER, "Построй дом", "old-api", "build")
            manager.observed["apis"][api] = True
            with self.assertRaisesRegex(RuntimeError, "Нажмите Start"):
                manager.operator_handle(ADAPTER, "Построй дом", "paused", "build")
            manager.observed = {"connected": False}
            status["world_ready"] = False
            with self.assertRaisesRegex(RuntimeError, "UnionClef недоступен"):
                manager.operator_handle(ADAPTER, "Построй дом", "offline", "build")
            call.assert_not_called()
        self.assertIsNone(manager.operator_adapter)

    def test_distinct_models_drive_source_package_and_expert_callback(self):
        self.toggle(True)
        profile = self.app.models.store.profiles()[EXPERT]
        revision = profile.pop("revision")
        profile.update(provider="minimax", model="MiniMax-M3.1-Flash", reasoning="low")
        self.request("/api/models", {"role": EXPERT, "profile": profile, "revision": revision})
        self.assertNotIn(EXPERT, settings.read_profiles(self.app.models.store.path))
        self.assertEqual(self.app.models.store.profiles()[DESIGN]["provider"], "codex")

        bridge = self.app.adapters.instance(ADAPTER)
        resources = {"inventory": {"minecraft:red_carpet": 3}, "canSupplyBlocks": True}
        local_caps = {**CAPS, "world": {"dimension": "minecraft:overworld", "server": None,
                                       "connected": True, "worldUid": "local:design|minecraft:overworld"},
                      "resources": resources}
        with patch.object(bridge, "call", side_effect=[local_caps, {"runId": "run-live", "projectId": "test", "state": "PLANNING"}]) as gateway:
            self.assertEqual(self.chat("Построй каменный дом. X 4, Y -60, Z 5", "designing")["status"], "running")
        _, payload, properties = gateway.call_args.args
        self.assertEqual(payload["description"], "каменный дом")
        self.assertEqual(payload["context"]["resources"], resources)
        self.assertTrue(payload["options"]["continuousRecovery"])
        self.assertEqual(payload["options"]["expert"]["deadlineMillis"], 0)
        plan = {
            "palette": {"floor": "minecraft:stone_bricks", "wall": "minecraft:stone_bricks",
                        "roof": "minecraft:stone_bricks"},
            "levels": [{"footprint": [[0, 0, 6, 6]], "doors": [{"at": [3, 0], "facing": "north"}],
                        "rooms": [{"name": "Hall", "type": "hall", "area": [[1, 1, 5, 5]]}],
                        "decorations": [{"room": "Hall", "at": [3, 1, 2], "block": "minecraft:red_carpet"}]}],
            "entrance": {"door": [3, 1, 0]},
            "mandatory": [{"quote": "каменный", "checks": [
                {"kind": "palette", "element": "wall", "value": "minecraft:stone_bricks"},
            ]}], "assumptions": [], "conflicts": [],
        }
        rejected = deepcopy(plan)
        rejected["levels"][0]["doors"][0]["facing"] = "south"
        unsupported = deepcopy(plan)
        unsupported["levels"][0]["decorations"].append(
            {"room": "Hall", "at": [2, 2, 2], "block": "minecraft:lantern"})
        seat = deepcopy(plan)
        seat["levels"][0]["furniture"] = [
            {"room": "Hall", "at": [2, 3], "block": "minecraft:oak_stairs", "state": {"facing": "east"}}]
        self.provider.complete.side_effect = [
            {"json": json.dumps(candidate)} for candidate in (rejected, unsupported, seat, plan)]
        value = {"description": payload["description"], "context": payload["context"], "projectId": "test", "runId": "run-live"}
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "mage_home", "design", "--profile", properties["designProfile"]],
            input=json.dumps(value, ensure_ascii=False), encoding="utf-8", capture_output=True, timeout=10,
            env={**os.environ, "PYTHONPATH": properties["modulePath"]},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        design = json.loads(proc.stdout)
        self.assertTrue(design["ok"], design)
        self.assertEqual(design["blueprint"]["projectId"], "test")
        first, revision_call, second_call, final_call = self.provider.complete.call_args_list
        material_options = json.loads(first.args[1])["materialOptions"]
        revision = json.loads(revision_call.args[1])["revision"]
        self.assertEqual(revision["previousPlan"], rejected)
        self.assertIn("entrance does not face out", revision["compilerError"]["detail"])
        second_revision = json.loads(second_call.args[1])["revision"]
        self.assertEqual(second_revision["previousPlan"], unsupported)
        self.assertEqual(second_revision["compilerError"]["code"], "unsupported_cell")
        self.assertEqual(second_revision["earlierCompilerErrors"], [revision["compilerError"]])
        final_revision = json.loads(final_call.args[1])["revision"]
        self.assertIn("Hall at (2, 3): minecraft:oak_stairs", final_revision["compilerError"]["detail"])
        self.assertEqual(final_revision["earlierCompilerErrors"], [revision["compilerError"], second_revision["compilerError"]])
        self.assertEqual(design["blueprint"]["entrance"]["facing"], "north")
        self.assertEqual(material_options["inventory"], resources["inventory"])
        self.assertTrue(material_options["canSupplyBlocks"])
        self.assertIn("minecraft:red_carpet", material_options["supportedInteriorDecor"])
        self.assertIn("minecraft:red_carpet", {cell["block"] for cell in design["blueprint"]["cells"]})
        self.assertIsNone(self.provider.complete.call_args.kwargs["timeout"])
        self.assertTrue(callable(self.provider.complete.call_args.kwargs["cancelled"]))
        design_profile = bridge.context.data_dir / os.path.basename(properties["designProfile"])
        self.assertEqual(json.loads(design_profile.read_text(encoding="utf-8"))["deadlineMillis"], 0)

        expert = bridge.context.model_endpoint("expert", "designing")
        body = {"model": "expert", "messages": [{"role": "system", "content": "Repair"},
                                                {"role": "user", "content": "{}"}]}
        with patch("saruman.roles.api_complete", return_value=({"json": '{"outcome":"blocked"}'}, {})) as invoke:
            self.assertIn("blocked", self.request(expert, body, token=False)["choices"][0]["message"]["content"])
        self.assertEqual(invoke.call_args.args[0]["model"], "MiniMax-M3.1-Flash")
        self.assertIsNone(invoke.call_args.args[0]["timeout"])
        self.assertEqual({row["role"] for row in self.app.models.store.history()}, {DESIGN, EXPERT})
        kinds = [row["kind"] for row in self.request("/api/activity?trace=admin-designing")["events"]]
        self.assertEqual(kinds.count("llm.request"), 5)
        self.assertEqual(kinds.count("llm.result"), 5)
        self.assertIn("request.received", kinds)
        journal = bridge.context.data_dir / "design-history" / sha256(b"test").hexdigest()
        self.assertEqual(len(list(journal.glob("*.json"))), 4)
        with patch.object(bridge, "call", return_value={"runId": "run-live", "state": "BUILDING"}):
            bridge.observe(True, bridge.context.state())
        logged = self.request("/api/activity?trace=admin-designing")["events"]
        self.assertEqual(sum(row["kind"] == "adapter.minecraft.design" for row in logged), 4)
        bridge.context.save_state({})
        with self.assertRaises(HTTPError) as stale:
            self.request(expert, body, token=False)
        self.assertEqual(stale.exception.code, 403)
        stale.exception.close()

    def test_design_provider_timeout_reports_projecting_failure(self):
        self.toggle(True)
        self.app.adapters.entry(ADAPTER).agents["design"]["timeout_seconds"] = 180
        profile = self.app.models.store.profiles()[DESIGN]
        revision = profile.pop("revision")
        profile.update(provider="minimax", model="MiniMax-M3.1-Flash", timeout=70)
        self.request("/api/models", {"role": DESIGN, "profile": profile, "revision": revision})
        bridge = self.app.adapters.instance(ADAPTER)
        running = {"runId": "run-timeout", "state": "PLANNING"}
        with patch.object(bridge, "call", side_effect=[CAPS, running]) as gateway:
            self.chat("Построй трёхэтажный дом. X 4, Y -60, Z 5", "timeout")
        _, payload, properties = gateway.call_args.args
        value = {"description": payload["description"], "context": payload["context"], "projectId": "test"}
        opener = Mock()
        opener.open.side_effect = TimeoutError("private transport data")
        with patch("saruman.roles.build_opener", return_value=opener):
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "mage_home", "design", "--profile", properties["designProfile"]],
                input=json.dumps(value, ensure_ascii=False), encoding="utf-8", capture_output=True, timeout=10,
                env={**os.environ, "PYTHONPATH": properties["modulePath"]},
            )
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "provider_timeout", proc.stderr)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 70)
        event = next(row for row in self.request("/api/activity?trace=admin-timeout")["events"]
                     if row["kind"] == "llm.result")
        self.assertIn("тайм-аут", event["summary"])
        self.assertNotIn("private transport data", event["summary"])
        message = bridge.describe({"runId": "run-timeout", "state": "FAILED",
                                   "waitingReason": "design_provider_timeout"})
        self.assertIn("Подготовка участка ещё не началась", message["detail"])
        bridge.context.save_state({})

    def test_resume_after_client_restart_preserves_run_world_and_approved_blueprint(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        game = self.root / "isolated-save"
        folder = game / "config/mage_home/build-journals"
        folder.mkdir(parents=True)
        world_uid = "local:resume|minecraft:overworld"
        caps = {**CAPS, "gameDir": str(game), "world": {**CAPS["world"], "worldUid": world_uid}}
        run_id = "run-original"
        result = {"runId": run_id, "projectId": "project-original", "worldUid": world_uid, "state": "PLANNING"}
        with patch.object(bridge, "call", side_effect=[caps, result]):
            self.chat("Построй дом с печью. X 4, Y -60, Z 5", "original")
        state = bridge.context.state()
        binding = f"{world_uid}|project-original|4 -60 5|revision-history/1"
        blueprint = {"projectId": "project-original", "revision": 2}
        saved = {"binding": binding, "schema": "mage_home.attempts/1", "runId": run_id, "projectId": "project-original",
                 "worldUid": world_uid, "lastState": "PAUSED", "blueprint": blueprint}
        (folder / (sha256(binding.encode()).hexdigest() + ".json")).write_text(json.dumps(saved), encoding="utf-8")
        with patch.object(bridge, "call", side_effect=[caps, result]) as call:
            self.assertEqual(bridge.restore(world_uid, state)["runId"], run_id)
        operation, request, _ = call.call_args.args
        self.assertEqual(operation, "start")
        self.assertEqual(request["blueprint"], blueprint)
        self.assertEqual(request["options"]["restoreRunId"], run_id)
        self.assertEqual(request["options"]["restoreProjectId"], "project-original")
        self.assertEqual(bridge.context.state()["trace_id"], "original")

    def test_design_token_limit_reports_truncation_instead_of_provider_error(self):
        self.toggle(True)
        profile = self.app.models.store.profiles()[DESIGN]
        revision = profile.pop("revision")
        profile.update(provider="minimax", model="MiniMax-M3.1-Flash", reasoning="low", max_output=32768)
        self.request("/api/models", {"role": DESIGN, "profile": profile, "revision": revision})
        bridge = self.app.adapters.instance(ADAPTER)
        with patch.object(bridge, "call", side_effect=[CAPS, {"runId": "limit", "state": "PLANNING"}]) as game:
            self.chat("Построй трёхэтажный дом. X 4, Y -60, Z 5", "limit")
        _, payload, properties = game.call_args.args
        value = {"description": payload["description"], "context": payload["context"], "projectId": "test"}
        reply = {"stop_reason": "max_tokens", "content": [],
                 "usage": {"input_tokens": 1807, "output_tokens": 32768}}
        with patch("saruman.roles.read_until_complete", return_value=reply):
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "mage_home", "design", "--profile", properties["designProfile"]],
                input=json.dumps(value, ensure_ascii=False), encoding="utf-8", capture_output=True, timeout=10,
                env={**os.environ, "PYTHONPATH": properties["modulePath"]},
            )
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "provider_output_limit", proc.stderr)
        events = self.request("/api/activity?trace=admin-limit")["events"]
        event = next(row for row in events if row["kind"] == "llm.result")
        self.assertIn("32768", event["summary"])
        self.assertIn("лимит ответа", event["summary"])
        status = bridge.describe({"state": "FAILED", "waitingReason": "design_provider_output_limit"})
        self.assertIn("лимит токенов", status["detail"])
        bridge.context.save_state({})

    def test_unlimited_design_cancels_upstream_when_game_closes_the_worker(self):
        self.toggle(True)
        profile = self.app.models.store.profiles()[DESIGN]
        revision = profile.pop("revision")
        profile.update(provider="minimax", model="MiniMax-M3.1-Flash", timeout=5)
        self.request("/api/models", {"role": DESIGN, "profile": profile, "revision": revision})
        bridge = self.app.adapters.instance(ADAPTER)
        with patch.object(bridge, "call", side_effect=[CAPS, {"runId": "unlimited", "state": "PLANNING"}]) as game:
            self.chat("Построй дом. X 4, Y -60, Z 5", "unlimited")
        _, payload, properties = game.call_args.args
        value = {"description": payload["description"], "context": payload["context"], "projectId": "test"}
        started, disconnected = Event(), Event()

        class SilentProvider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                started.set()
                self.connection.settimeout(5)
                if not self.connection.recv(1):
                    disconnected.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), SilentProvider)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with patch("saruman.roles.protocol.endpoint", return_value=f"http://127.0.0.1:{server.server_port}/messages"):
                with subprocess.Popen(
                    [sys.executable, "-X", "utf8", "-m", "mage_home", "design", "--profile", properties["designProfile"]],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                    env={**os.environ, "PYTHONPATH": properties["modulePath"]},
                ) as worker:
                    try:
                        worker.stdin.write(json.dumps(value, ensure_ascii=False))
                        worker.stdin.close()
                        self.assertTrue(started.wait(4))
                        events = self.request("/api/activity?trace=admin-unlimited")["events"]
                        event = next(row for row in events if row["kind"] == "llm.request")
                        self.assertIsNone(self.request(f"/api/activity/event?id={event['id']}")["details"]["timeout_seconds"])
                    finally:
                        worker.terminate()
                        worker.wait(5)
                    self.assertTrue(disconnected.wait(2))
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    events = self.request("/api/activity?trace=admin-unlimited")["events"]
                    if any(row["kind"] == "llm.result" and row["status"] == "cancelled" for row in events):
                        break
                    time.sleep(0.05)
                self.assertTrue(any(row["kind"] == "llm.result" and row["status"] == "cancelled" for row in events))
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
            bridge.context.save_state({})

    def test_journal_groups_build_status_attempts_and_routes_by_chat_request(self):
        self.toggle(True)
        game = self.root / "fabric-profile"
        journal = game / "config/mage_home/build-journals"
        journal.mkdir(parents=True)
        (game / "logs").mkdir()
        (game / "logs/latest.log").write_text("Placed block\nAuthorization: Bearer local-token\n", encoding="utf-8")
        bridge = self.app.adapters.instance(ADAPTER)
        run = "run-ours"
        running = {"runId": run, "state": "BUILDING", "updatedAtTick": 10}
        with patch.object(bridge, "call", side_effect=[{**CAPS, "gameDir": str(game)}, running]):
            self.chat("Построй тестовый дом. X 4, Y -60, Z 5", "house")

        attempt = {"id": "a", "runId": run, "type": "attempt", "outcome": "failed",
                   "method": "walk", "reason": "obstruction", "actualRoute": [{"x": 4, "y": -60, "z": 5}]}
        snapshot = {"schema": "mage_home.attempts/1", "records": [
            attempt, {**attempt, "runId": "other-run"},
            {"id": "b", "runId": run, "type": "route", "status": "blocked"},
            {"id": "c", "runId": run, "type": "expert_decision", "accepted": False, "detail": "stale"},
        ]}
        (journal / ("a" * 64 + ".json")).write_text(json.dumps(snapshot), encoding="utf-8")
        ready = {"runId": run, "state": "READY", "controlReleased": True, "updatedAtTick": 30}
        with patch.object(bridge, "call", side_effect=[running, {**running, "updatedAtTick": 20}, ready]) as gateway:
            for _ in range(4):
                self.app.adapters.observe()
            self.assertEqual(gateway.call_count, 3)

        events = self.request("/api/activity?trace=admin-house")["events"]
        kinds = [row["kind"] for row in events]
        self.assertEqual(kinds.count("adapter.minecraft.attempt"), 1)
        self.assertIn("adapter.minecraft.route", kinds)
        self.assertIn("adapter.minecraft.expert", kinds)
        self.assertEqual(kinds.count("adapter.minecraft.state"), 2)
        observed = next(message for message in self.request("/api/admin")["messages"] if message["id"] == "house")
        self.assertEqual(observed["status"], "completed")
        bridge.context.record("adapter.minecraft.state", request_id="house", status="unavailable",
                              details={"text": "Клиент больше не сообщает состояние этого запуска.", "run_id": run})
        archived = next(message for message in self.request("/api/admin")["messages"] if message["id"] == "house")
        self.assertEqual(archived["result"], observed["result"])
        self.assertTrue(all(row["scope"] == "game_private" and row["actor"] == "Mage Home" for row in events))
        attempt_event = next(row for row in events if row["kind"] == "adapter.minecraft.attempt")
        details = self.request("/api/activity/event?id=" + str(attempt_event["id"]))["details"]
        self.assertEqual(details["record"], attempt)
        log = self.request("/api/activity/log?source=client&offset=0")["text"]
        self.assertIn("Placed block", log)
        self.assertIn("[redacted]", log)
        self.assertNotIn("local-token", log)
        bridge.context.save_state({})

    def test_journal_read_pins_old_snapshot_without_blocking_atomic_replace(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        reader = type(bridge).journals.__globals__["open_journal"]
        journal = self.root / "journal.json"
        draft = self.root / "journal-next.json"
        journal.write_bytes(b"previous")
        draft.write_bytes(b"new")
        with reader(journal) as opened:
            os.replace(draft, journal)
            self.assertEqual(opened.read(), b"previous")
        self.assertEqual(journal.read_bytes(), b"new")
        self.assertEqual(list(self.root.glob(".lab-read-*.tmp")), [])

    def test_invalid_journal_does_not_hide_the_live_build_status(self):
        self.toggle(True)
        game = self.root / "fabric-profile"
        folder = game / "config/mage_home/build-journals"
        folder.mkdir(parents=True)
        bridge = self.app.adapters.instance(ADAPTER)
        run = {"runId": "run-ours", "state": "BUILDING"}
        with patch.object(bridge, "call", side_effect=[{**CAPS, "gameDir": str(game)}, run]):
            self.chat("Построй тестовый дом. X 4, Y -60, Z 5", "house")
        file = folder / ("a" * 64 + ".json")
        file.write_text(json.dumps({"schema": "mage_home.attempts/1", "records": None}), encoding="utf-8")
        paused = {**run, "state": "PAUSED", "waitingReason": "journal_write_failed", "controlReleased": True}
        with patch.object(bridge, "call", return_value=paused):
            self.app.adapters.observe()
            self.app.adapters.observe()

        events = self.request("/api/activity?trace=admin-house")["events"]
        self.assertEqual(sum(event["kind"] == "adapter.minecraft.journal" for event in events), 1)
        self.assertTrue(any(event["kind"] == "adapter.minecraft.state" and event["status"] == "paused" for event in events))
        observed = next(message for message in self.request("/api/admin")["messages"] if message["id"] == "house")
        self.assertEqual(observed["status"], "paused")
        self.assertIn("Ожидается доступ к журналу", observed["result"])

    def test_local_world_status_reads_only_the_current_saves_checkpoint(self):
        self.toggle(True)
        bridge = self.app.adapters.instance(ADAPTER)
        game = self.root / "profile"
        journal = game / "config/mage_home/build-journals"
        journal.mkdir(parents=True)
        first, second = "local:first|minecraft:overworld", "local:second|minecraft:overworld"
        def caps(uid):
            return {**CAPS, "gameDir": str(game),
                    "world": {"dimension": "minecraft:overworld", "server": None,
                              "connected": True, "worldUid": uid, "name": "Same display name"}}
        def checkpoint(name, uid, state, cells):
            (journal / (name * 64 + ".json")).write_text(json.dumps({
                "schema": "mage_home.attempts/1", "worldUid": uid, "runId": uid, "lastState": state,
                "anchor": {"x": 4, "y": -60, "z": 5},
                "progress": {"targetCells": 180, "confirmedCells": cells},
            }), encoding="utf-8")
        checkpoint("a", first, "PAUSED", 17)
        checkpoint("b", second, "READY", 180)
        error = sys.modules[type(bridge).__module__].GameError("unknown_run", "No live run")
        with patch.object(bridge, "call", side_effect=[error, caps(first), error, caps(second),
                                                       caps(first), {"runId": "run-new", "state": "PREPARING", "worldUid": first}]) as gateway:
            earlier = json.loads(self.chat("Статус", "world-a")["result"])
            later = json.loads(self.chat("Статус", "world-b")["result"])
            self.assertIn("17 из 180", earlier["detail"])
            self.assertEqual(earlier["world_uid"], first)
            self.assertIn("180 из 180", later["detail"])
            self.assertEqual(later["world_uid"], second)
            self.assertEqual(self.chat("Построй тестовый дом. X 4, Y -60, Z 5", "local-start")["status"], "running")
            sent_world = gateway.call_args.args[1]["context"]["world"]
            self.assertEqual(sent_world["worldUid"], first)
            self.assertIsNone(sent_world["server"])
        bridge.context.save_state({})
