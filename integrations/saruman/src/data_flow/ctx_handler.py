"""CtxHandler — manages ctx_chat events with duplicate detection and event waiting.

Supports two backends:
- Legacy: multiprocessing Manager proxy (polling-based change detection)
- Redis: RedisCtxChat with pub/sub notifications (instant, no polling)

The backend is auto-detected from the ctx_swarm type.
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional, Sequence, Union

from data_schema.chat_structures import (
    CtxEventBase,
    EventBase,
    default_event_check,
)
from data_schema.activity_tracker import ActivityTracker
from data_schema.ctx_structures import CtxSwarmType
from data_schema.event_convert import convert_to_chat_message

DUPLICATE_LIMIT_CHECK = 8
SESSION_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "logs", "sessions",
)


def _is_redis_backend(ctx_swarm) -> bool:
    """Check if ctx_swarm is backed by Redis."""
    try:
        from data_flow.redis_ctx_swarm import RedisCtxSwarm
        return isinstance(ctx_swarm, RedisCtxSwarm)
    except ImportError:
        return False


class CtxHandler:
    def __init__(self, ctx_swarm: CtxSwarmType):
        self.ctx_swarm: CtxSwarmType = ctx_swarm
        self.ctx_chat = self.ctx_swarm["ctx_chat"]
        self.ctx_chat_lock = self.ctx_swarm["ctx_chat_lock"]
        self._event_waiters: list = []
        self._sync_event_waiters: list = []
        self.duplicate_limit: int = DUPLICATE_LIMIT_CHECK
        self._running = True
        self.processing_events = []
        self.activity_tracker = ActivityTracker()

        # Session log (JSONL)
        os.makedirs(SESSION_LOG_DIR, exist_ok=True)
        session_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._session_log_path = os.path.join(SESSION_LOG_DIR, f"{session_name}.jsonl")
        self._session_log_lock = threading.Lock()

        # Choose monitoring strategy based on backend
        self._use_redis = _is_redis_backend(ctx_swarm)
        if self._use_redis:
            self._checker_thread = threading.Thread(
                target=self._check_changes_redis, daemon=True
            )
        else:
            self._last_length = len(self.ctx_chat)
            self._checker_thread = threading.Thread(
                target=self._check_changes_polling, daemon=True
            )
        self._checker_thread.start()

    # -------------------------------------------------------------------
    # Conversion helpers
    # -------------------------------------------------------------------

    def _convert_to_event(self, message: Union[EventBase, str]) -> CtxEventBase:
        return convert_to_chat_message(message)

    def _to_dict(self, event: CtxEventBase) -> EventBase:
        return event.to_dict()

    # -------------------------------------------------------------------
    # Read operations
    # -------------------------------------------------------------------

    def get_ctx_chat(
        self, validate=True, dict_format=False, limit=100, raw=False
    ) -> Union[Sequence[CtxEventBase], Sequence[EventBase]]:
        """Get messages from ctx_chat."""
        if raw:
            return self.ctx_chat
        validated = []
        total = len(self.ctx_chat)
        if total >= limit:
            check_list = list(self.ctx_chat[-limit:])
        else:
            check_list = list(self.ctx_chat)
        for msg in check_list:
            event = self._convert_to_event(msg)
            if not validate or self._is_valid_event(event):
                validated.append(msg if dict_format else event)
        return validated

    def get_event_by_id(
        self, processing_timestamp: int, dict_format=False
    ) -> Union[CtxEventBase, dict, None]:
        """Get event by processing_timestamp."""
        if processing_timestamp == -1:
            return None

        # Fast path for Redis — direct timestamp lookup
        if self._use_redis:
            from data_flow.redis_collections import RedisCtxChat
            if isinstance(self.ctx_chat, RedisCtxChat):
                result = self.ctx_chat.get_by_timestamp(processing_timestamp)
                if result is not None:
                    if dict_format:
                        return result
                    return self._convert_to_event(result)
                return None

        for msg in reversed(self.get_ctx_chat(validate=False, dict_format=dict_format)):
            if dict_format:
                if msg.get("processing_timestamp") == processing_timestamp:
                    return msg
            else:
                event = self._convert_to_event(msg)
                if event.processing_timestamp == processing_timestamp:
                    return event
        return None

    # -------------------------------------------------------------------
    # Write operations
    # -------------------------------------------------------------------

    def _append_session_log(self, event_dict: dict):
        """Append event to JSONL session log for dataset collection."""
        try:
            with self._session_log_lock:
                with open(self._session_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event_dict, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass  # logging should never break the pipeline

    def _aggregate_mood(self, event_dict: dict):
        """Update mood from happiness_score in events (death=-3, kill=+1, etc.)."""
        score = event_dict.get("happiness_score")
        if score is None or score == 0:
            return
        try:
            mood = float(self.ctx_swarm["states"].get("mood", 0))
            mood = max(-10.0, min(10.0, mood + float(score)))
            self.ctx_swarm["states"]["mood"] = str(round(mood, 1))
            # Track reason
            ev_type = event_dict.get("type", "")
            user = event_dict.get("user", "")
            desc = event_dict.get("description", "")
            reason = f"{ev_type}: {user} {desc}".strip()
            self.ctx_swarm["states"]["mood_reason"] = reason
        except Exception:
            pass

    def add_message(
        self, message: Union[CtxEventBase, EventBase, str], duplicate_check=True
    ) -> bool:
        """Add a new message to ctx_chat with duplicate checking."""
        if isinstance(message, CtxEventBase):
            chat_message = message
        else:
            chat_message = self._convert_to_event(message)
        if not chat_message.processing_timestamp:
            chat_message.processing_timestamp = time.time_ns()

        duplicate_id = -1
        if duplicate_check:
            duplicate_id = self._get_event_duplicate(chat_message)

        if duplicate_id == -1:
            event_dict = self._to_dict(chat_message)
            self.ctx_chat.append(event_dict)
            self._append_session_log(event_dict)
            self._aggregate_mood(event_dict)
            return True
        else:
            existing = self.get_event_by_id(duplicate_id, dict_format=True)
            if existing is not None:
                new_dict = chat_message.to_dict()
                new_dict["repeat"] = existing.get("repeat", 1) + 1
                existing.update(new_dict)
                self.modify_message(duplicate_id, existing)
            return False

    def remove_message(self, processing_timestamp: int) -> bool:
        """Remove message by processing_timestamp."""
        if self._use_redis:
            from data_flow.redis_collections import RedisCtxChat
            if isinstance(self.ctx_chat, RedisCtxChat):
                return self.ctx_chat.remove_by_timestamp(processing_timestamp)

        for i, msg in enumerate(self.ctx_chat):
            event = self._convert_to_event(msg)
            if event.processing_timestamp == processing_timestamp:
                with self.ctx_chat_lock:
                    self.ctx_chat.pop(i)
                return True
        return False

    def modify_message(self, processing_timestamp: int, updates: EventBase) -> bool:
        """Modify existing message by processing_timestamp."""
        if self._use_redis:
            from data_flow.redis_collections import RedisCtxChat
            if isinstance(self.ctx_chat, RedisCtxChat):
                return self.ctx_chat.modify_by_timestamp(processing_timestamp, updates)

        for i, msg_dict in list(reversed(list(enumerate(self.ctx_chat)))):
            if msg_dict.get("processing_timestamp") == processing_timestamp:
                msg_dict.update(updates)
                with self.ctx_chat_lock:
                    self.ctx_chat[i] = msg_dict
                return True
        return False

    # -------------------------------------------------------------------
    # Duplicate detection
    # -------------------------------------------------------------------

    def _get_event_duplicate(self, chat_message: CtxEventBase) -> int:
        """Returns processing_timestamp of duplicate, or -1."""
        for existing in self.get_ctx_chat(
            validate=False, limit=self.duplicate_limit, raw=False
        ):
            if chat_message.processing_timestamp == existing.processing_timestamp:
                return existing.processing_timestamp
            # Always keep kill/death/damage events
            msg_type = chat_message.additional_fields.get("type", "")
            if msg_type in ("kill", "death", "damage"):
                return -1
            if (
                existing.type == chat_message.type
                and existing.additional_fields.get("isEvent")
                == chat_message.additional_fields.get("isEvent")
                and existing.additional_fields.get("description")
                == chat_message.additional_fields.get("description")
                and existing.user == chat_message.user
                and existing.msg == chat_message.msg
            ):
                return existing.processing_timestamp
        return -1

    # -------------------------------------------------------------------
    # Event validation
    # -------------------------------------------------------------------

    def _is_valid_event(self, event: CtxEventBase) -> bool:
        if event is None:
            return False
        if event.type and event.type == "mc_executor_event":
            return False
        if event.additional_fields.get("filter_results", None):
            return True
        return False

    # -------------------------------------------------------------------
    # Event waiting
    # -------------------------------------------------------------------

    def _check_event_waiters(self, message: Union[EventBase, CtxEventBase]):
        """Notify waiters if message matches their condition."""
        event = (
            self._convert_to_event(message) if isinstance(message, dict) else message
        )
        self.activity_tracker.record(event)

        for waiter in self._event_waiters[:]:
            try:
                if waiter.check_condition(event):
                    self._event_waiters.remove(waiter)
                    waiter.set_result(event)
            except Exception as e:
                print(f"[CTX HANDLER] Waiter error: {e}")

        for waiter in self._sync_event_waiters[:]:
            try:
                if waiter.check_condition(event):
                    self._sync_event_waiters.remove(waiter)
                    waiter.set_result(event)
            except Exception as e:
                print(f"[CTX HANDLER] Sync waiter error: {e}")

    def _get_base_event_check(
        self, check: Callable[[CtxEventBase], bool]
    ) -> Callable[[CtxEventBase], bool]:
        def new_check(event: CtxEventBase) -> bool:
            return self._is_valid_event(event) and check(event)
        return new_check

    async def wait_for(
        self,
        check: Callable[[CtxEventBase], bool],
        timeout: Optional[float] = None,
    ) -> CtxEventBase:
        """Async wait for a matching event."""
        waiter = EventWaiter(self._get_base_event_check(check))
        self._event_waiters.append(waiter)
        try:
            return await asyncio.wait_for(waiter.wait(), timeout)
        except asyncio.TimeoutError:
            if waiter in self._event_waiters:
                self._event_waiters.remove(waiter)
            raise

    def wait_for_sync(
        self,
        check: Optional[Callable[[CtxEventBase], bool]] = None,
        timeout: Optional[float] = None,
        raise_timeout=True,
    ) -> Optional[CtxEventBase]:
        """Synchronous wait for a matching event."""
        if check is None:
            check = default_event_check
        waiter = SyncEventWaiter(self._get_base_event_check(check))
        self._sync_event_waiters.append(waiter)
        try:
            return waiter.wait(timeout)
        except TimeoutError:
            if raise_timeout:
                raise
            return None
        finally:
            if waiter in self._sync_event_waiters:
                self._sync_event_waiters.remove(waiter)

    # -------------------------------------------------------------------
    # Change detection backends
    # -------------------------------------------------------------------

    def _check_changes_redis(self):
        """Redis pub/sub listener — replaces polling with instant notifications."""
        from data_flow.redis_backend import get_redis, make_key

        r = get_redis()
        pubsub = r.pubsub()
        channel = make_key("events:ctx_chat")
        pubsub.subscribe(channel)
        print("[CTX HANDLER] Redis pub/sub listener started")

        for message in pubsub.listen():
            if not self._running:
                break
            if message["type"] != "message":
                continue
            try:
                timestamp = int(message["data"])
                event_dict = self.ctx_chat.get_by_timestamp(timestamp)
                if event_dict is not None:
                    event = self._convert_to_event(event_dict)
                    if self._is_valid_event(event):
                        self._check_event_waiters(event)
                        # Remove from processing_events (added during initial dispatch attempt)
                        self.processing_events = [
                            (idx, ts) for idx, ts in self.processing_events if ts != timestamp
                        ]
                    else:
                        if not any(ts == timestamp for _, ts in self.processing_events):
                            self.processing_events.append((0, timestamp))
            except (ValueError, TypeError, IndexError):
                pass
            except Exception as e:
                print(f"[CTX HANDLER] Redis listener error: {e}")

            # Check processing_events (waiting for filter results)
            self._recheck_processing_events()

        pubsub.unsubscribe()
        pubsub.close()
        print("[CTX HANDLER] Redis pub/sub listener stopped")

    def _check_changes_polling(self):
        """Legacy polling-based change detection for Manager backend."""
        while self._running:
            current_length = len(self.ctx_chat)

            if current_length < self._last_length:
                self.processing_events = []
                self._last_length = current_length

            if current_length > self._last_length:
                for i in range(self._last_length, current_length):
                    try:
                        message = self.ctx_chat[i]
                        event = self._convert_to_event(message)
                        if self._is_valid_event(event):
                            self._check_event_waiters(event)
                        else:
                            self.processing_events.append(
                                (i, event.processing_timestamp)
                            )
                    except IndexError:
                        pass
                self._last_length = current_length

            self._recheck_processing_events()
            threading.Event().wait(0.05)

    def _recheck_processing_events(self):
        """Re-check events waiting for validation (e.g. filter results)."""
        if not self.processing_events:
            return
        for idx, timestamp in self.processing_events[:]:
            event_dict = None
            try:
                if self._use_redis:
                    event_dict = self.ctx_chat.get_by_timestamp(timestamp)
                else:
                    event_dict = self.ctx_chat[idx]
                    if event_dict.get("processing_timestamp") != timestamp:
                        event_dict = None
            except (IndexError, KeyError):
                pass

            if event_dict is None:
                # Search by timestamp
                for ed in reversed(list(self.ctx_chat)):
                    if ed.get("processing_timestamp") == timestamp:
                        event_dict = ed
                        break

            if event_dict is not None:
                event = self._convert_to_event(event_dict)
                if self._is_valid_event(event):
                    self._check_event_waiters(event)
                    self.processing_events.remove((idx, timestamp))

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def save_to_json(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            messages = [
                msg.to_dict() if isinstance(msg, CtxEventBase) else msg
                for msg in self.ctx_chat
            ]
            json.dump(messages, f, ensure_ascii=False, indent=2, default=str)

    @classmethod
    def load_from_json(cls, filepath: str) -> "CtxHandler":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            messages = [convert_to_chat_message(msg) for msg in data]
            return cls(messages)

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def shutdown(self):
        self._running = False
        if self._checker_thread.is_alive():
            self._checker_thread.join(timeout=2.0)

    def __exit__(self, *args):
        self.shutdown()

    def __del__(self):
        self.shutdown()


# -------------------------------------------------------------------
# Event waiter helpers (unchanged)
# -------------------------------------------------------------------

class EventWaiter:
    def __init__(self, check: Callable[[CtxEventBase], bool]):
        self.check = check
        self.future = asyncio.get_event_loop().create_future()

    def check_condition(self, message: CtxEventBase) -> bool:
        try:
            return self.check(message)
        except Exception:
            return False

    def set_result(self, message: CtxEventBase):
        if not self.future.done():
            self.future.set_result(message)

    async def wait(self) -> CtxEventBase:
        return await self.future


class SyncEventWaiter:
    def __init__(self, check: Callable[[CtxEventBase], bool]):
        self.check = check
        self.event = threading.Event()
        self.result = None

    def check_condition(self, message: CtxEventBase) -> bool:
        try:
            return self.check(message)
        except Exception:
            return False

    def set_result(self, message: CtxEventBase):
        self.result = message
        self.event.set()

    def wait(self, timeout: Optional[float] = None) -> CtxEventBase:
        if not self.event.wait(timeout):
            raise TimeoutError("Wait for event timed out")
        return self.result
