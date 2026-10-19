"""Message builder for ChatAgent — native langchain message format.

Builds conversation history using native langchain types:
- External events → HumanMessage (batched)
- Own speak (voice) → AIMessage with text content
- Own tool calls → AIMessage with tool_calls + ToolMessage for results
- Strict user/assistant alternation via merging

Used by ChatAgent.
"""

import copy
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from config_schema.general import get_name
from data_schema.chat_structures import EventBase, MentionedUser
from utils.event_format import format_absolute_time_event, format_relative_time_event, timestamp_to_datetime
from utils.event_pipeline import filter_spam_events, limit_events, merge_user_events

SELF_NAME = get_name()

MAX_EVENTS = 30


def _track_user(event: EventBase, mentioned_users: Dict[str, MentionedUser]):
    """Track user mentions from event."""
    user = event.get("user", "")
    if not user:
        return
    if user not in mentioned_users:
        mentioned_users[user] = MentionedUser(
            {"name": user, "found_count": 0, "binded_events": []}
        )
    mu = mentioned_users[user]
    mu["binded_events"].append(event)
    mu["found_count"] = mu["found_count"] + 1
    mu["last_activity_timestamp"] = event.get("processing_timestamp", -1)
    mu["last_activity_string"] = format_relative_time_event(event)


def _format_event_line(event: EventBase, absolute_time: bool = False) -> str:
    """Format a single event into a concise one-line string.

    Args:
        absolute_time: if True, use HH:MM:SS (stable for cache). False = relative (5.2m ago).
    """
    ev = event.copy()
    env = ev.get("env", "").upper()
    ev_type = ev.get("type", "")
    user = ev.get("user", "")
    msg = ev.get("msg", "")
    timestamp = format_absolute_time_event(ev) if absolute_time else format_relative_time_event(ev)

    if ev.get("isTriggerEvent", False):
        timestamp = "WAKED YOU UP, " + timestamp

    if ev_type == "voice":
        label = f"MC VOICE ({env})" if env else "VOICE"
    elif ev_type and ev_type not in ("chat",):
        label = ev_type.upper()
    elif env:
        label = f"{env} CHAT"
    else:
        label = "CHAT"

    if ev.get("isEvent") and ev.get("description"):
        content = ev["description"]
    elif env == "DONATION":
        summ = ev.get("summ", 0)
        desc = ev.get("description", "")
        content = f"donated {summ}$"
        if desc:
            content = desc
        if msg:
            content += f', message: "{msg}"'
    else:
        content = msg

    if not user:
        user = "(server)"
    if ev_type == "voice":
        content += " [speech-to-text, may be inaccurate]"

    repeat = ev.get("repeat", 0)
    repeat_str = f" (x{repeat})" if repeat > 1 else ""

    return f"{label} [{timestamp}] {user}: {content}{repeat_str}"


def _format_self_speak(event: EventBase) -> str:
    """Format own speak tool call as voice text."""
    msg = event.get("msg", "")
    args = event.get("args", {})
    if isinstance(args, dict):
        comment = args.get("comment", args.get(0, msg))
        emotion = args.get("emotion", "")
    else:
        comment = msg
        emotion = ""
    if emotion:
        return f"{comment} *{emotion}*"
    return comment


def _is_self_event(event: EventBase, self_name: str) -> bool:
    """Check if event is our own action."""
    user = event.get("user", "")
    if user != self_name:
        return False
    ev_type = event.get("type", "")
    if ev_type == "tool_call" and event.get("self"):
        return True
    if ev_type in ("chat", "mc_chat") and user == self_name:
        return True
    if ev_type == "OVERRIDE_ASSISTANT_ROLE":
        return True
    return False


def _categorize_time(event: EventBase) -> str:
    """Return 'old', 'relevant', or 'recent' based on event age."""
    try:
        event_dt = timestamp_to_datetime(event)
        diff = datetime.now() - event_dt
        if diff <= timedelta(seconds=10):
            return "recent"
        elif diff <= timedelta(minutes=1):
            return "relevant"
        return "old"
    except Exception:
        return "relevant"


def _make_tool_call_id(event: EventBase) -> str:
    """Get or generate a tool_call_id for an event."""
    tc_id = event.get("tool_call_id")
    if tc_id:
        return str(tc_id)
    # Fallback: use processing_timestamp as stable id
    return f"call_{event.get('processing_timestamp', 0)}"


def build_chat_messages(
    system_prompt: str,
    situation_context: str,
    events: list,
    trigger_event_id: int = -1,
    reminder: str = "",
) -> Tuple[List[BaseMessage], Dict[str, MentionedUser]]:
    """Build native langchain message list for ChatAgent.

    Returns:
        (messages: List[BaseMessage], mentioned_users)

    Message structure:
        SystemMessage: character prompt + output rules
        HumanMessage: situation context
        --- conversation history ---
        HumanMessage: batch of external events
        AIMessage: voice text + tool_calls (native format)
        ToolMessage: tool results
        HumanMessage: next events
        ...
        HumanMessage: latest events + situation + reminder (tail)
    """
    mentioned_users: Dict[str, MentionedUser] = {}

    if not events:
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=situation_context + "\n\n" + reminder),
        ], mentioned_users

    # Preprocess
    this_events = copy.deepcopy(events)
    processed = limit_events(this_events, MAX_EVENTS * 2)
    processed = merge_user_events(processed)
    processed = filter_spam_events(processed, MAX_EVENTS)

    # Mark trigger
    if trigger_event_id != -1:
        for ev in processed:
            if int(ev.get("processing_timestamp", 0)) == int(trigger_event_id):
                ev["isTriggerEvent"] = True
                break

    # --- Build native message list ---
    # We walk through events and group them into proper langchain messages:
    # - External events → accumulate into HumanMessage lines
    # - Self speak → AIMessage(content=voice_text)
    # - Self tool calls → AIMessage(content=voice_text, tool_calls=[...]) + ToolMessage
    # Tool calls that happen "together" (same response) are grouped into one AIMessage.

    messages: List[BaseMessage] = []
    messages.append(SystemMessage(content=system_prompt))

    # Pending human lines (external events)
    human_lines: List[str] = []
    current_time_label = None

    # Pending assistant state: voice text + tool calls from same "turn"
    pending_voice_text = ""
    pending_tool_calls = []  # list of {"name", "args", "id"}
    pending_tool_results = []  # list of (tool_call_id, result_str)

    def _flush_human():
        nonlocal human_lines
        if human_lines:
            messages.append(HumanMessage(content="\n".join(human_lines)))
            human_lines = []

    def _flush_assistant():
        nonlocal pending_voice_text, pending_tool_calls, pending_tool_results
        if not pending_voice_text and not pending_tool_calls:
            return

        # Qwen requires HumanMessage before AIMessage — if none exists yet, insert one
        has_human = any(isinstance(m, HumanMessage) for m in messages)
        if not has_human:
            messages.append(HumanMessage(content="(conversation start)"))

        # Build AIMessage
        ai_content = pending_voice_text.strip() if pending_voice_text else ""
        if pending_tool_calls:
            messages.append(AIMessage(
                content=ai_content,
                tool_calls=pending_tool_calls,
            ))
            # Add ToolMessage for each tool call
            for tc_id, tc_result in pending_tool_results:
                messages.append(ToolMessage(
                    content=str(tc_result) if tc_result else "ok",
                    tool_call_id=tc_id,
                ))
        elif ai_content:
            messages.append(AIMessage(content=ai_content))

        pending_voice_text = ""
        pending_tool_calls = []
        pending_tool_results = []

    for event in processed:
        _track_user(event, mentioned_users)
        is_self = _is_self_event(event, SELF_NAME)

        if is_self:
            ev_type = event.get("type", "")
            tool_name = event.get("tool", "")

            if ev_type == "tool_call" and tool_name == "speak":
                # Speak = voice text. Flush human first.
                # If multiple speaks in a row — keep only the last one
                # (prevents self-reinforcing loops with repeated voice text)
                _flush_human()
                pending_voice_text = _format_self_speak(event)

            elif ev_type == "tool_call" and tool_name:
                # Real tool call — add to pending tool calls
                _flush_human()
                tc_id = _make_tool_call_id(event)
                args = event.get("args", {})
                if not isinstance(args, dict):
                    args = {"input": str(args)}

                pending_tool_calls.append({
                    "name": tool_name,
                    "args": args,
                    "id": tc_id,
                })
                pending_tool_results.append((
                    tc_id,
                    event.get("result", "ok"),
                ))

            elif ev_type == "OVERRIDE_ASSISTANT_ROLE":
                _flush_human()
                msg_text = event.get("msg", "")
                if msg_text:
                    pending_voice_text = msg_text

            else:
                # Own chat echo — skip (represented by tool call)
                continue
        else:
            # External event → flush assistant, add to human lines
            _flush_assistant()

            # Time divider
            time_cat = _categorize_time(event)
            if time_cat != current_time_label:
                labels = {
                    "old": "--- Old context (>1 min ago) ---",
                    "relevant": "--- Recent context (past minute) ---",
                    "recent": "--- Happening now ---",
                }
                human_lines.append(labels.get(time_cat, ""))
                current_time_label = time_cat

            human_lines.append(_format_event_line(event))

    # Flush remaining
    _flush_human()
    _flush_assistant()

    # Add situation context + reminder as the tail (always recomputed, not cached)
    tail = situation_context
    if reminder:
        tail += "\n\n" + reminder

    if messages and isinstance(messages[-1], HumanMessage):
        messages[-1].content += "\n\n" + tail
    else:
        messages.append(HumanMessage(content=tail))

    # Ensure alternation — merge consecutive same-role messages
    messages = _ensure_alternation(messages)

    # Safety: some models (Qwen) require last message to be HumanMessage
    if messages and not isinstance(messages[-1], HumanMessage):
        messages.append(HumanMessage(content="Continue. Respond in-character with voice *emotion*."))

    # Validate: ensure no ToolMessage without preceding AIMessage with tool_calls
    # Some LM Studio templates crash on orphaned ToolMessages
    messages = _validate_tool_messages(messages)

    return messages, mentioned_users


def _validate_tool_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Ensure message sequence is valid for Qwen/LM Studio jinja templates.
    After the last ToolMessage, there must be a HumanMessage."""
    if not messages:
        return messages
    # Find last ToolMessage — if nothing follows it (or only non-Human follows),
    # the template will crash with "No user query found"
    last_tool_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ToolMessage):
            last_tool_idx = i
            break
    if last_tool_idx == -1:
        return messages
    # Check if there's a HumanMessage after the last ToolMessage
    has_human_after = any(
        isinstance(messages[j], HumanMessage)
        for j in range(last_tool_idx + 1, len(messages))
    )
    if not has_human_after:
        messages.append(HumanMessage(content="Tools executed. Continue with voice *emotion*."))
    return messages


def _ensure_alternation(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Merge consecutive same-role messages. Preserves tool_calls and ToolMessages."""
    if not messages:
        return messages

    result: List[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, (SystemMessage, ToolMessage)):
            result.append(msg)
            continue

        # Merge consecutive HumanMessages
        if isinstance(msg, HumanMessage) and result and isinstance(result[-1], HumanMessage):
            result[-1] = HumanMessage(content=result[-1].content + "\n\n" + msg.content)
            continue

        # Merge consecutive AIMessages only if neither has tool_calls
        if isinstance(msg, AIMessage) and result and isinstance(result[-1], AIMessage):
            prev_tc = getattr(result[-1], "tool_calls", None)
            curr_tc = getattr(msg, "tool_calls", None)
            if not prev_tc and not curr_tc:
                result[-1] = AIMessage(content=result[-1].content + "\n\n" + msg.content)
                continue

        result.append(msg)

    return result


# Keep for backward compat
ensure_alternation = _ensure_alternation


def build_events_block(
    events: list,
    trigger_event_id: int = -1,
    max_events: int = MAX_EVENTS,
) -> Tuple[str, str, Dict[str, MentionedUser]]:
    """Legacy helper — extract mentioned_users from events."""
    if not events:
        return "", "", {}

    this_events = copy.deepcopy(events)
    processed = limit_events(this_events, max_events * 2)
    processed = merge_user_events(processed)
    processed = filter_spam_events(processed, max_events)

    mentioned_users: Dict[str, MentionedUser] = {}
    for event in processed:
        _track_user(event, mentioned_users)

    return "", "", mentioned_users


def events_to_messages(
    events: list,
    trigger_event_id: int = -1,
) -> Tuple[List[BaseMessage], Dict[str, MentionedUser]]:
    """Convert a list of events to langchain messages (history only, no system/tail).

    Used for incremental append-only history building.
    Returns (messages, mentioned_users).
    """
    mentioned_users: Dict[str, MentionedUser] = {}
    if not events:
        return [], mentioned_users

    messages: List[BaseMessage] = []
    human_lines: List[str] = []
    current_time_label = None
    pending_voice_text = ""
    pending_tool_calls = []
    pending_tool_results = []

    def _flush_human():
        nonlocal human_lines
        if human_lines:
            messages.append(HumanMessage(content="\n".join(human_lines)))
            human_lines = []

    def _flush_assistant():
        nonlocal pending_voice_text, pending_tool_calls, pending_tool_results
        if not pending_voice_text and not pending_tool_calls:
            return
        ai_content = pending_voice_text.strip() if pending_voice_text else ""
        if pending_tool_calls:
            messages.append(AIMessage(content=ai_content, tool_calls=pending_tool_calls))
            for tc_id, tc_result in pending_tool_results:
                messages.append(ToolMessage(
                    content=str(tc_result) if tc_result else "ok",
                    tool_call_id=tc_id,
                ))
        elif ai_content:
            messages.append(AIMessage(content=ai_content))
        pending_voice_text = ""
        pending_tool_calls = []
        pending_tool_results = []

    # NOTE: no trigger marking here — "WAKED YOU UP" should only appear in tail,
    # not in cached history (it would break cache and confuse LLM on old events)

    for event in events:
        # Skip internal engine spam (Tungsten pathfinder logs, etc.)
        msg = event.get("msg", "")
        if msg and ("§2§l§o[Tungsten]" in msg or "§2§l§oWARNING:" in msg):
            continue

        _track_user(event, mentioned_users)
        is_self = _is_self_event(event, SELF_NAME)

        if is_self:
            ev_type = event.get("type", "")
            tool_name = event.get("tool", "")
            if ev_type == "tool_call" and tool_name == "speak":
                _flush_human()
                pending_voice_text = _format_self_speak(event)
            elif ev_type == "tool_call" and tool_name:
                _flush_human()
                tc_id = _make_tool_call_id(event)
                args = event.get("args", {})
                if not isinstance(args, dict):
                    args = {"input": str(args)}
                pending_tool_calls.append({"name": tool_name, "args": args, "id": tc_id})
                pending_tool_results.append((tc_id, event.get("result", "ok")))
            elif ev_type == "OVERRIDE_ASSISTANT_ROLE":
                _flush_human()
                msg_text = event.get("msg", "")
                if msg_text:
                    pending_voice_text = msg_text
            else:
                continue
        else:
            # External event — no time labels in cached history (they'd break cache)
            # Absolute time (HH:MM:SS) for cached history — stable, won't break cache
            _flush_assistant()
            human_lines.append(_format_event_line(event, absolute_time=True))

    _flush_human()
    _flush_assistant()

    return messages, mentioned_users
