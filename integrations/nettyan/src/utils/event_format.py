"""Event formatting — single event → string for LLM prompts.

Extracted from format_helper. Contains:
- format_single_event: formats one EventBase into a prompt line
- format_relative_time_event: human-readable timestamp for an event
- timestamp_to_datetime: convert event timestamp to datetime
"""

from datetime import datetime
from typing import Sequence

from dateutil.parser import parse

from agent.tools.tool_executor import format_tool_command
from config_schema.general import get_name
from data_schema.chat_structures import EventBase
from data_schema.tool_structures import TOOL_COMMAND_FORMATS, ToolCommandFormats
from utils.time_helper import format_relative_time

SELF_NAME = get_name()


def timestamp_to_datetime(event: EventBase) -> datetime:
    timestamp = event.get("processing_timestamp", event["date"])
    if isinstance(timestamp, str):
        timestamp = parse(timestamp)
    elif isinstance(timestamp, datetime):
        pass
    elif isinstance(timestamp, (int, float)):
        timestamp = datetime.fromtimestamp(timestamp / 1000000000)  # ns → s
    return timestamp


def format_relative_time_event(event: EventBase) -> str:
    if event.get("text_timestamp", False):
        return event["text_timestamp"]
    event_dt = timestamp_to_datetime(event)
    return format_relative_time(event_dt)


def format_absolute_time_event(event: EventBase) -> str:
    """Return HH:MM:SS timestamp for an event. Stable for caching."""
    try:
        event_dt = timestamp_to_datetime(event)
        return event_dt.strftime("%H:%M:%S")
    except Exception:
        return "??:??:??"


def format_single_event(
    event: EventBase,
    self_name: str = SELF_NAME,
    tool_call_format: ToolCommandFormats = "command",
) -> str:
    """Format single event to string for LLM prompt.

    Example:
        EventBase(user=TestNick, ...)  →  MINECRAFT CHAT [2.3m ago] TestNick: message
    """
    ev = event.copy()
    env: str = ev.get("env", "")
    ev_type_raw = ev.get("type", "")
    msg = ""
    if ev_type_raw == "OVERRIDE_ASSISTANT_ROLE":
        return ev.get("msg", "")
    timestamp = format_relative_time_event(ev)
    if ev.get("isTriggerEvent", False):
        timestamp = "WAKED YOU UP, " + timestamp
    if not ev.get("user", False):
        ev["user"] = "(server message)"
    else:
        if ev.get("description", False):
            msg = f"{ev['description']}"
        else:
            fields = [
                f"{k}={v}"
                for k, v in ev.items()
                if k not in ["date", "user", "isEvent", "env"]
            ]
            msg = f""" in new Event({", ".join(fields)})"""
    event_type_name = "CHAT"
    if not ev.get("isEvent"):
        event_type_name = ""
        if ev.get("msg", ""):
            msg = ": " + ev.get("msg", "")
        else:
            msg = ": "
    user_str = ev["user"]
    if ev.get("user", "") in [self_name]:
        user_str += " (YOU)"
    repeat_msg = ""
    repeat_cnt = ev.get("repeat", 0)
    if repeat_cnt > 1:
        repeat_msg = " (x" + str(repeat_cnt) + ")"
    if ev_type_raw:
        event_type_name = ev_type_raw.upper()
        if (
            ev_type_raw == "tool_call"
            and ev.get("self")
            and ev.get("tool")
            and ev.get("args")
        ):
            is_tool_call = False
            is_single_tool = not ev.get("multiple_tool_call", False)
            if is_single_tool:
                formatted_tool_use = format_tool_command(
                    ev["tool"], ev["args"], tool_call_format=tool_call_format
                )
                if formatted_tool_use:
                    is_tool_call = True
                    event_type_name = "RESPONSE"
                    msg = (
                        TOOL_COMMAND_FORMATS[tool_call_format]["multiple_start"]
                        + formatted_tool_use
                        + TOOL_COMMAND_FORMATS[tool_call_format]["multiple_end"]
                    )
                    return msg
            else:
                if ev.get("formatted_tool_command", False):
                    is_tool_call = True
                    event_type_name = "RESPONSE"
                    msg = (
                        TOOL_COMMAND_FORMATS[tool_call_format]["multiple_start"]
                        + ev["formatted_tool_command"]
                        + TOOL_COMMAND_FORMATS[tool_call_format]["multiple_end"]
                    )
                    return msg
            if is_tool_call:
                env = ""
                repeat_msg = ""
    if env and event_type_name:
        if env.upper() == event_type_name.upper():
            event_type_name = ""
        else:
            event_type_name = " " + event_type_name

        if env == "voice":
            msg += " [RECOGNIZED FROM RAW VOICE, ERRORS POSSIBLE!]"
        elif env == "donation":
            event_type_name = "DONATION"
            donation_msg = ev.get("msg", "")
            msg = (
                ev.get("description", "")
                + f"\nDonationInfo[user={user_str}, sum={ev.get('summ', 0)}$]\n"
            )
            if donation_msg:
                msg += (
                    user_str
                    + " also sent a message in his donation, FULLY AND HONESTLY ANSWER it as GOOD as you can:\n`"
                    + donation_msg
                    + "`"
                )
            env = ""
            repeat_msg = ""

    final_result: str = (
        f"{env.upper()}{event_type_name.upper()} [{timestamp}] {user_str}{msg}{repeat_msg}"
    )
    return final_result
