"""Event pipeline — merge, filter, preprocess event lists.

Extracted from format_helper. Contains:
- merge_user_events: merge consecutive same-user messages
- merge_tool_calls: merge consecutive tool calls
- filter_spam_events: remove spam and limit system messages
- limit_events: hard limit on event count
- preprocess_events: full pipeline (limit → merge tools → merge users → filter)
- categorize_events: split events by time (old/relevant/recent)
"""

import traceback
from datetime import datetime, timedelta
from typing import List, Sequence, Tuple

from agent.tools.tool_executor import format_tool_command
from config_schema.general import get_name
from data_schema.chat_structures import EventBase
from data_schema.ctx_structures import CtxChatType
from data_schema.tool_structures import TOOL_COMMAND_FORMATS, ToolCommandFormats
from utils.event_format import timestamp_to_datetime

SELF_NAME = get_name()

EVENTS_LIMIT = 30
MAX_SYSTEM_MSG_PERCENT = 30


def merge_user_events(events: Sequence[EventBase]) -> List[EventBase]:
    """Merge consecutive messages from the same user.

    Args:
        events: should be a FULL DEEPCOPY of initial events
    """
    merged_events = []
    for event in events:
        if not merged_events:
            merged_events.append(event)
            continue
        merging_event = merged_events[-1]
        event_user = event.get("user", False)

        if (
            event_user == merging_event.get("user")
            and event.get("msg")
            and merging_event.get("msg")
            and event.get("type") == merging_event.get("type")
        ):
            merged_event = event
            merged_event["msg"] = merging_event["msg"] + "\n" + event.get("msg", "")
            merged_events[-1] = merged_event
        else:
            merged_events.append(event)
    return merged_events


def merge_tool_calls(
    events: Sequence[EventBase], tool_call_format: ToolCommandFormats
) -> List[EventBase]:
    """Merge consecutive tool calls into single events.

    Args:
        events: should be a FULL DEEPCOPY of initial events
    """
    merged_events: List[EventBase] = []
    merging_tool_call: Tuple[int, bool, EventBase] = None
    for i, event in enumerate(events):
        if event.get("type") == "tool_call":

            if not event.get("formatted_tool_command"):
                result = format_tool_command(
                    event["tool"], event["args"], tool_call_format
                )
                event["formatted_tool_command"] = result
                if event.get("result"):
                    event["formatted_tool_result"] = (
                        "Tool "
                        + event.get("tool", "")
                        + " returned {"
                        + str(event.get("result", "None"))
                        + "}"
                    )
                else:
                    event["formatted_tool_result"] = ""
                events[i] = event
            # speak tool special handling
            if event.get("tool", "") == "speak":
                if tool_call_format == "text":
                    merged_events.append(
                        EventBase(
                            processing_timestamp=event["processing_timestamp"],
                            type="OVERRIDE_ASSISTANT_ROLE",
                            msg=event.get("formatted_tool_command", ""),
                            user=event["user"],
                            date=event["date"],
                        )
                    )
                    continue
            is_first = False
            if not merged_events:
                merged_events.append(event)
                merging_tool_call = i, True, event
                continue
            if not merging_tool_call:
                merging_tool_call = i, True, event
            if merging_tool_call[1]:
                merging_event = merged_events[-1]
            else:
                is_first = True
                merging_event = event

            if (
                event.get("user") == merging_event.get("user")
                and event["type"] == merging_event.get("type")
                and not is_first
            ):
                event["multiple_tool_call"] = True
                merged_event = event
                try:
                    merged_event["formatted_tool_command"] = (
                        merging_event["formatted_tool_command"]
                        + TOOL_COMMAND_FORMATS[tool_call_format][
                            "multiple_tool_separator"
                        ]
                        + event["formatted_tool_command"]
                    )
                    if event["formatted_tool_result"]:
                        merged_event["formatted_tool_result"] = (
                            merging_event["formatted_tool_result"]
                            + "; "
                            + event["formatted_tool_result"]
                        )
                except KeyError as e:
                    print("[EVENT PIPELINE] formatted_tool_command KeyError: ", e)
                    traceback.print_exc()
                    merged_event["formatted_tool_command"] = format_tool_command(
                        merged_event["tool"], merged_event["args"], tool_call_format
                    )
                if merging_tool_call[1]:
                    merged_events[-1] = merged_event
                else:
                    merged_events.append(merged_event)
            else:
                merged_events.append(event)
            merging_tool_call = i, True, event
        else:
            merged_events.append(event)
            merging_tool_call = i, False, event

    final_events: List[EventBase] = []
    for event in merged_events:
        if event.get("type") == "tool_call" and event.get("formatted_tool_result"):
            final_events.append(event)
            tool_response_event = event.copy()
            tool_response_event["type"] = "system"
            tool_response_event["msg"] = (
                "Executing commands...\nResults:\n"
                + event.get("formatted_tool_result", "")
            )
            tool_response_event["isEvent"] = False
            for field in [
                "tool",
                "args",
                "formatted_tool_command",
                "formatted_tool_result",
                "description",
            ]:
                tool_response_event.pop(field, None)
            final_events.append(tool_response_event)
        else:
            final_events.append(event)
    return final_events


def limit_events(events: CtxChatType, max_events: int) -> CtxChatType:
    limited_events: CtxChatType = []
    for event in events:
        if len(limited_events) >= max_events:
            break
        limited_events.append(event)
    return limited_events


def filter_spam_events(events: CtxChatType, max_events: int) -> CtxChatType:
    filtered_events: CtxChatType = []
    prev_timestamp = None
    spam_count = 0

    total_events = len(events)
    if total_events > max_events:
        max_system_msgs = int(max_events * MAX_SYSTEM_MSG_PERCENT / 100)
        non_parsed_msg_count = 0
    else:
        non_parsed_msg_count = None

    event: EventBase
    tool_binded_events = set()
    for event in list(reversed(events)):
        if len(filtered_events) >= max_events:
            break
        event_add = True
        timestamp = timestamp_to_datetime(event)
        event_user = event.get("user", "")

        if non_parsed_msg_count is not None:
            if not event_user:
                if non_parsed_msg_count >= max_system_msgs:
                    event_add = False
                non_parsed_msg_count += 1

        if prev_timestamp is None:
            prev_timestamp = timestamp
        else:
            if prev_timestamp - timestamp <= timedelta(seconds=0.5):
                spam_count += 1
                if spam_count > 4:
                    event_add = False
                    prev_timestamp = timestamp
            else:
                spam_count = 0

            if spam_count <= 1:
                prev_timestamp = timestamp

        if event_user and event_user == SELF_NAME:
            event_type = event.get("type", "")
            if event_type and "chat" in event_type:
                event_add = False

        if event_add:
            tool_bind_event_id = event.get("tool_bind_event_id", None)
            if tool_bind_event_id:
                tool_binded_events.add(tool_bind_event_id)
            filtered_events.append(event)
    filtered_events = [
        ev
        for ev in filtered_events
        if ev.get("processing_timestamp") not in tool_binded_events
    ]
    return list(reversed(filtered_events))


def categorize_events(
    events: CtxChatType, trigger_id: int = -1
) -> Tuple[CtxChatType, CtxChatType, CtxChatType, EventBase]:
    now = datetime.now()
    recent = []
    relevant = []
    old = []

    trigger_event = None
    if trigger_id != -1:
        try:
            for event in events:
                event_timestamp = int(event.get("processing_timestamp", 0))
                if event_timestamp == int(trigger_id):
                    event["isTriggerEvent"] = True
                    trigger_event = [event]
                    break
        except:
            pass

    for event in events:
        timestamp = timestamp_to_datetime(event)
        time_diff = now - timestamp

        if time_diff <= timedelta(seconds=10):
            recent.append(event)
        elif time_diff <= timedelta(minutes=1):
            relevant.append(event)
        else:
            old.append(event)

    return recent, relevant, old, trigger_event


def preprocess_events(
    events: CtxChatType,
    tool_call_format: ToolCommandFormats = "command",
    max_events: int = EVENTS_LIMIT,
) -> CtxChatType:
    """Full preprocessing pipeline: limit → merge tools → merge users → filter spam."""
    processed_events = limit_events(events, max_events * 2)
    processed_events = merge_tool_calls(processed_events, tool_call_format)
    processed_events = merge_user_events(processed_events)
    processed_events = filter_spam_events(processed_events, max_events)
    return processed_events
