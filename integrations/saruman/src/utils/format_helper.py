"""Format helper — re-export facade.

Core logic has been split into:
- utils.event_format: single event formatting (format_single_event, timestamp helpers)
- utils.event_pipeline: event list processing (merge, filter, preprocess, categorize)

This module re-exports everything for backward compatibility and keeps
functions that depend on both (format_events_with_roles, format_events_for_llm, etc.).
"""

import copy
import os
import sys
import time
from typing import Dict, List, Tuple, Union

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from config_schema.general import get_name
from data_schema.chat_structures import EventBase, MentionedUser
from data_schema.ctx_structures import CtxChatType
from data_schema.tool_structures import ToolCommandFormats
from utils.time_helper import eztime

# Re-exports from event_format
from utils.event_format import (  # noqa: F401
    format_relative_time_event,
    format_single_event,
    timestamp_to_datetime,
)

# Re-exports from event_pipeline
from utils.event_pipeline import (  # noqa: F401
    EVENTS_LIMIT,
    categorize_events,
    filter_spam_events,
    limit_events,
    merge_tool_calls,
    merge_user_events,
    preprocess_events,
)

SELF_NAME = get_name()
RAG_EVENTS_LIMIT = 3


def format_events_for_llm(
    events: list[dict], self_name=SELF_NAME, trigger_event_id=None
) -> str:
    if not events:
        return ""

    formatted_messages = format_events_with_roles(events, self_name, trigger_event_id)
    if not formatted_messages:
        return ""

    return "\n\n".join(
        f"[{msg['role']}] ===\n{msg['content']}" for msg in formatted_messages
    )


def format_events_for_rag(events: list[dict]) -> str:
    if events is not None:
        length = len(events)
        if length > 0:
            if length > RAG_EVENTS_LIMIT:
                this_events = events[-RAG_EVENTS_LIMIT:]
            else:
                this_events = events
            fields = [
                [f"{v}" for k, v in event.items() if k and k in ["msg", "text"]]
                for event in this_events
            ]
            rag_query = "\n".join(["\n".join(field) for field in fields])
            return rag_query
    return ""


def get_user_mentions(
    events: List[dict], user: str, events_limit: int = 80
) -> MentionedUser:
    mentioned_user: MentionedUser = MentionedUser()
    for event in events:
        if events_limit <= 1:
            break
        events_limit -= 1
        if event.get("user", False):
            if event["user"] == user:
                if not mentioned_user:
                    mentioned_user = MentionedUser(
                        {"name": user, "found_count": 0, "binded_events": []}
                    )
                mentioned_user["binded_events"].append(event)
                mentioned_user["found_count"] = mentioned_user["found_count"] + 1
                mentioned_user["last_activity_timestamp"] = event.get(
                    "processing_timestamp", -1
                )
                mentioned_user["last_activity_string"] = format_relative_time_event(
                    event
                )
    return mentioned_user


def format_events_with_roles(
    events: List[dict],
    self_name: str = SELF_NAME,
    trigger_event_id=-1,
    tool_call_format: ToolCommandFormats = "command",
    return_mentioned_users: bool = False,
) -> Union[List[dict], Tuple[List[dict], Dict[str, MentionedUser]]]:
    """Format events into role-based messages for legacy agents (CoreAgent, etc.)."""
    if return_mentioned_users:
        mentioned_users: dict[str, MentionedUser] = {}
    if not events:
        if return_mentioned_users:
            return [], mentioned_users
        return []

    this_events = copy.deepcopy(events)
    processed_events = preprocess_events(
        this_events, tool_call_format, max_events=EVENTS_LIMIT
    )

    recent, relevant, old, trigger_event = categorize_events(
        processed_events, trigger_event_id
    )
    formatted_messages = []

    def add_divider(text: str):
        divider_event = EventBase(
            user="system",
            msg=text,
            type="system",
            env="system",
            processing_timestamp=time.time_ns(),
            date=eztime(),
            text_timestamp="DIVIDER",
        )
        divider_formatted_text = format_single_event(
            event=divider_event, self_name=self_name, tool_call_format=tool_call_format
        )
        formatted_messages.append({"role": "user", "content": divider_formatted_text})

    def add_events(category_events: list):
        for event in category_events:
            ev_type = event.get("type", "")
            if (
                ev_type == "tool_call"
                and event.get("user", "") == self_name
                or ev_type == "OVERRIDE_ASSISTANT_ROLE"
            ):
                role = "assistant"
            else:
                role = "user"
            formatted_event = format_single_event(
                event=event, self_name=self_name, tool_call_format=tool_call_format
            )
            if return_mentioned_users:
                user = event.get("user", False)
                if user:
                    if user in mentioned_users:
                        mentioned_user = mentioned_users[user]
                    else:
                        mentioned_user = MentionedUser(
                            {"name": user, "found_count": 0, "binded_events": []}
                        )
                    mentioned_user["binded_events"].append(event)
                    mentioned_user["found_count"] = mentioned_user["found_count"] + 1
                    mentioned_user["last_activity_timestamp"] = event.get(
                        "processing_timestamp", -1
                    )
                    mentioned_user["last_activity_string"] = format_relative_time_event(
                        event
                    )
                    mentioned_users[user] = mentioned_user
            formatted_messages.append({"role": role, "content": formatted_event})

    if old:
        add_divider(
            f"{SELF_NAME}, acknowledge the Old events (older than minute, OLD CONTEXT):\n"
        )
        add_events(old)

    if relevant:
        add_divider(
            f"{SELF_NAME}, the next Events were for the past minute (CONTEXT):\n"
        )
        add_events(relevant)

    if recent:
        add_divider(f"{SELF_NAME}, react to Recent events (performs just NOW):\n")
        add_events(category_events=recent)

    if return_mentioned_users:
        return formatted_messages, mentioned_users
    return formatted_messages


def openai_chat_to_str(chat_messages: list[dict]) -> str:
    """Convert format_events_with_roles output to string."""
    if not chat_messages:
        return ""

    result = []
    for msg in chat_messages:
        content = msg["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            if content and content[0].get("type", False) == "text":
                text = content["text"]
            else:
                continue
        else:
            continue
        if msg["role"] == "system":
            result.append(f"\n{msg['role']} =====\n{text}\n")
        else:
            role_rename = msg["role"]
            if role_rename == "assistant":
                role_rename = "you"
            result.append(f"{role_rename} ====\n{text}\n\n")

    return "".join(result).strip()


if __name__ == "__main__":

    from data_schema.structure_templates import CTX_CHAT_SAMPLE

    print(
        format_events_with_roles(CTX_CHAT_SAMPLE, trigger_event_id=1736021873965839300)
    )
    print("--- for rag ---")
    print(format_events_for_rag(CTX_CHAT_SAMPLE))
    event = CTX_CHAT_SAMPLE[0]
    timestamp = format_relative_time_event({"date": "22:19:15"})
    print(timestamp)
    print(time.time())
    timestamp = format_relative_time_event({"date": time.time_ns()})
    print("cur time", timestamp)
