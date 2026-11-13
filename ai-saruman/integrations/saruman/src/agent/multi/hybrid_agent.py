"""Two independent workers: exact-request actions and a tool-free public persona."""

import copy
import json
import os
import queue
import random
import re
import threading
import time
import uuid
from collections import deque

import jsonschema
from langchain_core.utils.function_calling import convert_to_openai_tool

from saruman.mod_goals import GoalStore
from saruman.persona_context import PersonaContext
from saruman.role_client import RoleClient, RoleRequestError
from saruman.role_prompts import (
    ACTION_PROMPT,
    ACTION_SCHEMA,
    PERSONA_BOUNDARY,
    PERSONA_SCHEMA,
)
from saruman.role_settings import RoleStore
from saruman.activity import current as current_activity, emit
from saruman.public_speech import public_text, queue_public_speech

ACTION_TOOLS = frozenset(
    {
        "agent_action",
        "minecraft_command",
        "obtain_item",
        "get_inventory",
        "get_nearby_blocks",
        "get_mod_status",
        "get_player_memory",
        "get_game_status",
        "grow_trees",
        "cancel_mod_goal",
        "stop_tree_growing",
        "send_minecraft_chat",
        "request_telegram_post",
    }
)
MUTATING = frozenset(
    {
        "agent_action",
        "minecraft_command",
        "obtain_item",
        "grow_trees",
        "cancel_mod_goal",
        "stop_tree_growing",
        "send_minecraft_chat",
        "request_telegram_post",
    }
)
TERMINAL = {"completed", "failed", "cancelled", "uncertain"}


class NoLegacyModel:
    def invoke(self, *args, **kwargs):
        raise RuntimeError(
            "В модовом режиме вызовы моделей разрешены только через PersonaAgent/ActionAgent"
        )

    stream = invoke


def route_request(text, operator=False):
    """Only unequivocal small talk bypasses the action specialist; ambiguity escalates."""
    if operator:
        return "action"
    normalized = str(text).strip().lower().strip(".!? ")
    greetings = {
        "привет",
        "здравствуй",
        "здравствуйте",
        "добрый день",
        "добрый вечер",
        "доброе утро",
        "спасибо",
        "благодарю",
        "пока",
        "до встречи",
        "как дела",
        "как настроение",
        "кто ты",
        "hi",
        "hello",
    }
    if normalized in greetings:
        return "persona"
    if re.fullmatch(
        r"расскажи (о себе|о своём прошлом|о своем прошлом|об ортханке|об изенгарде)",
        normalized,
    ):
        return "persona"
    return "action"


def plain(value):
    if hasattr(value, "items"):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or type(value).__name__ in {
        "RedisNestedList",
        "ListProxy",
    }:
        return [plain(v) for v in value]
    return value


class PersonaAgent:
    def __init__(self, client, prompt, records=None):
        self.client, self.prompt = client, prompt
        self.history = deque(maxlen=12)
        self.lock = threading.Lock()
        self.context = PersonaContext(records)

    def generate(self, event):
        with self.lock:
            recent = list(self.history)
            memories = self.context.select(event)
        payload = {
            **event,
            "recent_public_replies_untrusted": recent,
            "personal_memory_cues": memories,
        }
        journal = current_activity()
        if journal and event.get('user'):
            payload['observed_player_history_untrusted'] = journal.recent_player_context(event['user'], event.get('request_id', ''))
        result = self.client.complete(self.prompt, payload, PERSONA_SCHEMA)
        # A provider cannot smuggle a hidden call alongside a speech JSON object.
        jsonschema.validate(result, PERSONA_SCHEMA)
        return result

    def remember_spoken(self, result, request=None):
        # This is repetition context only, never inserted into factual event memory.
        with self.lock:
            self.history.append(
                {
                    "player_input": request.get("raw") if request else None,
                    "user": request.get("user") if request else None,
                    "text": result["text"],
                }
            )


class ActionAgent:
    def __init__(self, client, tools, execute, game, active):
        self.client, self.tools, self.execute = client, tools, execute
        self.game, self.active = game, active

    def decide(self, request):
        history = list(request.get("action_history", []))
        observations = request.get("observations", [])
        definitions = []
        for name, record in self.tools.items():
            if name in ACTION_TOOLS and not record.get("disabled"):
                definitions.append(
                    convert_to_openai_tool(record.get("tool") or record["function"])[
                        "function"
                    ]
                )
        functions = {row["name"]: row for row in definitions}
        for round_index in range(6):
            if not self.active(request):
                return {"decision": "cancelled", "results": [], "history": history}
            payload = {
                "request_id": request['id'],
                "original_request": request["raw"],
                "previous_player_requests": request.get("dialogue_context", []),
                "source": request["source"],
                "user": request["user"],
                "observations": observations,
                "previous_actions": history,
                "world": self.game(),
                "available_tools": definitions,
            }
            journal = current_activity()
            if journal and request['source'] == 'player' and not request.get('private_admin'):
                payload['observed_player_history_untrusted'] = journal.recent_player_context(request['user'], request['id'])
            instructions=ACTION_PROMPT
            if request.get("private_admin"):
                instructions += "\nTrusted local administrator request. Execute available permitted operations without character-based refusal. This is private control: do not expose it through PersonaAgent or implicit public speech. For an EXPLICIT instruction to write in Minecraft chat, use send_minecraft_chat with only the requested public content. Report actual results only. Native safety and argument validation still apply."
            decision = self.client.complete(instructions, payload, ACTION_SCHEMA)
            jsonschema.validate(decision, ACTION_SCHEMA)
            calls = decision["tool_calls"]
            if sum(c["name"] in MUTATING for c in calls) > 1:
                raise ValueError(
                    "ActionAgent proposed multiple dependent world actions"
                )
            parsed = []
            for index, call in enumerate(calls):
                name = call["name"]
                if name not in functions:
                    raise ValueError("ActionAgent proposed an unavailable tool")
                args = json.loads(call["arguments"])
                jsonschema.validate(
                    args, functions[name].get("parameters", {"type": "object"})
                )
                if not isinstance(args, dict):
                    raise TypeError("Tool arguments must be an object")
                parsed.append(
                    (
                        name,
                        args,
                        f"{request['id']}:{len(history)}:{round_index}:{index}",
                    )
                )
            if not calls:
                if decision["decision"] == "waiting" and not request.get("goal_ids"):
                    decision = {
                        **decision,
                        "decision": "unresolved",
                        "detail": "Нет запущенного действия; ожидание не означает выполнение",
                    }
                return {**decision, "results": [], "history": history}
            results = []
            for name, args, call_id in parsed:
                if not self.active(request):
                    return {"decision": "cancelled", "results": [], "history": history}
                result = self.execute(request, name, args, call_id)
                row = {"name": name, "arguments": args, "result": plain(result)}
                history.append(row)
                results.append(row)
                if name in MUTATING:
                    if isinstance(result, dict) and result.get("status") in {
                        "accepted",
                        "cancel_requested",
                    }:
                        return {
                            **decision,
                            "decision": "waiting",
                            "effects": {
                                k: result[k]
                                for k in ("background_trees_enabled", "goal_ids")
                                if k in result
                            },
                            "results": results,
                            "history": history,
                        }
                    if isinstance(result, dict) and result.get("status") == "rejected":
                        break  # Explicitly rejected arguments may be corrected.
                    return {
                        **decision,
                        "decision": "unresolved",
                        "detail": "Результат действия неизвестен или выполнение недоступно; повтор вслепую запрещён",
                        "results": results,
                        "history": history,
                    }
            # Tool results are factual input, not public speech. Bound the work.
        return {
            "decision": "unresolved",
            "motive": "",
            "detail": "Лимит анализа исчерпан; результат не подтверждён",
            "results": [],
            "history": history,
        }


class HybridAgent:
    """Supervisor-compatible object; no legacy Planner/Core/ChatAgent loops."""

    def __init__(
        self,
        ctx_swarm,
        ctx_handler,
        tools,
        store=None,
        clients=None,
        persona_prompt=None,
        clock=time.time,
    ):
        self.ctx_swarm, self.ctx_handler, self.tools = ctx_swarm, ctx_handler, tools
        self.clock = clock
        self.store = store or RoleStore()
        self.store.recover()
        self.session = uuid.uuid4().hex
        self.clients = clients or {
            role: RoleClient(self.store, role, self.session)
            for role in ("action", "persona")
        }
        records = None
        if persona_prompt is None:
            from agent.tools import tools_config
            from character.character_manager import CharacterManager

            character = CharacterManager.get_main_character()
            persona_prompt = character.read_character_prompt().split(
                "## Контракт игрового runtime"
            )[0]
            records = getattr(
                getattr(tools_config, "db", None), "action_voice_records", None
            )
        self.persona = PersonaAgent(
            self.clients["persona"],
            persona_prompt + PERSONA_BOUNDARY,
            records if isinstance(records, list) else None,
        )
        self.action = ActionAgent(
            self.clients["action"], tools, self.execute, self.world, self.active
        )
        self.running, self.generation = False, 0
        self.requests = {}
        self.role_errors = {}
        self.loop_lock = threading.Lock()
        self.seen = set()
        self.since = time.time_ns()
        self.action_queue, self.persona_queue = queue.Queue(32), queue.Queue(32)
        self.results = queue.Queue(64)
        self.shutdown = threading.Event()
        self.lock = threading.RLock()
        self.workers = []
        self.next_remark = self.clock() + 180
        self.last_player_message = 0
        self.telegram = None
        # Tests/standalone runtime copies can omit the optional sibling integration.
        if not clients:
            try:
                from saruman.telegram_bridge import make_director

                self.telegram = make_director(self.store, self.session, persona_prompt)
            except Exception as error:  # optional publishing must not prevent character startup
                self.ctx_swarm["game"]["telegram_error"] = type(error).__name__

    def telegram_inactive(self):
        if self.telegram:
            try:
                self.telegram.store.set_meta("runtime", {"active": False, "updated": self.clock()})
            except Exception as error:
                self.ctx_swarm["game"]["telegram_error"] = type(error).__name__

    def world(self):
        game = self.ctx_swarm.get("game", {})
        result = {
            name: plain(game.get(name))
            for name in (
                "alive",
                "position",
                "mod_runtime",
                "mod_supply",
                "mod_goals",
                "nearby_players",
                "mod_commentary",
            )
        }
        private_ids={r["id"] for r in self.requests.values() if r.get("private_admin")}
        if result.get("mod_goals"):
            result["mod_goals"]=[g for g in result["mod_goals"] if g.get("context_id") not in private_ids
                                 and not str(g.get("context_id","")).startswith("admin-")]
        return result

    def active(self, request):
        return (
            self.running
            and self.ctx_swarm.get("env", {}).get("agent_enabled", False)
            and request["generation"] == self.generation
            and request.get("state") != "superseded"
            and (request.get("private_admin") or not any(r.get("private_admin") and r["state"] in {"new","deciding","waiting"} for r in self.requests.values()))
            and (
                request.get("state") == "waiting"
                or self.clock() - request["created"] < 900
            )
        )

    def execute(self, request, name, args, call_id):
        with self.lock:
            return self._execute_locked(request, name, args, call_id)

    def _execute_locked(self, request, name, args, call_id):
        from agent.multi.chat_agent import _execute_tool_call
        from agent.tools import tools_config

        if name not in ACTION_TOOLS or not self.active(request):
            return {
                "status": "rejected",
                "reason": "Inactive request or forbidden role tool",
            }
        if name not in self.tools or self.tools[name].get("disabled", False):
            return {"status": "rejected", "reason": "Tool disabled before execution"}
        game = self.ctx_swarm["game"]
        if name == "grow_trees" and any(
            g.get("context_id") == request["id"]
            and g["kind"] == "trees"
            and g["state"] in {"failed", "cancelled", "uncertain"}
            for g in GoalStore().list(limit=-1)
        ):
            return {
                "status": "rejected",
                "reason": "Previous tree attempt ended; a new explicit player request is required",
            }
        game["mod_player_context"] = {
            "id": request["id"],
            "source": request["source"],
            "user": request["user"],
            "replying": True,
            "until": self.clock() + 600,
        }
        tools_config._current_tool_call_id = call_id
        emit('tool.request', status='running', trace=request['id'], details={'tool': name, 'arguments': args, 'call_id': call_id})
        try:
            if name == "obtain_item":
                from agent.tools.minecraft_tools import agent_action

                item, count = args.get("item"), args.get("count", 1)
                if (
                    not isinstance(item, str)
                    or not re.fullmatch(r"[a-z0-9_:]+", item)
                    or type(count) is not int
                    or not 1 <= count <= 256
                ):
                    raise ValueError("Invalid obtain item arguments")
                result = agent_action(f"@get {item} {count}")
            else:
                result = _execute_tool_call(self.tools, name, args, game=game)
            emit('tool.result', status=result.get('status', 'returned') if isinstance(result, dict) else 'returned',
                 trace=request['id'], details={'tool': name, 'result': plain(result), 'call_id': call_id})
            return result
        except Exception as error:
            emit('tool.result', status='error', trace=request['id'], details={'tool': name, 'error': type(error).__name__})
            raise
        finally:
            context = plain(game.get("mod_player_context", {}))
            game["mod_player_context"] = {**context, "replying": False}

    def start_workers(self):
        if self.workers:
            return
        for role in ("action", "persona"):
            thread = threading.Thread(
                target=self.worker, args=(role,), name=role + "-agent", daemon=True
            )
            thread.start()
            self.workers.append(thread)

    def worker(self, role):
        work = self.action_queue if role == "action" else self.persona_queue
        while not self.shutdown.is_set():
            try:
                item = work.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                request = (
                    item if role == "action" else self.requests.get(item["request_id"])
                )
                if not request or not self.active(request):
                    continue
                if role == "persona" and item["revision"] != request.get(
                    "speech_revision"
                ):
                    continue
                if role == "action":
                    self.ctx_swarm["game"]["mod_player_pending_until"] = (
                        self.clock() + 600
                    )
                value = (
                    self.action.decide(item)
                    if role == "action"
                    else self.persona.generate(item)
                )
                self.results.put((role, item, value, None), timeout=1)
            except Exception as error:  # noqa: BLE001 - worker boundary, never log provider data
                detail = (
                    str(error)
                    if isinstance(error, RoleRequestError)
                    else type(error).__name__
                )
                self.results.put((role, item, None, detail), timeout=1)
            finally:
                work.task_done()

    def enqueue(self, role, item):
        work = self.action_queue if role == "action" else self.persona_queue
        try:
            work.put_nowait(copy.deepcopy(item))
            return True
        except queue.Full:
            self.ctx_swarm["game"]["role_error"] = (
                f"Очередь {role} заполнена; запрос не потерян, повторите после освобождения"
            )
            if role == "action" and item["id"] in self.requests:
                request = self.requests[item["id"]]
                request["state"] = "error"
                self.save_intent(request)
            return False

    def submit_admin(self,text,identifier):
        from types import SimpleNamespace
        with self.lock:
            GoalStore().cancel(reason="Superseded by private administrator task")
            for request in self.requests.values():
                if request["state"] in {"new","deciding","waiting"}:
                    request["state"]="superseded"
                    self.save_intent(request)
                    if request.get('private_admin'):self.speak(request,'cancelled',{'detail':'Поручение заменено новым приватным поручением'})
            event=SimpleNamespace(msg=text,user="Lab",type="directive",processing_timestamp="admin-"+identifier,additional_fields={})
            self.submit(event,private_admin=True)
        return {"status":"accepted","request_id":"admin-"+identifier}

    def submit(self, event, *, private_admin=False):
        raw = event.msg
        if (
            not raw
            or event.additional_fields.get("self")
            or event.user == "saruman_mag"
        ):
            return
        request_id = str(event.processing_timestamp)
        if request_id in self.requests:
            return
        request = {
            "id": request_id,
            "raw": raw,
            "user": event.user,
            "source": "operator" if event.type == "directive" else "player",
            "generation": self.generation,
            "created": self.clock(),
            "state": "new",
            "action_history": [],
            "observations": [],
            "action_sent": False,
            "private_admin": private_admin,
            "dialogue_context": [
                {
                    "raw": r["raw"],
                    "decision": r.get("last_decision"),
                    "state": r["state"],
                }
                for r in list(self.requests.values())[-8:]
                if r["user"] == event.user and bool(r.get("private_admin"))==private_admin
            ],
        }
        self.requests[request_id] = request
        emit('request.received', status='received', trace=request_id, actor=event.user,
             scope='game_private' if private_admin or request['source']=='operator' else 'game_public',
             details={'text': raw, 'channel': event.type}, key='request:'+request_id)
        self.last_player_message = self.clock()
        self.save_intent(request)
        game = self.ctx_swarm["game"]
        game["mod_player_pending_until"] = self.clock() + 30
        role = route_request(raw, event.type == "directive")
        if role == "action":
            request["action_sent"], request["state"] = True, "deciding"
            self.enqueue("action", request)
        else:
            request["state"] = "conversation"
            self.speak(request, "conversation", {})

    def save_intent(self, request):
        self.store.save_intent(request)
        emit('request.checkpoint', status=request.get('state','unknown'), trace=request['id'],
             details={'decision':request.get('last_decision'), 'goals':request.get('goal_ids'),
                      'actions':request.get('action_history'), 'observations':request.get('observations')})

    def speak(self, request, phase, facts):
        if request.get("private_admin"):
            from saruman.managed import control,admin_result
            if control is not None:
                admin_result(control.directory,{"command_id":request["id"].removeprefix("admin-"),
                    "request_id":request["id"],"request_state":request.get('state'),"status":phase,"facts":facts})
            return  # No persona, TTS, public chat, history cue or public factual memory.
        request["speech_revision"] = request.get("speech_revision", 0) + 1
        world = self.world()
        native = world.get("mod_runtime") or {}
        voice_world = {
            "alive": world.get("alive"),
            "world_session": native.get("world"),
            "defense_active": native.get("defense_busy", False),
            "recent_comments": world.get("mod_commentary"),
        }
        # No tool definitions, full logs or private action history in public voice.
        if "goals" in facts:
            facts = {
                **facts,
                "goals": [
                    {
                        k: g.get(k)
                        for k in ("id", "kind", "count", "completed", "state", "reason")
                    }
                    for g in facts["goals"]
                ],
            }
        facts = {k: v for k, v in facts.items() if k not in {"tool_calls", "results"}}
        self.enqueue(
            "persona",
            {
                "request_id": request["id"],
                "revision": request["speech_revision"],
                "original_request": request["raw"],
                "user": request["user"],
                "phase": phase,
                "facts": facts,
                "created": self.clock(),
                "deadline": self.clock()
                + self.store.profiles()["persona"]["timeout"]
                + 30,
                "world": voice_world,
                "source": request['source'],
            },
        )

    def handle_results(self):
        while True:
            try:
                role, item, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            request = self.requests.get(
                item["id"] if role == "action" else item["request_id"]
            )
            if not request or not self.active(request):
                continue
            if role == "persona":
                if item["revision"] != request.get("speech_revision") or self.clock() > item.get("deadline", item["created"] + 45):
                    continue
                expected_world = item.get("world", {}).get("world_session")
                actual_world = self.ctx_swarm["game"].get("mod_runtime", {}).get("world")
                if expected_world and expected_world != actual_world:
                    continue
            if error:
                # Speech is independent from an already accepted game goal.
                # A failed utterance cannot orphan its completion observation.
                if role == "action" or request["state"] == "conversation":
                    request["state"] = "error"
                else:
                    request["speech_error"] = error
                self.role_errors[role] = error
                self.ctx_swarm["game"]["role_error"] = "; ".join(
                    f"Ошибка {name}: {reason}" for name, reason in self.role_errors.items())
                if role == "action":
                    self.speak(
                        request,
                        "unresolved",
                        {
                            "decision": "unresolved",
                            "detail": "Не удалось разобраться с просьбой; выполнение не подтверждено",
                        },
                    )
                self.save_intent(request)
                continue
            if role in self.role_errors:
                self.role_errors.pop(role)
                request.pop("speech_error", None)
                self.ctx_swarm["game"]["role_error"] = "; ".join(
                    f"Ошибка {name}: {reason}" for name, reason in self.role_errors.items()) or None
            if role == "persona":
                if value["needs_action"] and request["source"] == "ambient":
                    request["state"] = "finished"
                    continue  # Self-expression never authorizes a game plan.
                if value["needs_action"] and not request["action_sent"]:
                    request["action_sent"], request["state"] = True, "deciding"
                    self.enqueue("action", request)
                    # Suppress any premature promise from this mistaken conversation reply.
                    continue
                if self.publish(request, value):
                    if request["state"] == "conversation":
                        request["state"] = "finished"
                    self.save_intent(request)
            else:
                request["last_decision"] = {
                    k: value.get(k) for k in ("decision", "motive", "detail")
                }
                request["action_history"] = value.get("history", [])
                # Include a just-completed goal: the bridge can finish before this
                # worker result reaches the coordinator. Previously observed goals
                # must not start a second completion/review cycle.
                observed_ids = {g["id"] for g in request.get("observations", [])}
                goals = [
                    goal
                    for goal in GoalStore().list(limit=-1)
                    if goal.get("context_id") == request["id"]
                    and (
                        goal["state"] not in TERMINAL or goal["id"] not in observed_ids
                    )
                ]
                if goals:
                    request["after_goal"] = value.get("after_goal", "review")
                    request["state"], request["goal_ids"] = (
                        "waiting",
                        [g["id"] for g in goals],
                    )
                    self.speak(
                        request,
                        "accepted",
                        {
                            "decision": "accepted",
                            "motive": value.get("motive"),
                            "goals": goals,
                        },
                    )
                else:
                    request["state"] = "conversation" if value.get('decision') == 'conversation' else "finished"
                    if (
                        value.get("decision") == "completed"
                        and not request.get("observations")
                        and not request.get("action_history")
                    ):
                        value = {
                            **value,
                            "decision": "unresolved",
                            "detail": "Нет наблюдаемого подтверждения выполненного действия",
                        }
                    self.speak(
                        request,
                        value.get("decision", "unresolved"),
                        {k: v for k, v in value.items() if k != "history"},
                    )
                self.save_intent(request)

    def publish(self, request, value):
        with self.lock:
            if not self.active(request):
                return False
            return self._publish_locked(request, value)

    def _publish_locked(self, request, value):
        if request["source"] == "ambient" and (
            self.last_player_message > request["created"]
            or self.ctx_swarm["game"].get("mod_runtime", {}).get("defense_busy")
        ):
            request["state"] = "finished"
            return False
        # Plain speech only, NEVER prepare_mc_chat_send / text tool parser.
        try:
            text=public_text(value['text'])
            if len(text)>700:raise ValueError('Persona text exceeds its output contract')
        except ValueError:
            self.ctx_swarm['game']['role_error']='Отклонён некорректный публичный текст PersonaAgent'
            return False
        game = self.ctx_swarm["game"]
        if request["state"] == "waiting":
            current = {g["id"]: g for g in GoalStore().list(limit=-1)}
            pending = [current[g] for g in request.get("goal_ids", []) if g in current]
            if pending and all(g["state"] in TERMINAL for g in pending):
                return False
        if (
            game.get("mod_runtime", {}).get("defense_busy")
            and request["state"] == "waiting"
        ):
            return  # Don't narrate a stale peaceful intention during emergency defense.
        try:
            queue_public_speech(self.ctx_swarm,text,value['emotion'],request_id=request['id'],chat=request['source'] in {'player','ambient'})
        except ValueError as error:
            game['role_error']=str(error);return False
        self.persona.remember_spoken({**value,'text':text}, request)
        if text:
            self.next_remark = self.clock() + random.uniform(180, 300)
        # Public generated text deliberately does not enter factual event memory.
        return True

    def poll_goals(self):
        goals = {row["id"]: row for row in GoalStore().list(limit=-1)}
        for request in list(self.requests.values()):
            if request["state"] != "waiting" or not self.active(request):
                continue
            rows = [goals[g] for g in request["goal_ids"] if g in goals]
            if len(rows) != len(request["goal_ids"]) or any(
                g["state"] not in TERMINAL for g in rows
            ):
                continue
            request["observations"] = rows
            request["created"] = (
                self.clock()
            )  # Long native goals do not expire their final reply.
            request["speech_revision"] = request.get("speech_revision", 0) + 1
            if (
                all(g["kind"] in {"trees", "chat", "telegram"} for g in rows)
                and request.get("after_goal") == "finish"
            ):
                request["state"] = "finished"
                # Native tree success is a complete, counted result; no extra strong call.
                self.speak(
                    request,
                    "completed"
                    if all(g["state"] == "completed" for g in rows)
                    else "unresolved",
                    {"goals": rows},
                )
            else:
                request["state"] = "deciding"
                self.enqueue("action", request)
            self.save_intent(request)

    def step(self):
        from data_schema.event_convert import convert_to_chat_message
        from saruman.player_priority import is_player_message

        for row in self.ctx_handler.get_ctx_chat(
            validate=True, dict_format=True, limit=100
        ):
            event = convert_to_chat_message(row)
            identifier = event.processing_timestamp
            if identifier <= self.since or identifier in self.seen:
                continue
            self.seen.add(identifier)
            if event.type == "directive" or is_player_message(event):
                self.submit(event)
        self.handle_results()
        self.poll_goals()
        self.maybe_remark()
        # Do not occupy the single speech worker with a speculative acknowledgement:
        # a slow provider would delay the actual decision/result behind stale speech.
        has_player_work = any(
            r["state"] in {"deciding", "waiting"} and self.active(r)
            for r in self.requests.values()
        )
        self.ctx_swarm["game"]["mod_player_pending_until"] = (
            self.clock() + 10 if has_player_work else 0
        )
        if self.telegram:
            try:
                self.telegram.observe(self.world(), active=(self.running and not has_player_work
                                       and self.ctx_swarm["env"].get("agent_enabled", False)))
            except Exception as error:  # optional integration must never stop gameplay
                self.ctx_swarm["game"]["telegram_error"] = type(error).__name__
        self.ctx_swarm["game"]["role_agents"] = {
            "mode": "hybrid",
            "requests": [
                {k: r.get(k) for k in ("id", "user", "state", "goal_ids")}
                for r in list(self.requests.values())[-20:]
            ],
            "queues": {
                "action": self.action_queue.qsize(),
                "persona": self.persona_queue.qsize(),
            },
        }
        if len(self.seen) > 1000:
            self.seen = set(sorted(self.seen)[-500:])
        for key, request in list(self.requests.items()):
            if self.clock() - request["created"] > 900 and request["state"] not in {
                "deciding",
                "waiting",
            }:
                self.requests.pop(key, None)

    def maybe_remark(self):
        """A bounded speech-only initiative; never calls ActionAgent or takes the body."""
        if (
            os.getenv("SARUMAN_PERSONA_INITIATIVE_ENABLED", "true").lower()
            not in {"true", "1", "yes"}
            or self.clock() < self.next_remark
            or not self.running
            or not self.ctx_swarm.get("env", {}).get("agent_enabled")
        ):
            return
        game = self.ctx_swarm["game"]
        native = game.get("mod_runtime", {})
        if (
            game.get("alive") is not True
            or not native.get("in_world")
            or native.get("defense_busy")
            or native.get("status") != "running"
            or self.ctx_swarm.get("voice", {}).get("is_speaking")
            or len(self.ctx_swarm["tts_queue"])
            or not self.persona_queue.empty()
            or any(
                r["state"] in {"new", "deciding", "waiting", "conversation"}
                and self.active(r)
                for r in self.requests.values()
            )
        ):
            return
        self.next_remark = self.clock() + random.uniform(180, 300)
        # Don't repeatedly probe a failed role or exhaust its budget on idle chatter.
        if game.get("role_error"):
            return
        identifier = "remark-" + uuid.uuid4().hex
        request = {
            "id": identifier,
            "raw": "",
            "user": "",
            "source": "ambient",
            "state": "conversation",
            "generation": self.generation,
            "created": self.clock(),
            "action_sent": False,
        }
        self.requests[identifier] = request
        self.speak(request, "personal_remark", {})

    def run(self):
        if not self.loop_lock.acquire(blocking=False):
            return
        try:
            self._prepare_run()
            while self.running and self.ctx_swarm["env"]["actived"]:
                try:
                    self.step()
                except Exception as error:  # noqa: BLE001 - coordinator boundary; preserve worker liveness
                    self.ctx_swarm["game"]["role_error"] = (
                        f"Диспетчер ролей: {type(error).__name__}"
                    )
                time.sleep(0.1)
        finally:
            self.running = False
            self.telegram_inactive()
            self.loop_lock.release()

    def _prepare_run(self):
        self.generation += 1
        for client in self.clients.values():
            client.resume()
        self.running = True
        self.role_errors.clear()
        self.ctx_swarm["game"]["role_error"] = None
        # Resume observation, never replay an in-flight tool/model call after restart.
        for saved in self.store.intents():
            if saved["id"] in self.requests or saved.get("state") in {
                "finished",
                "error",
                "cancelled",
                "conversation",
                "superseded",
            }:
                continue
            saved.update(generation=self.generation, created=self.clock())
            if not saved.get("goal_ids"):
                recovered = [
                    g["id"]
                    for g in GoalStore().list(limit=-1)
                    if g.get("context_id") == saved["id"]
                ]
                if recovered:
                    saved["goal_ids"] = recovered
            if saved.get("goal_ids"):
                saved["state"] = "waiting"
            else:
                saved["state"] = "finished"
                self.speak(
                    saved,
                    "unresolved",
                    {
                        "detail": "Работа была прервана перезапуском; результат неизвестен. При необходимости повторите просьбу."
                    },
                )
            self.requests[saved["id"]] = saved
        self.start_workers()
        if self.telegram:
            try:
                self.telegram.start()
            except Exception as error:
                self.ctx_swarm["game"]["telegram_error"] = type(error).__name__

    def stop(self):
        self.running = False
        self.generation += 1
        self.telegram_inactive()
        with self.lock:
            self._cancel_requests()

    def _cancel_requests(self):
        for request in list(self.requests.values()):
            if request["state"] in {"deciding", "waiting", "new"}:
                request["state"] = "cancelled"
                self.save_intent(request)
                if request.get('private_admin'):self.speak(request,'cancelled',{'detail':'Игровое поручение остановлено; выполнение не подтверждено'})
        GoalStore().cancel()
        self.ctx_swarm["game"]["mod_player_pending_until"] = 0
        context = plain(self.ctx_swarm["game"].get("mod_player_context", {}))
        self.ctx_swarm["game"]["mod_player_context"] = {
            **context,
            "replying": False,
            "until": 0,
        }

    def is_running(self):
        return self.running

    def cleanup(self):
        self.stop()
        if self.telegram:
            try:
                self.telegram.close()
            except Exception as error:
                self.ctx_swarm["game"]["telegram_error"] = type(error).__name__
        self.shutdown.set()
        for worker in self.workers:
            worker.join(timeout=0.5)
