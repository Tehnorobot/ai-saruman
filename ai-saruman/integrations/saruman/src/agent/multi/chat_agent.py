"""ChatAgent — modern agent with native LLM function calling.

Uses LiteLLM's bind_tools for structured tool calling when the model supports it.
Falls back to plain text generation + manual speak() call when bind_tools fails.

Features:
- Native function calling via langchain bind_tools (when supported)
- Fallback: plain text response -> speak tool (for models without tool support)
- Proper user/assistant message alternation
- Streaming with real-time console output
- Timing instrumentation for every step
- Interrupt on new chat events
"""

import time
import traceback
from copy import deepcopy
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.base_agent import BaseAgent
from agent.llm_clients.lc_clients import get_llm_chain
from agent.prompt_generation.message_builder import events_to_messages
from agent.prompt_generation.prompt_constructor import (
    SmartEventWaiter,
    get_game_status,
    get_mood_context,
    get_users_info,
)
from agent.tools import tools_config
from agent.tools.status_tools import get_tool_status
from character.character_manager import CharacterManager
from config_schema.general import get_name
from data_flow.ctx_handler import CtxHandler
from data_flow.ctx_host import CtxHost
from data_schema.activity_tracker import IDLE_SELF_ACTIVATE, IDLE_THRESHOLD
from data_schema.chat_structures import (
    CtxEventBase,
    default_event_check,
    interrupt_event_check,
    nick_mention_check,
)
from data_schema.ctx_structures import CtxSwarmType
from data_schema.tool_structures import SpeakEmotions, ToolBankType
from utils.debug import print_messages
from saruman.minecraft import COMMAND_GUIDE
from saruman.procedures import ProcedureContinuation, procedure_prompt, select_procedure
from saruman.tree_tasks import cancel_task, result as tree_task_result
from saruman.tree_memory import prepare_resume, resume_paused
from saruman.autonomy import Autonomy
from saruman.mod_goals import using_mods, LEGACY_MOD_TOOLS, GoalStore
from saruman.mod_prompt import MOD_PROMPT
from saruman.action_speech import backstory_prompt
from saruman.player_priority import PlayerInbox, is_player_message

SELF_NAME = get_name()
MAX_TOOL_ROUNDS = 3
MAX_RESPONSE_TOKENS = 500  # Hard limit on LLM generation per round
ENABLE_DEBUG_PRINT_TOOL_LIST = True
# Minimum characters for a valid response — shorter = interrupted garbage, discard it
MIN_RESPONSE_LENGTH = 12
# After an interrupt, ignore further interrupts for this many seconds
INTERRUPT_COOLDOWN = 10.0
# Max interrupts per respond() call
MAX_INTERRUPTS_PER_CALL = 2


def _timer(label: str):
    """Simple context-manager-like timer that prints elapsed time."""
    class _T:
        def __init__(self):
            self.start = time.time()
        def __enter__(self):
            self.start = time.time()
            return self
        def __exit__(self, *a):
            elapsed = time.time() - self.start
            print(f"[CHAT AGENT ⏱️ {elapsed:.2f}s] {label}")
    return _T()


# Tools excluded from LLM tool calling — handled directly from text output
DIRECT_TEXT_TOOLS = {"speak", "speak_list"}


def _get_available_tools(tool_bank: ToolBankType, exclude: set = DIRECT_TEXT_TOOLS, game=None) -> List:
    tools = []
    for name, record in tool_bank.items():
        if record.get("disabled", False):
            continue
        if name in exclude or (using_mods(game or {}) and name in LEGACY_MOD_TOOLS):
            continue
        lc_tool = record.get("tool")
        if lc_tool is not None:
            tools.append(lc_tool)
    return tools


def _execute_tool_call(tool_bank: ToolBankType, tool_name: str, tool_args: dict, *, game=None) -> Any:
    if tool_name not in tool_bank:
        return f"Error: tool '{tool_name}' not found"
    record = tool_bank[tool_name]
    if record.get("disabled", False):
        return f"Error: tool '{tool_name}' is disabled"
    game = {} if game is None else game
    if using_mods(game) and tool_name in LEGACY_MOD_TOOLS:
        return {"status": "rejected", "reason": "Native mods own tree/combat logic. Use grow_trees or get_mod_status."}
    if using_mods(game) and tool_name in {"agent_action", "minecraft_command", "obtain_item", "eat_food", "grow_trees", "cancel_mod_goal"}:
        context = game.get("mod_player_context", {})
        if not context.get("replying") or context.get("until", 0) < time.time():
            return {"status": "rejected", "reason": "World actions require an active player/operator turn"}
        if tool_name == "eat_food" and (game.get("mod_runtime", {}).get("defense_busy") or game.get("mod_runtime", {}).get("native_busy")):
            return {"status": "busy", "reason": "Native controller owns the body"}
    if game.get("self_defense", {}).get("active") and tool_name in {
        "agent_action", "minecraft_command", "obtain_item", "eat_food", "apply_bone_meal",
        "grow_tree", "manage_trees", "plant_sapling", "find_tree_planting_spots",
        "farm_crops", "tend_trees", "hunt_hostiles", "attack_hostile", "explore_nearby",
        "ensure_scepter", "cast_scepter",
    } and tool_args.get("action") != "@stop" and not (tool_name == "cast_scepter" and tool_args.get("action") == "protect" and game.get("combat_runtime") != "reflex-v2"):
        return {"status": "waiting_for_self_defense"}
    if game.get("survival", {}).get("active") and tool_name in {
        "agent_action", "minecraft_command", "obtain_item", "eat_food", "apply_bone_meal",
        "grow_tree", "manage_trees", "plant_sapling", "find_tree_planting_spots",
        "farm_crops", "tend_trees", "hunt_hostiles", "attack_hostile", "explore_nearby",
        "ensure_scepter", "cast_scepter", "defend_self",
    }:
        return {"status": "waiting_for_survival", "survival": dict(game["survival"].items())}
    if "alive" in game and game.get("alive") is not True and tool_name in {
        "agent_action", "minecraft_command", "obtain_item", "eat_food",
        "apply_bone_meal", "grow_tree", "manage_trees", "plant_sapling",
        "find_tree_planting_spots", "farm_crops", "hunt_hostiles", "attack_hostile",
        "explore_nearby", "tend_trees",
        "ensure_scepter", "cast_scepter", "defend_self",
    }:
        return {"status": "waiting_for_respawn", "respawn": dict(game.get("respawn", {}).items())}
    priority = game.get("player_priority", {})
    if priority.get("awaiting_stop") and tool_name in {
        "agent_action", "obtain_item", "eat_food", "apply_bone_meal", "grow_tree",
        "manage_trees", "plant_sapling", "farm_crops", "tend_trees", "hunt_hostiles",
        "attack_hostile", "explore_nearby",
        "ensure_scepter", "cast_scepter", "defend_self",
    } and tool_args.get("action") != "@stop":
        if game.get("hasActiveTask") or game.get("navigation", {}).get("status") in ("stopping", "stop_requested", "stop_failed"):
            return {"status": "waiting_for_task_stop", "reason": "Background action is stopping for player priority; wait for confirmed idle"}
        game["player_priority"] = {**dict(priority.items()), "awaiting_stop": False}
    func = record["function"]
    try:
        return func(**tool_args)
    except Exception as e:
        # A TypeError may happen AFTER an interaction. Never replay a world action
        # with positional arguments as a speculative recovery from that error.
        return f"Error executing {tool_name}: {e}"


class ChatAgent(BaseAgent):
    """Modern chat agent with native function calling + plain-text fallback."""

    def __init__(
        self,
        ctx_swarm: CtxSwarmType,
        ctx_handler: Optional[CtxHandler] = None,
        ctx_host: Optional[CtxHost] = None,
        tool_bank: Optional[ToolBankType] = None,
    ):
        super().__init__(ctx_swarm, ctx_handler)
        if ctx_handler is None:
            self.ctx_handler = CtxHandler(ctx_swarm)
        else:
            self.ctx_handler = ctx_handler

        self.llm = get_llm_chain()
        self.self_name = get_name()
        # Preserve new directives while paused, without replaying previous sessions.
        self._last_directive_ts = time.time_ns()
        self._player_inbox = PlayerInbox(since=self._last_directive_ts)

        if tool_bank is not None:
            self.tool_bank = tool_bank
        else:
            self.tool_bank = tools_config.init_tools_module(
                ctx_swarm, self.ctx_handler, new_llm=self.llm
            )

        if ctx_host is None:
            self.ctx_host = CtxHost(ctx_handler, tool_bank=self.tool_bank)
        else:
            self.ctx_host = ctx_host

        # Try to bind tools; fall back to plain text if model doesn't support it
        self._tools_supported = False
        self._rebind_tools()

        # Cache system prompt — must be identical every call for KV cache hits
        self._cached_system_prompt = self._build_system_prompt()
        self._cached_personality = self.ctx_swarm["states"].get("personality", "streamer_abstract")

        # APPEND-ONLY prefix — the core of KV cache strategy.
        # Starts with [SystemMessage], events and LLM responses get appended.
        # Prompt sent to LLM = _cached_prefix + [HumanMessage(tail)]
        # Objects in this list are NEVER replaced or reordered.
        self._cached_prefix: List[BaseMessage] = [
            SystemMessage(content=self._cached_system_prompt),
        ]
        self._last_event_ts: int = 0  # timestamp of last processed event
        self._prefix_max_tokens: int = 15000  # trim when exceeded
        self._prefix_max_messages: int = 40  # hard limit on message count

        # Interrupt rate limiting
        self._last_interrupt_time = 0.0
        self._interrupts_this_call = 0

        # KV cache keepalive — ping LM Studio every few seconds to prevent eviction
        self._keepalive_interval = 3.0  # seconds between pings
        self._keepalive_running = False
        self._keepalive_thread = None
        self._keepalive_enabled = self._should_enable_keepalive()
        self._last_tail_content = "."  # last tail sent to LLM — reused in ping

        self.initialized = True

    def _rebind_tools(self):
        """Bind tools to LLM. If bind_tools fails, fall back to plain text mode."""
        available_tools = _get_available_tools(self.tool_bank, game=self.ctx_swarm.get("game", {}))
        self._tool_names = [t.name for t in available_tools]

        if available_tools:
            try:
                self.llm_with_tools = self.llm.bind_tools(available_tools)
                self._tools_supported = True
                print(f"[CHAT AGENT] Native tool calling ON, {len(available_tools)} tools: {self._tool_names}")
                if ENABLE_DEBUG_PRINT_TOOL_LIST:
                    for t in available_tools:
                        desc = (t.description or "no description")
                        args = ""
                        if hasattr(t, "args_schema") and t.args_schema:
                            try:
                                schema = t.args_schema.schema()
                                props = schema.get("properties", {})
                                args = ", ".join(f"{k}: {v.get('type', '?')}" for k, v in props.items())
                            except Exception:
                                args = "?"
                        status = get_tool_status(t.name)
                        status_str = f" | status: {status}" if status else ""
                        print(f"  [{t.name}]({args}) {desc}{status_str}")
            except Exception as e:
                print(f"[CHAT AGENT] bind_tools failed ({e}), falling back to plain text mode")
                self.llm_with_tools = self.llm
                self._tools_supported = False
        else:
            self.llm_with_tools = self.llm
            self._tools_supported = False
            print("[CHAT AGENT] No tools available, plain text mode")

    def _build_system_prompt(self) -> str:
        """Build system prompt. Keep STATIC for prompt caching —
        dynamic info (tool statuses, mood etc.) goes in situation_context."""
        character = CharacterManager.get_main_character()
        personality = self.ctx_swarm["states"].get("personality", "streamer_abstract")
        prompt = character.read_character_prompt(personality)
        native = using_mods(getattr(self, "ctx_swarm", {}).get("game", {}))
        if native:
            prompt = prompt.split("## Контракт игрового runtime")[0]
        if character.character_name == "Saruman":
            records = getattr(getattr(tools_config, "db", None), "action_voice_records", None)
            prompt += backstory_prompt(records if isinstance(records, list) else None)

        if native:
            return prompt + MOD_PROMPT
        emotions = list(SpeakEmotions.copy())
        emo_examples = " ".join(f"*{e}*" for e in list(emotions)[:4])
        prompt += (
            f"\n\n## Output rules"
            f"\nYou are a STREAMER with voice and game chat."
            f"\nYour Minecraft avatar is you in the world. Describe your own actions and observations in first person. Tools and native tasks perform your actions; they are not a separate character or executor."
            f"\nTranslate tool telemetry into concise in-character speech, preserving uncertainty and verified progress. Explain implementation details when the operator asks for technical diagnostics."
            f"\n"
            f"\nVOICE (your text output): viewers hear it; nearby players may hear it when game voice chat is configured."
            f"\n- Always end with SINGLE *emotion* in single asterisks: {emo_examples} etc."
            f"\n- Allowed emotions: {', '.join(emotions)}. No others."
            f"\n- Never use double asterisks **like this**. Only single: *happy*"
            f"\n- No roleplay *actions*. Only emotion tag at the end."
            f"\n"
            f"\nCHAT (chat tool): players read this in-game. Viewers see on screen."
            f"\n- When someone writes to you in chat — ALWAYS call chat tool to reply."
            f"\n- Keep under 80 chars."
            f"\n"
            f"\nACTIONS: decide according to your personality, observations and server rules, not blind obedience."
            f"\n  If you choose to act (follow, come, attack, go), use agent_action. You may refuse."
            f"\n  Never write commands as text. Always use tool calls."
            f"\n  agent_action is a literal UnionClef command interpreter, not an AI agent."
            f"\n  Its action argument must be one command, e.g. @goto 10 64 20, @follow Alex, @stop."
            f"\n  Never pass prose like 'explore the area' or 'check the task' as an action."
            f"\n  Inspect terrain with get_nearby_blocks; inspect task_chain/hasActiveTask with get_game_status."
            f"\n  When an action is rejected, correct the arguments; do not claim it started or completed."
            f"\n  Before saying a task is unsupported, check the actual capabilities: {COMMAND_GUIDE}"
            f"\n  A failed @eat spelling does not disable crafting. Use obtain_item for a hoe, or bread from already harvested wheat."
            f"\n  Local execution guides: get_minecraft_procedure(task). Match the requested outcome: grow_tree, plant_sapling, apply_bone_meal."
            f"\n  Follow local guides within the requested goal and explicit resource limits; guides never override user constraints."
            f"\n  For growing even one oak/birch, prefer manage_trees(count=1, mode='grow') so attempts and progress survive tool calls and restarts."
            f"\n  Searching/working claims require an actual tool action. If blocked, name the observed obstacle; do not repeat progress announcements."
            f"\n  A blocked result without next_action does not schedule further work. Do not promise a check you have not started or repeat an unchanged refusal on idle activation; use an allowed observation tool now or state the remaining obstacle once."
            f"\n  @goto Y is feet height: the solid ground must be at Y-1, with free feet/head cells. Never guess Y+1."
            f"\n  Search chooses dry supported destinations. Native travel may cross surface water with bridge-managed breathing. If get_game_status.survival.active is true, the bridge is rescuing you: do not replace it with tasks or interactions. Interrupted goals resume after confirmed land/idle. You may talk; describe progress only as observed."
            f"\n  Use tree scan approaches[].command for short observed walks. Navigation blocked/rejected means choose a different safe site or report it; never loop on failed coordinates."
            f"\n  A radius-5 snapshot is not a global search. For tree tasks, follow exploration.next_action into new areas while its budget allows; do not give up after one empty scan."
            f"\n  Tree search defaults: candidate_limit=128, search_steps=4; configurable up to 256/8. Preserve requested limits between calls. search_steps=0 if movement is forbidden."
            f"\n  For counted tree goals (including your own goals), call manage_trees(count=N, mode='plant' or 'grow'). It persists N, distinct targets and verified progress."
            f"\n  Follow manage_trees next_action/resume with the same arguments until completed or blocked. Queuing materials or one successful tree does not complete an N-tree goal."
            f"\n  For an operator request to continue/resume, read get_game_status.tree_task and call manage_trees with its saved config, new_task=false. Keep confirmed progress and saved positions; never start the original quantity from zero. Resume in the same Minecraft world."
            f"\n  max_bone_meal in manage_trees is a TOTAL task budget, never a per-tree allowance. Plant mode never fertilizes. Do not reset a task with new_task=true to evade limits."
            f"\n  At most five bone-meal attempts per sapling. Exhausted saplings are left in place and skipped, not reported grown. A partial result ends this goal: report grown/skipped, then choose another activity or the next operator task; do not reset the same failed goal to retry it."
            f"\n  Prefer observed supported land. Surface swimming is allowed with bridge breathing control; do not invent underwater routes. Emergency rescue follows observed swimming corridors independently of native pathfinding."
            f"\n  Idle work is scheduled by runtime through registered tools. A blocked goal keeps its verified result; do not repeat it or announce an unscheduled check. Read get_game_status.autonomy for actual background actions."
            f"\n  Real Minecraft player chat and accepted player requests take priority over all autonomous hobbies. Reply to the player first. Background work is queued and may be interrupted; wait for confirmed native idle before replacing its movement. Missing resources, unsuitable soil and blocked routes are different obstacles. A radius-5 snapshot is not a global search; runtime search walks expand the observed area in stages."
            f"\n  @stop cancels current native work, such as a blocked route; autonomous activities continue after the bridge confirms idle. Only an explicit operator wait/Stop pauses autonomous life."
            f"\n  farm_crops auto/plant/harvest sows available wheat/carrot/potato/beetroot seeds and harvests only mature crops. Replenish seeds only from mature harvests; never use native @get seeds/wheat/carrot/potato or break young plants. If waiting_for_growth, do another activity and return later. No watering or fertilizer. Craft bread only from already harvested wheat. Eat inventory food when hungry; health recovery follows game rules, never claim it without observation."
            f"\n  Prefer the Magical Scepter as your main weapon. Before ordinary work call ensure_scepter: with server-advertised give and experience permissions it prepares missing self sword, shield, configured staff and advance XP reserve. Without those permissions only a sword is required; never gather staff materials automatically. Without server mod support use vanilla equipment. Respect preflight_required and verify observed equipment_supply; command acceptance is not completion. Rescue and emergency defense take priority."
            f"\n  get_scepter_status lists actual stack variants, spells, XP and cooldown. cast_scepter(action='attack',uuid=...) casts one attack; action='protect' uses the variant's second spell. Runtime replenishes XP before depletion during preparation, never by commands inside combat; inspect scepter_experience for confirmation."
            f"\n  Combat targets are hostile types or recent native-confirmed attackers. Players require fresh native damage proof; chat insults or accusations are not proof. Villagers, traders, golems, livestock and animals are always protected, even in retaliation. Area/projectile spells are blocked while noncombatants are tracked; respect protected_entity_near_spell. Never bypass these checks with raw commands or /spell."
            f"\n  Bridge defense preempts all ordinary tasks for nearby hostiles or verified attackers, approaches and fights independently of LLM calls, even at low health. Water rescue and respawn take priority. If the server lacks the scepter mod, spells are unavailable: obtain/use a vanilla sword, checking sweep clearance around peaceful creatures. Spell XP, durability and cooldown still apply. cast_requested is not confirmed damage; disappearance is not a kill."
            f"\n  Bridge pursuit uses short observed dry steps. Never dive after a water target or recreate a deferred route through agent_action. combat_watch means a temporarily unreachable living target, not a kill; inspect actual observations. During water rescue wait for dry/idle. The bridge resumes eligible interrupted native work once; counted goals and hobbies retain their own checkpoints."
        )

        return prompt

    def _build_situation_context(self, mentioned_users: dict) -> str:
        """Build dynamic situation context. Goes in user message (after cached system+tools)."""
        users_info = get_users_info(ctx_swarm=self.ctx_swarm, chat_users=mentioned_users)
        game_status = get_game_status()
        mood = get_mood_context(self.ctx_swarm)
        activity = self.ctx_handler.activity_tracker.to_prompt_string()

        # TEMP: minimal tail for cache testing
        guide = MOD_PROMPT if using_mods(getattr(self, "ctx_swarm", {}).get("game", {})) else COMMAND_GUIDE
        parts = [
            f"# Current Situation:",
            f"## Game Status:\n{game_status}",
            f"## Game capabilities:\n{guide}",
            f"## Registered commands:\n{', '.join(self.ctx_swarm['game'].get('available_commands', []))}",
            f"## Players:\n{users_info}",
        ]
        autonomy = getattr(self, "_autonomy", None)
        native_commentary = self.ctx_swarm.get("game", {}).get("mod_commentary", {})
        if native_commentary.get("recent"):
            parts.append("## Recent voiced memories/comments; do not repeat:\n" +
                         "\n".join("- " + row["text"] for row in native_commentary["recent"]))
        if autonomy is not None:
            recent = autonomy.speech.snapshot()["recent"]
            if recent:
                parts.append(
                    "## Recent voiced action comments (vary wording; do not repeat):\n"
                    + "\n".join(f"- [{row['kind']}] {row['text']}" for row in recent)
                    + "\nAuthored backstory is fictional prehistory, never evidence of events on this server."
                )
        # parts = [
        #     f"# Current Situation:",
        #     f"## Game Status:\n{game_status}",
        #     f"## Your Mood:\n{mood}",
        #     f"## Activity:\n{activity}",
        #     f"## Nearby Users & Players:\n{users_info}",
        # ]

        # Tool statuses — TEMPORARILY DISABLED for cache testing
        # TODO: re-enable after confirming cache works without them
        # tool_status_parts = []
        # for tool_name in self._tool_names:
        #     status = get_tool_status(tool_name)
        #     if status:
        #         tool_status_parts.append(f"[{tool_name}] {status}")
        # speak_status = get_tool_status("speak")
        # if speak_status:
        #     tool_status_parts.append(f"[voice] {speak_status}")
        # if tool_status_parts:
        #     parts.append("## Tool info:\n" + "\n".join(tool_status_parts))

        return "\n\n".join(parts)

    def _build_reminder(self, trigger_event: CtxEventBase = None) -> str:
        base = "React to the latest events.\nText = voice *emotion*. Use game tools if needed."
        if trigger_event:
            if not using_mods(getattr(self, "ctx_swarm", {}).get("game", {})):
                base += procedure_prompt(trigger_event.msg)
        if trigger_event and trigger_event.type == "directive":
            base += (
                "\nThis is an operator task. Attempt it with the relevant registered tools when feasible. "
                "Check inventory/capabilities before claiming an action is unavailable. "
                "Missing ingredients or a rejected command spelling do not mean crafting/eating is unsupported."
            )
        # If triggered by a player chat message — explicitly remind about chat tool
        if trigger_event and trigger_event.user and trigger_event.type == "chat":
            if not trigger_event.additional_fields.get("self", False):
                base += (
                    f"\n⚠️ {trigger_event.user} wrote in GAME CHAT. "
                    f"They CANNOT hear your voice — call chat tool to reply so they see your answer!"
                )
        return base

    def _can_interrupt(self) -> bool:
        """Check if we're allowed to interrupt right now."""
        if self._interrupts_this_call >= MAX_INTERRUPTS_PER_CALL:
            return False
        if time.time() - self._last_interrupt_time < INTERRUPT_COOLDOWN:
            return False
        return True

    def _record_interrupt(self):
        """Record that an interrupt happened."""
        self._last_interrupt_time = time.time()
        self._interrupts_this_call += 1

    def _estimate_tokens(self, messages: List[BaseMessage]) -> int:
        """Rough token estimate: ~3 chars per token for multilingual."""
        return sum(len(m.content) for m in messages) // 3

    @staticmethod
    def _should_enable_keepalive() -> bool:
        """Determine if KV cache keepalive should be enabled.

        OVERRIDE_PROMPT_CACHING_PING env var:
          "auto" (default) — enable for lm_studio, disable for others
          "true" — always enable
          "false" — always disable
        """
        import os
        override = os.getenv("OVERRIDE_PROMPT_CACHING_PING", "auto").lower().strip()
        if override == "true":
            return True
        if override == "false":
            return False
        # auto: enable only for local LM Studio
        model_name = os.getenv("CORE_LLM_MODEL_NAME", "").lower()
        enabled = "lm_studio" in model_name or "lm-studio" in model_name or "lmstudio" in model_name
        print(f"[CHAT AGENT] KV cache keepalive: {'ON' if enabled else 'OFF'} (model={model_name}, override={override})")
        return enabled

    def _start_keepalive(self):
        """Start background thread that pings LM Studio to keep KV cache alive."""
        if not self._keepalive_enabled:
            return
        if self._keepalive_running:
            return
        self._keepalive_running = True
        from threading import Thread
        self._keepalive_thread = Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def _stop_keepalive(self):
        """Stop keepalive pinging (called before LLM request)."""
        self._keepalive_running = False

    def _keepalive_loop(self):
        """Ping LM Studio with cached prefix + last tail to prevent KV cache eviction.
        Uses the SAME tail as last real request — maximizes cache hit on next call."""
        llm = self.llm_with_tools if self._tools_supported else self.llm
        while self._keepalive_running:
            time.sleep(self._keepalive_interval)
            if not self._keepalive_running:
                break
            if len(self._cached_prefix) <= 1:
                continue
            try:
                ping_msgs = list(self._cached_prefix) + [HumanMessage(content=self._last_tail_content)]
                # Suppress litellm info messages during ping
                import logging
                logging.getLogger("LiteLLM").setLevel(logging.ERROR)
                for _ in llm.stream(ping_msgs, max_tokens=1):
                    pass
                logging.getLogger("LiteLLM").setLevel(logging.WARNING)
            except Exception:
                pass

    def _invalidate_cache(self):
        """Full cache reset — rebuilds prefix from scratch."""
        self._cached_prefix = [SystemMessage(content=self._cached_system_prompt)]
        self._last_event_ts = 0

    def _append_to_prefix(self, msgs: List[BaseMessage]):
        """Append messages to cached prefix. Merges consecutive HumanMessages
        in-place (Qwen requires user/assistant alternation)."""
        for msg in msgs:
            if (
                isinstance(msg, HumanMessage)
                and self._cached_prefix
                and isinstance(self._cached_prefix[-1], HumanMessage)
            ):
                # Merge into existing HumanMessage — same object, mutated content
                self._cached_prefix[-1].content += "\n\n" + msg.content
            else:
                self._cached_prefix.append(msg)

    def _trim_prefix_if_needed(self):
        """Trim oldest messages from prefix if too long.
        Keeps SystemMessage[0] intact, trims from index 1."""
        token_est = self._estimate_tokens(self._cached_prefix)
        msg_count = len(self._cached_prefix)
        if token_est <= self._prefix_max_tokens and msg_count <= self._prefix_max_messages:
            return

        # Keep system message + last 2/3 of the rest
        rest = self._cached_prefix[1:]
        keep = min(len(rest) * 2 // 3, self._prefix_max_messages - 1)
        removed = len(rest) - keep
        self._cached_prefix = [self._cached_prefix[0]] + rest[-keep:]

        # After trim, ensure first message after system is HumanMessage (Qwen requirement)
        if len(self._cached_prefix) > 1 and not isinstance(self._cached_prefix[1], HumanMessage):
            self._cached_prefix.insert(1, HumanMessage(content="(conversation start)"))

        new_est = self._estimate_tokens(self._cached_prefix)
        print(f"[CHAT AGENT] Prefix trimmed: removed {removed} msgs, ~{new_est} tokens left")

    def respond(self, trigger_event: CtxEventBase) -> str:
        if trigger_event and trigger_event.type == "directive" and not using_mods(getattr(self, "ctx_swarm", {}).get("game", {})):
            autonomy = getattr(self, "_autonomy", None)
            if autonomy is not None:
                autonomy.directive(trigger_event.msg)
            game = getattr(self, "ctx_swarm", {}).get("game")
            if game is not None:
                if not prepare_resume(game, trigger_event.msg):
                    game["tree_search"] = {}
                    game["tree_task"] = {}
            self._last_navigation_failure = getattr(self, "ctx_swarm", {}).get("game", {}).get("navigation", {}).get("event_id")
            self._minecraft_procedure = (
                ProcedureContinuation(trigger_event.msg) if select_procedure(trigger_event.msg) else None
            )
        if not self.initialized:
            return ""
        if trigger_event and trigger_event.type == "directive":
            self._last_directive_ts = max(
                self._last_directive_ts, trigger_event.processing_timestamp
            )
        self._interrupts_this_call = 0

        # Rebuild system prompt if personality changed → full cache invalidation
        mode = self.ctx_swarm.get("game", {}).get("automation_mode")
        if mode != getattr(self, "_bound_automation_mode", None):
            self._bound_automation_mode = mode
            self._rebind_tools()
            self._cached_system_prompt = self._build_system_prompt()
            self._invalidate_cache()
        current_personality = self.ctx_swarm["states"].get("personality", "streamer_abstract")
        if current_personality != self._cached_personality:
            print(f"[CHAT AGENT] Personality changed: {self._cached_personality} -> {current_personality}")
            self._cached_system_prompt = self._build_system_prompt()
            self._cached_personality = current_personality
            self._invalidate_cache()

        t_total = time.time()

        # --- 1. Append new events to prefix ---
        with _timer("build prompt"):
            all_events = self.ctx_handler.get_ctx_chat(validate=True, dict_format=True, limit=50)
            new_events = [
                e for e in all_events
                if int(e.get("processing_timestamp", 0)) > self._last_event_ts
            ]

            if new_events:
                new_msgs, mentioned_users = events_to_messages(
                    new_events, trigger_event.processing_timestamp
                )
                # Ensure first thing after SystemMessage is a HumanMessage
                if len(self._cached_prefix) == 1 and new_msgs and not isinstance(new_msgs[0], HumanMessage):
                    self._cached_prefix.append(HumanMessage(content="(conversation start)"))

                self._append_to_prefix(new_msgs)
                self._last_event_ts = max(
                    int(e.get("processing_timestamp", 0)) for e in new_events
                )
                cache_status = f"append {len(new_msgs)} msgs from {len(new_events)} events"
            else:
                mentioned_users = {}
                from agent.prompt_generation.message_builder import _track_user
                for ev in all_events:
                    _track_user(ev, mentioned_users)
                cache_status = "no new events"

            self._trim_prefix_if_needed()

            # --- 2. Build tail (the ONLY dynamic message) ---
            reminder = self._build_reminder(trigger_event)
            situation_context = self._build_situation_context(mentioned_users)
            tail = situation_context
            if reminder:
                tail += "\n\n" + reminder

            # --- 3. Assemble: prefix (frozen) + tail (fresh) ---
            # prefix is passed by reference — _respond_with_tools will append to it
            tail_msg = HumanMessage(content=tail)
            lc_messages = self._cached_prefix + [tail_msg]
            # Save tail for keepalive ping — next ping uses same tail for max cache hit
            self._last_tail_content = tail

        # --- Debug: prefix hash + litellm API-level hash ---
        import hashlib, json as _json

        prefix_hash = hashlib.md5(
            "|||".join(
                f"{type(m).__name__}:{m.content}:{getattr(m, 'tool_calls', '')}"
                for m in self._cached_prefix
            ).encode()
        ).hexdigest()[:8]
        if not hasattr(self, '_last_prefix_hash'):
            self._last_prefix_hash = None
        cache_hit = "HIT" if prefix_hash == self._last_prefix_hash else "MISS"
        self._last_prefix_hash = prefix_hash

        # Litellm interceptor — hash ACTUAL API request to find serialization diffs
        if not hasattr(self, '_litellm_patched'):
            self._litellm_patched = True
            self._prev_api_hash = None
            import litellm
            _orig_completion = litellm.completion
            def _cache_debug_completion(*args, **kwargs):
                msgs = kwargs.get('messages', [])
                tools = kwargs.get('tools', [])
                api_payload = _json.dumps(msgs, sort_keys=True, ensure_ascii=False) + _json.dumps(tools, sort_keys=True, ensure_ascii=False)
                h = hashlib.md5(api_payload.encode()).hexdigest()[:12]
                status = "API_HIT" if h == self._prev_api_hash else "API_MISS"
                print(f"  [LITELLM] hash={h} ({status}) n_msgs={len(msgs)}")
                if h != self._prev_api_hash and self._prev_api_hash and hasattr(self, '_prev_api_msgs'):
                    for j, (pm, cm) in enumerate(zip(self._prev_api_msgs, msgs)):
                        ps = _json.dumps(pm, sort_keys=True, ensure_ascii=False)
                        cs = _json.dumps(cm, sort_keys=True, ensure_ascii=False)
                        if ps != cs:
                            print(f"  [LITELLM DIFF] msg[{j}] role={cm.get('role','')} changed!")
                            print(f"    prev: {ps[:150]}")
                            print(f"    curr: {cs[:150]}")
                            break
                self._prev_api_hash = h
                self._prev_api_msgs = msgs
                return _orig_completion(*args, **kwargs)
            litellm.completion = _cache_debug_completion

        msg_types = [
            f"{type(m).__name__}({len(m.content)})"
            + (f"+tc:{len(m.tool_calls)}" if hasattr(m, 'tool_calls') and m.tool_calls else "")
            for m in lc_messages
        ]
        print(f"\n[CHAT AGENT] Prefix: {len(self._cached_prefix)} msgs ({cache_status})")
        print(f"[CHAT AGENT] Prefix hash: {prefix_hash} ({cache_hit})")
        print(f"[CHAT AGENT] Message types: {msg_types}")
        print("\n[CHAT AGENT] === PROMPT ===")
        print_messages(lc_messages)

        # --- 4. LLM call ---
        self._stop_keepalive()  # pause pinging during actual LLM request
        smart_event_waiter = SmartEventWaiter(ctx_handler=self.ctx_handler)
        self.ctx_swarm["fx_queue"].put("thinking")

        if self._tools_supported:
            final_text = self._respond_with_tools(
                lc_messages, smart_event_waiter,
                refresh_context=lambda: HumanMessage(
                    content=self._build_situation_context(mentioned_users) + "\n\n" + reminder
                ),
            )
        else:
            final_text = self._respond_plain_text(lc_messages, smart_event_waiter)

        smart_event_waiter.shutdown()
        self._start_keepalive()  # resume pinging to keep cache warm

        elapsed_total = time.time() - t_total
        print(f"\n[CHAT AGENT ⏱️ {elapsed_total:.2f}s total] Final response ({len(final_text)} chars): {final_text}")
        return final_text

    def _respond_with_tools(self, lc_messages: List[BaseMessage], event_waiter: SmartEventWaiter, refresh_context=None) -> str:
        """Response path: LLM text = speech, tool calls = game actions.

        IMPORTANT: appends AI/Tool messages to self._cached_prefix so they
        become part of the frozen prefix for the next call. lc_messages is
        _cached_prefix + [tail] — we rebuild it each round by re-appending tail.
        """
        final_text = ""
        tool_round = 0
        # The tail is always the last message in lc_messages
        tail_msg = lc_messages[-1]

        try:
            while tool_round < MAX_TOOL_ROUNDS:
                tool_round += 1
                if refresh_context is not None:
                    tail_msg = refresh_context()
                # Rebuild lc_messages: frozen prefix + tail
                lc_messages = self._cached_prefix + [tail_msg]

                with _timer(f"LLM stream (tool round {tool_round})"):
                    response_text, tool_calls, ai_message, interrupted = self._stream_response(
                        lc_messages, event_waiter, use_tools=True
                    )

                # If interrupted with too little text — discard and retry
                if interrupted and len(response_text.strip()) < MIN_RESPONSE_LENGTH:
                    print(f"[CHAT AGENT] Discarding short interrupted response ({len(response_text.strip())} chars), retrying...")
                    continue

                # Append AI response to cached prefix (frozen for future calls)
                self._cached_prefix.append(ai_message)

                # Collect text from this round
                if response_text.strip():
                    final_text = response_text

                if not tool_calls:
                    break

                print(f"\n[CHAT AGENT] Executing {len(tool_calls)} tool call(s)...")
                tool_failed = False
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc.get("args", {})
                    tool_id = tc.get("id", tool_name)

                    tools_config._current_tool_call_id = tool_id
                    with _timer(f"tool {tool_name}"):
                        print(f"  -> {tool_name}({tool_args})")
                        continuation = getattr(self, "_minecraft_procedure", None)
                        if continuation is not None and continuation.blocks_task_start(tool_name, tool_args):
                            result = ({"status": "procedure_blocked", "hint": "Route recovery budget exhausted or native stop not confirmed. Report the blockage; wait for a new operator request."}
                                      if continuation.navigation_exhausted else
                                      {"status": "waiting_for_game_task", "hint": "One native task is already queued for this procedure. Do not overwrite it; continue after completion."})
                        else:
                            result = _execute_tool_call(
                                self.tool_bank, tool_name, tool_args,
                                game=getattr(self, "ctx_swarm", {}).get("game", {}),
                            )
                        observed_result = result
                        if tool_name == "manage_trees" and isinstance(result, dict) and result.get("task_id"):
                            game = getattr(self, "ctx_swarm", {}).get("game", {})
                            saved = deepcopy(dict(game.get("tree_task", {}).items()))
                            if saved:
                                saved["priority_source"] = "player" if getattr(getattr(self, "_player_inbox", None), "active", None) else saved.get("priority_source", "background")
                                game["tree_task"] = saved
                        if tool_name == "manage_trees" and isinstance(result, str) and result.startswith("Error"):
                            saved = getattr(self, "ctx_swarm", {}).get("game", {}).get("tree_task", {})
                            if saved.get("status") == "active":
                                observed_result = tree_task_result(dict(saved.items()), "in_progress")
                        if tool_name == "manage_trees" and isinstance(observed_result, dict) and observed_result.get("task_id"):
                            if continuation is None or continuation.done or (getattr(continuation, "batch_id", None) not in (None, observed_result["task_id"])):
                                continuation = ProcedureContinuation(observed_result["goal"])
                                self._minecraft_procedure = continuation
                            continuation.batch_id = observed_result["task_id"]
                        if tool_name == "agent_action" and result is True and tool_args.get("action", "").strip().removeprefix("@").split()[:1] in (["stop"], ["cancel"]):
                            cancel_task(getattr(self, "ctx_swarm", {}).get("game", {}))
                            autonomy = getattr(self, "_autonomy", None)
                            if autonomy is not None:
                                autonomy.end_activity("Current native task was stopped")
                                game = getattr(self, "ctx_swarm", {}).get("game", {})
                                game["autonomy"] = autonomy.snapshot(game)
                        if continuation is not None:
                            continuation.observe(tool_name, tool_args, observed_result)
                        if result is False or (isinstance(result, str) and result.startswith("Error")):
                            tool_failed = True
                        print(f"  <- {result}")
                    tools_config._current_tool_call_id = None

                    self._cached_prefix.append(ToolMessage(content=str(result), tool_call_id=tool_id))

                # Give the model actual observations/errors before its final spoken answer.
                # A voice preamble does not prove that the tool call worked.
                if tool_failed:
                    final_text = ""

                if event_waiter.check():
                    print("[CHAT AGENT] Interrupted by new event")
                    break

                # After tool results, Qwen requires HumanMessage before next assistant turn
                self._cached_prefix.append(
                    HumanMessage(content="Read the tool results above. Correct rejected arguments; use observations for the next action. "
                                 "Do not repeat an accepted movement command while it is active. "
                                 "Queue acceptance does not confirm completion. Continue or finish with voice *emotion*.")
                )

        except Exception as e:
            print(f"\n[CHAT AGENT] Tool calling error: {e}")
            traceback.print_exc()
            print("[CHAT AGENT] Falling back to plain text mode...")
            lc_messages = self._cached_prefix + [tail_msg]
            final_text = self._respond_plain_text(lc_messages, event_waiter)
            return final_text

        # Send text output as speech
        if final_text.strip():
            self._send_as_speech(final_text)

        return final_text

    def _respond_plain_text(self, lc_messages: List[BaseMessage], event_waiter: SmartEventWaiter) -> str:
        """Fallback: plain text response, sent as speech.
        Does NOT append AI response to prefix (no tool loop to continue)."""
        for attempt in range(MAX_INTERRUPTS_PER_CALL + 1):
            try:
                with _timer(f"LLM stream (plain text, attempt {attempt + 1})"):
                    response_text, _, ai_message, interrupted = self._stream_response(
                        lc_messages, event_waiter, use_tools=False
                    )
                if interrupted and len(response_text.strip()) < MIN_RESPONSE_LENGTH:
                    print(f"[CHAT AGENT] Discarding short interrupted response, retrying...")
                    continue
            except Exception as e:
                print(f"\n[CHAT AGENT] Plain text error: {e}")
                traceback.print_exc()
                return ""

            # Append AI response to prefix for cache continuity
            if ai_message and ai_message.content:
                self._cached_prefix.append(ai_message)

            if response_text.strip():
                self._send_as_speech(response_text)
            return response_text

        return ""

    def _send_as_speech(self, text: str):
        """Parse *emotion* from text, clean up, send to TTS only.

        Chat is handled separately by the LLM via chat tool call.
        """
        try:
            import re
            from agent.tools.base_tools import _parse_emotion, voice_send

            emotion, clean_text = _parse_emotion(text)

            # Validate emotion — if not in allowed set, default to neutral
            if not emotion or emotion not in SpeakEmotions:
                if emotion:
                    print(f"[CHAT AGENT] Invalid emotion '{emotion}', using neutral")
                emotion = "neutral"

            # Strip roleplay markers like *walks away*, *follows player*
            clean_text = re.sub(r"\*[^*]{2,}\*", "", clean_text)

            # Strip command-like lines
            lines = clean_text.split("\n")
            speech_lines = [l for l in lines if not l.strip().startswith(("@", "/", "!"))]
            clean_text = "\n".join(speech_lines).strip()

            # Collapse multiple whitespace/newlines
            clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
            clean_text = clean_text.strip()

            if not clean_text:
                return
            print(f"[CHAT AGENT] 🟢 TTS: {clean_text} *{emotion}*")
            # NOTE: no _add_tool_run_log here — AI response is already in _cached_prefix.
            # Logging speak to ctx_chat would create a duplicate AIMessage in prefix.
            voice_send([{"text": clean_text, "emotion": emotion}])
        except Exception as e:
            print(f"[CHAT AGENT] Speech send error: {e}")
            traceback.print_exc()

    def _stream_response(
        self,
        messages: List[BaseMessage],
        event_waiter: SmartEventWaiter,
        use_tools: bool = True,
    ) -> tuple:
        """Stream LLM response. Returns (text, tool_calls, ai_message).

        Interrupt logic:
        - Checks event_waiter per chunk, but respects cooldown and max count
        - If interrupted too early (< MIN_RESPONSE_LENGTH chars), the text
          is discarded by the caller so it doesn't pollute conversation history
        """
        response_text = ""
        tool_calls = []
        chunks = []
        was_interrupted = False

        llm = self.llm_with_tools if use_tools else self.llm

        print("[CHAT AGENT] ", end="", flush=True)
        t_first_token = time.time()
        got_first_token = False

        stream_gen = None
        try:
            stream_gen = llm.stream(messages, max_tokens=MAX_RESPONSE_TOKENS)
            for chunk in stream_gen:
                if not got_first_token:
                    ttft = time.time() - t_first_token
                    print(f"[TTFT: {ttft:.2f}s] ", end="", flush=True)
                    got_first_token = True

                chunks.append(chunk)

                if chunk.content:
                    print(chunk.content, end="", flush=True)
                    response_text += chunk.content
                elif use_tools and hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    for tc in chunk.tool_call_chunks:
                        if tc.get("name"):
                            print(f"\n[→ {tc['name']}] ", end="", flush=True)
                        if tc.get("args"):
                            print(tc["args"], end="", flush=True)

                # Check interrupt — only if cooldown allows
                if self._can_interrupt() and event_waiter.check():
                    self._record_interrupt()
                    was_interrupted = True
                    print(f"\n[CHAT AGENT] Generation interrupted ({len(response_text)} chars so far)")
                    break

            if not got_first_token:
                elapsed = time.time() - t_first_token
                print(f"[NO TOKENS after {elapsed:.2f}s]", flush=True)

            print()

        except Exception as e:
            print(f"\n[CHAT AGENT] Stream error: {e}")
            traceback.print_exc()
            if getattr(self.llm, "_llm_type", "") == "saruman-codex":
                # A second generation can spend the subscription allowance twice.
                return "", [], AIMessage(content=""), False
            # Try non-streaming invoke as last resort
            try:
                print("[CHAT AGENT] Trying non-streaming invoke...")
                with _timer("LLM invoke fallback"):
                    response = llm.invoke(messages)
                response_text = response.content or ""
                print(f"[CHAT AGENT] {response_text}")
                if hasattr(response, "tool_calls") and response.tool_calls:
                    tool_calls = response.tool_calls
                return response_text, tool_calls, response, False
            except Exception as e2:
                print(f"[CHAT AGENT] Invoke also failed: {e2}")
                traceback.print_exc()
                return "", [], AIMessage(content=""), False
        finally:
            # CRITICAL: drain remaining stream chunks before closing.
            # LM Studio only saves KV cache when stream is fully consumed.
            # Breaking early without draining = cache miss on next request (~2.5s penalty).
            if stream_gen is not None:
                try:
                    for _ in stream_gen:
                        pass  # discard remaining chunks
                except Exception:
                    pass

        # Merge chunks
        ai_message = self._merge_chunks(chunks, response_text)
        if use_tools and hasattr(ai_message, "tool_calls") and ai_message.tool_calls:
            tool_calls = ai_message.tool_calls

        return response_text, tool_calls, ai_message, was_interrupted

    def _merge_chunks(self, chunks: list, response_text: str) -> AIMessage:
        """Merge streamed chunks into a single AIMessage (not AIMessageChunk).
        AIMessage is required for proper serialization in cached prefix."""
        if not chunks:
            return AIMessage(content=response_text)
        try:
            merged = chunks[0]
            for chunk in chunks[1:]:
                merged = merged + chunk
            # Convert AIMessageChunk → AIMessage for stable serialization
            tc = getattr(merged, "tool_calls", None)
            if tc:
                return AIMessage(content=merged.content or "", tool_calls=tc)
            return AIMessage(content=merged.content or response_text)
        except Exception:
            return AIMessage(content=response_text)

    def _to_langchain_messages(self, messages: List[dict]) -> List[BaseMessage]:
        result = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                result.append(SystemMessage(content=content))
            elif role == "assistant":
                result.append(AIMessage(content=content))
            else:
                result.append(HumanMessage(content=content))
        return result

    def _pending_directive(self):
        for event in self.ctx_handler.get_ctx_chat(validate=True, limit=100):
            if event.type == "directive" and event.processing_timestamp > self._last_directive_ts:
                return event
        return None

    def _collect_players(self):
        inbox = getattr(self, "_player_inbox", None)
        if inbox is None:
            inbox = self._player_inbox = PlayerInbox()
        handler = getattr(self, "ctx_handler", None)
        if handler is not None:
            inbox.collect(handler.get_ctx_chat(validate=True, limit=100))
        return inbox

    def _begin_player_turn(self, event):
        inbox = getattr(self, "_player_inbox", None) or PlayerInbox()
        self._player_inbox = inbox
        inbox.seen.add(event.processing_timestamp)
        inbox.handled.add(event.processing_timestamp)
        inbox.pending = [e for e in inbox.pending if e.processing_timestamp != event.processing_timestamp]
        inbox.active = event
        game = self.ctx_swarm.get("game", {})
        if using_mods(game):
            context = dict(game.get("mod_player_context", {}).items()) if event.additional_fields.get("mod_goal_id") else {
                "id": str(event.processing_timestamp), "user": event.user, "source": "operator" if event.type == "directive" else "player"}
            game["mod_player_context"] = {**context, "replying": True, "until": time.time()+600}
            game["mod_player_pending_until"] = 0
            self._minecraft_procedure = None
            return
        autonomy = getattr(self, "_autonomy", None)
        if autonomy is None:
            autonomy = self._autonomy = Autonomy()
        stop_in_progress = bool((autonomy.job or {}).get("cancel_requested") or (autonomy.equipment_job or {}).get("cancel_requested"))
        owned = autonomy.yield_to_player(event.user, event.msg)
        game = self.ctx_swarm.get("game", {})
        saved = game.get("tree_task", {})
        if saved.get("status") == "active":
            deferred = deepcopy(list(game.get("deferred_goals", [])))
            if not any(row.get("task", {}).get("id") == saved.get("id") for row in deferred):
                deferred.append({"task": deepcopy(dict(saved.items())),
                                 "search": deepcopy(dict(game.get("tree_search", {}).items()))})
            game["deferred_goals"] = deferred
            game["tree_task"] = {**deepcopy(dict(saved.items())), "status": "paused", "pause_reason": "Player priority"}
            self._minecraft_procedure = None
            owned = True
        awaiting_stop = bool(game.get("player_priority", {}).get("awaiting_stop"))
        if owned:
            queued = self.ctx_swarm.get("chat_queue", [])
            pending_native = any(isinstance(cmd, str) and cmd.startswith(("@goto", "@get")) for cmd in queued)
            if game.get("hasActiveTask") or pending_native:
                if not stop_in_progress:
                    _execute_tool_call(self.tool_bank, "agent_action", {"action": "@stop"}, game=game)
                awaiting_stop = True
        inbox.publish(game, status="replying", awaiting_stop=awaiting_stop)

    def _end_player_turn(self):
        inbox = self._player_inbox
        game = self.ctx_swarm.get("game", {})
        if using_mods(game):
            context = dict(game.get("mod_player_context", {}).items())
            context.update(replying=False, until=time.time()+600)
            game["mod_player_context"] = context
            inbox.active = None
            return
        continuation = getattr(self, "_minecraft_procedure", None)
        pending = game.get("hasActiveTask") or (continuation and continuation.pending and not continuation.done)
        inbox.active = None
        status = ("waiting_for_task_stop" if game.get("player_priority", {}).get("awaiting_stop") else
                  "waiting_for_player_task" if pending else "answered")
        inbox.publish(game, status=status)
        self._autonomy.release_player()

    def _resume_deferred_goal(self, autonomy, game):
        continuation = getattr(self, "_minecraft_procedure", None)
        saved = game.get("tree_task", {})
        if (not autonomy.enabled or autonomy.paused or autonomy.clock() < autonomy.player_until or game.get("hasActiveTask")
                or game.get("survival", {}).get("active")
                or game.get("self_defense", {}).get("active")
                or not game.get("ingame") or "alive" in game and game.get("alive") is not True
                or saved.get("status") == "active"
                or saved.get("resume_world") is not None and saved.get("resume_world") != game.get("world_session")
                or saved.get("status") == "paused" and saved.get("pause_reason") not in ("Player priority", "Self defense", "Water rescue")
                or continuation and continuation.pending and not continuation.done):
            return
        deferred = deepcopy(list(game.get("deferred_goals", [])))
        if not deferred:
            return
        eligible = [i for i, row in enumerate(deferred)
                    if (row["task"].get("status") == "active"
                        or row["task"].get("pause_reason") in ("Player priority", "Self defense", "Water rescue"))
                    and (row["task"].get("resume_world") is None or row["task"].get("resume_world") == game.get("world_session"))]
        if not eligible:
            return
        index = next((i for i in eligible if deferred[i]["task"].get("priority_source") == "player"), eligible[0])
        resume = deferred.pop(index)
        task = resume["task"]
        task.update(status="paused", pause_reason="Player priority")
        if saved and saved.get("id") != task.get("id"):
            history = deepcopy(list(game.get("tree_task_history", [])))
            game["tree_task_history"] = (history + [dict(saved.items())])[-20:]
        game["tree_task"] = task
        game["tree_search"] = resume["search"]
        resume_paused(task)
        game["tree_task"] = task
        game["deferred_goals"] = deferred
        self._minecraft_procedure = None

    def _sync_player_lifecycle(self):
        if using_mods(getattr(self, "ctx_swarm", {}).get("game", {})):
            return
        game = getattr(self, "ctx_swarm", {}).get("game", {})
        if game.get("combat_runtime") != "reflex-v2" and game.get("self_defense", {}).get("active") and not any(
                proof.get("until", 0) > time.time() for proof in game.get("combat_threats", {}).values()):
            game["self_defense"] = {"active": False, "status": "threat_expired"}
        if game.get("combat_runtime") != "reflex-v2" and game.get("self_defense", {}).get("active") and getattr(self, "tool_bank", {}).get("defend_self", {}).get("disabled", False):
            game["self_defense"] = {"active": False, "status": "tool_disabled"}
        rescuing = bool(game.get("survival", {}).get("active"))
        interruption = game.get("survival", {}).get("interruption_id")
        if (rescuing and not getattr(self, "_water_rescue_seen", False)
                or interruption and interruption != getattr(self, "_water_interruption", None)):
            self._minecraft_procedure = None
            autonomy = getattr(self, "_autonomy", None)
            if autonomy is not None:
                autonomy.yield_to_combat()
        self._water_interruption = interruption
        self._water_rescue_seen = rescuing
        combat_interruption = game.get("self_defense", {}).get("interruption_id")
        if combat_interruption and combat_interruption != getattr(self, "_combat_interruption", None):
            self._combat_interruption = combat_interruption
            self._minecraft_procedure = None
            autonomy = getattr(self, "_autonomy", None)
            if autonomy is not None and autonomy.job:
                autonomy.yield_to_combat()
        death_id = game.get("respawn", {}).get("death_id")
        if death_id is not None and death_id != getattr(self, "_last_death_id", None):
            self._last_death_id = death_id
            self._minecraft_procedure = None
            autonomy = getattr(self, "_autonomy", None)
            if autonomy is not None:
                autonomy.end_activity("Player died; recheck position and inventory after respawn")

    def _background_step(self):
        if using_mods(getattr(self, "ctx_swarm", {}).get("game", {})):
            return False
        self._sync_player_lifecycle()
        registry = getattr(self, "tool_bank", {})
        game = getattr(self, "ctx_swarm", {}).get("game", {})
        if game.get("combat_runtime") == "reflex-v2" and game.get("self_defense", {}).get("active"):
            self._minecraft_procedure = None
            autonomy = getattr(self, "_autonomy", None)
            if autonomy is not None and autonomy.job:
                autonomy.yield_to_combat()
            result = game["self_defense"].get("result", {})
            target_uuid = game["self_defense"].get("target", {}).get("uuid")
            if result.get("status") in ("cast_requested", "attacked", "killed") and target_uuid != getattr(self, "_defense_spoken_target", None):
                self._defense_spoken_target = target_uuid
                self._send_as_speech("Твоё нападение не останется без ответа. *angry*")
            return True
        if (game.get("combat_runtime") != "reflex-v2" and registry.get("defend_self") and not registry["defend_self"].get("disabled", False)
                and time.monotonic() >= getattr(self, "_defense_check_at", 0)
                and (game.get("self_defense", {}).get("active") or any(proof.get("until", 0) > time.time() for proof in game.get("combat_threats", {}).values()))):
            self._defense_check_at = time.monotonic() + 0.5
            result = _execute_tool_call(registry, "defend_self", {}, game=game)
            if isinstance(result, dict) and result.get("status") not in ("no_threat", "disconnected", "waiting_for_survival"):
                self._minecraft_procedure = None
                autonomy = getattr(self, "_autonomy", None)
                if autonomy is not None and autonomy.job:
                    autonomy.yield_to_combat()
                target_uuid = game.get("self_defense", {}).get("target", {}).get("uuid")
                if result.get("status") in ("cast_requested", "attacked", "killed") and target_uuid != getattr(self, "_defense_spoken_target", None):
                    self._defense_spoken_target = target_uuid
                    self._send_as_speech("Твоё нападение не останется без ответа. *angry*")
                if game.get("self_defense", {}).get("active"):
                    if autonomy is None:
                        autonomy = self._autonomy = Autonomy()
                    game["autonomy"] = {**autonomy.snapshot(game), "result": result,
                                       "action": {"tool": "defend_self", "arguments": {}, "started_at": time.time()}}
                    print(f"[SELF DEFENSE] {result}")
                    return True
        if self._collect_players().pending:
            self._player_inbox.publish(self.ctx_swarm.get("game", {}), status="queued")
            return True
        autonomy = getattr(self, "_autonomy", None)
        if autonomy is None:
            autonomy = self._autonomy = Autonomy()
        game = getattr(self, "ctx_swarm", {}).get("game", {})
        self._resume_deferred_goal(autonomy, game)
        registry = getattr(self, "tool_bank", {})
        continuation = getattr(self, "_minecraft_procedure", None)
        procedure_pending = bool(continuation and continuation.pending and not continuation.done)

        def execute(name, args):
            state = autonomy.snapshot(game, procedure_pending=procedure_pending)
            state["action"] = {"tool": name, "arguments": args, "started_at": time.time()}
            game["autonomy"] = state
            record = registry.get(name)
            if record is None or record.get("disabled", False):
                return {"status": "tool_unavailable", "tool": name,
                        "detail": f"Инструмент {name} выключен или не зарегистрирован"}
            return _execute_tool_call(self.tool_bank, name, args, game=game)

        if not getattr(autonomy, "_speech_catalogue_loaded", False):
            records = getattr(getattr(tools_config, "db", None), "action_voice_records", None)
            if isinstance(records, list):
                autonomy.speech.replace(records)
                autonomy.speech.source = "postgresql"
            autonomy._speech_catalogue_loaded = True
        outcome = autonomy.tick(
            game, execute, procedure_pending=procedure_pending,
            available_tools={name for name, record in registry.items() if not record.get("disabled", False)},
        )
        game["autonomy"] = autonomy.snapshot(
            game, procedure_pending=procedure_pending
        )
        if not outcome:
            return False
        for name, arguments, result in outcome["calls"]:
            print(f"[AUTONOMY] {name}({arguments}) -> {result}")
            if name == "manage_trees" and isinstance(result, dict) and result.get("task_id"):
                if continuation is None or continuation.done:
                    continuation = self._minecraft_procedure = ProcedureContinuation(result["goal"])
                continuation.batch_id = result["task_id"]
            if continuation is not None:
                continuation.observe(name, arguments, result)
        if outcome.get("speech"):
            emotion = "angry" if outcome["result"].get("status") in ("attacked", "killed") else "neutral"
            self._send_as_speech(outcome["speech"] + f" *{emotion}*")
        return True

    def resume_background(self):
        if getattr(self, "_autonomy", None) is not None:
            self._autonomy.directive("Resume after Start")

    def step(self):
        try:
            self._sync_player_lifecycle()
            tracker = self.ctx_handler.activity_tracker
            t_wait_start = time.time()
            activity_level = tracker.level
            print(f"[CHAT AGENT] Waiting for trigger... ({tracker.to_prompt_string()})")

            def extended_wait_check(event: CtxEventBase) -> bool:
                # Unconditional: mentions and directives always wake
                if is_player_message(event) or nick_mention_check(event, SELF_NAME):
                    return True
                if interrupt_event_check(event):
                    return True
                if using_mods(getattr(self, "ctx_swarm", {}).get("game", {})):
                    if event.additional_fields.get("mod_goal_id"):
                        return True
                    if event.env == "minecraft" and event.type in {"chat", "mc_executor_event", "mc_damaged"}:
                        return False  # Native telemetry is not an invitation to plan actions.

                # Queue backlog (existing logic)
                def check_timeout():
                    return time.time() - t_wait_start > 30.0
                if len(self.ctx_handler.ctx_swarm["chat_queue"]) > 1 and not check_timeout():
                    return False
                if len(self.ctx_handler.ctx_swarm["tts_queue"]) > 1 and not check_timeout():
                    return False

                level = tracker.level

                if level == "NO_ACTIVITY":
                    # Starved for input — accept anything
                    return True

                if level == "HIGH_ACTIVITY":
                    # Only kills/deaths (mentions already handled above)
                    return event.type in ("kill", "death")

                # LOW_ACTIVITY — default + flood suppression
                if event.type == "chat" and not event.user:
                    return not tracker.is_flood("server_msg")
                if event.type == "mc_damaged":
                    return not tracker.is_flood("damage")

                return default_event_check(event)

            # Shorten timeout when approaching idle self-activation
            remaining_to_idle = max(0, IDLE_SELF_ACTIVATE - tracker.idle_seconds)
            if remaining_to_idle < 15.0:
                wait_timeout = min(15.0, remaining_to_idle + 1.0)
            else:
                wait_timeout = 15.0

            event = self._pending_directive()
            if event is None:
                event = self._collect_players().next()
            continuation = getattr(self, "_minecraft_procedure", None)
            if event is None:
                game = getattr(self, "ctx_swarm", {}).get("game", {})
                navigation = game.get("navigation", {})
                failure = (navigation.get("status") in ("blocked", "rejected", "stop_failed")
                           and navigation.get("event_id") != getattr(self, "_last_navigation_failure", None))
                message = None
                if failure:
                    self._last_navigation_failure = navigation["event_id"]
                    message = (continuation.navigation_failed(navigation) if continuation is not None
                               else "Minecraft navigation failed: " + str(navigation)
                               + ". Check actual inventory/terrain, do not repeat this destination. Report a concrete blockage or use another observed safe approach.")
                elif continuation is not None:
                    message = continuation.poll(game.get("hasActiveTask", False), game.get("collection"))
                if continuation is not None and continuation.batch and continuation.done:
                    saved = game.get("tree_task", {})
                    if saved.get("status") == "active":
                        stopped = dict(saved.items())
                        stopped["status"] = "blocked"
                        stopped["reason"] = message or "Continuation stopped"
                        game["tree_task"] = stopped
                if message:
                    event = CtxEventBase(msg=message, user="", date="", processing_timestamp=time.time_ns(),
                                         type="system", env="minecraft",
                                         additional_fields={"filter_results": {"acceptable": True}})
                if continuation is not None and continuation.pending:
                    wait_timeout = min(wait_timeout, 0.5)
            if event is None:
                if self._background_step():
                    return
                wait_timeout = min(wait_timeout, 0.5 if using_mods(getattr(self, "ctx_swarm", {}).get("game", {})) else self._autonomy.wait_time(), 0.5)
                event = self.ctx_handler.wait_for_sync(
                    check=extended_wait_check, timeout=wait_timeout, raise_timeout=False
                )

            wait_elapsed = time.time() - t_wait_start
            if event:
                if event.type == "directive":
                    if event.processing_timestamp <= self._last_directive_ts:
                        return  # A delayed watcher notification for an already handled directive.
                    self._last_directive_ts = max(
                        self._last_directive_ts, event.processing_timestamp
                    )
                print(f"[CHAT AGENT ⏱️ waited {wait_elapsed:.1f}s] Triggered by: {event.user}: {event.msg[:80]}")
                native_completion = (using_mods(getattr(self, "ctx_swarm", {}).get("game", {}))
                    and event.additional_fields.get("mod_goal_id")
                    and event.additional_fields.get("mod_context_id") == self.ctx_swarm["game"].get("mod_player_context", {}).get("id")
                    and any(g["id"] == event.additional_fields["mod_goal_id"] for g in GoalStore().list()))
                player_turn = is_player_message(event) or (using_mods(getattr(self, "ctx_swarm", {}).get("game", {})) and event.type == "directive") or native_completion
                if player_turn:
                    if event.processing_timestamp in self._collect_players().handled:
                        return  # A delayed watcher notification for a handled player message.
                    self._begin_player_turn(event)
                try:
                    self.respond(event)
                finally:
                    if player_turn:
                        self._end_player_turn()
            elif (not using_mods(getattr(self, "ctx_swarm", {}).get("game", {})) and tracker.idle_seconds >= IDLE_SELF_ACTIVATE
                  and not getattr(getattr(self, "_autonomy", None), "enabled", False)):
                print(f"[CHAT AGENT ⏱️ waited {wait_elapsed:.1f}s] Idle self-activation ({tracker.idle_seconds:.0f}s idle)")
                self._handle_idle_activation()
            else:
                if time.monotonic() - getattr(self, "_last_no_trigger_log", 0) >= 15:
                    self._last_no_trigger_log = time.monotonic()
                    print(f"[CHAT AGENT ⏱️ waited {wait_elapsed:.1f}s] No trigger (timeout, {activity_level})")

        except Exception as e:
            print(f"[CHAT AGENT] Error: {e}")
            traceback.print_exc()

    def _handle_idle_activation(self):
        """Self-activate when nothing has happened for a while."""
        idle_event = CtxEventBase(
            msg="[Nothing is happening. You are idle and bored.]",
            user="",
            date="",
            processing_timestamp=time.time_ns(),
            type="system",
            env="minecraft",
            additional_fields={
                "filter_results": {"acceptable": True},
                "idle_trigger": True,
            },
        )
        self.ctx_handler.activity_tracker.reset_idle()
        self.respond(idle_event)

    def run(self):
        print("[CHAT AGENT] Starting...")
        self.resume_background()
        self.running = True
        while self.ctx_swarm["env"]["actived"] and self.running:
            self.step()

    def stop(self):
        cancel_task(getattr(self, "ctx_swarm", {}).get("game", {}))
        if getattr(self, "_autonomy", None) is not None:
            self._autonomy.pause()
        self._minecraft_procedure = None
        self.running = False
        print("[CHAT AGENT] Stopped")

    def cleanup(self):
        if self.running:
            self.stop()
        if hasattr(self, "llm"):
            del self.llm
