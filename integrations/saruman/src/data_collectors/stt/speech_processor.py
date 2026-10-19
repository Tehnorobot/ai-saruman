"""Voice Input Processor — routes audio streams to Docker STT via WebSocket.

One WebSocket per user. Docker handles VAD, accumulation, and STT.
Transcriptions arrive as events and go into ctx_chat.

Supports multiple parallel audio sources (Minecraft voice chat, mic, Discord).
"""

import os
import threading
import time
from typing import Dict

import numpy as np

from data_collectors.stt.stt_stream_client import STTStreamClient
from data_flow.ctx_handler import CtxHandler
from data_schema.chat_structures import EventBase
from data_schema.ctx_structures import CtxSwarmType
from utils.time_helper import eztime

MAX_PARALLEL_USERS: int = int(os.getenv("STT_MAX_PARALLEL_USERS", "20"))
USER_TIMEOUT: float = 30.0  # disconnect idle sessions after 30s


class UserSession:
    """One WebSocket streaming session per user."""

    def __init__(self, user: str, env: str, on_transcript):
        self.user = user
        self.env = env
        self.last_activity = time.time()
        self.client = STTStreamClient(
            on_transcript=lambda text, start, end: on_transcript(
                self.user, self.env, text, start, end
            ),
        )
        self.client.connect()

    def send(self, audio_pcm: np.ndarray):
        self.last_activity = time.time()
        self.client.send_audio(audio_pcm)

    def close(self):
        self.client.disconnect()

    @property
    def connected(self):
        return self.client.is_connected

    @property
    def idle_time(self):
        return time.time() - self.last_activity


class VoiceInputProcessor:
    def __init__(self, ctx_swarm: CtxSwarmType):
        self.ctx_swarm = ctx_swarm
        self.ctx_handler = CtxHandler(ctx_swarm)
        self.sessions: Dict[str, UserSession] = {}
        self.lock = threading.Lock()

        # Feed queue consumer
        self.feed_thread = threading.Thread(
            target=self._feed_loop, daemon=True
        )
        self.feed_thread.start()

        # Session cleanup
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True
        )
        self.cleanup_thread.start()

    def _get_or_create_session(self, user: str, env: str) -> UserSession:
        """Get existing session or create new one. Caller must hold self.lock."""
        session = self.sessions.get(user)
        if session and session.connected:
            session.env = env  # update env in case it changed
            return session

        # Evict if at limit
        if len(self.sessions) >= MAX_PARALLEL_USERS:
            self._evict_oldest()

        # Clean up old session if exists
        if session:
            session.close()

        session = UserSession(user, env, self._on_transcript)
        self.sessions[user] = session
        print(f"[STT] New session for {user} (env={env})")
        return session

    def _evict_oldest(self):
        """Remove the most idle session. Caller must hold self.lock."""
        if not self.sessions:
            return
        oldest_user = max(self.sessions, key=lambda u: self.sessions[u].idle_time)
        print(f"[STT] Evicting idle session: {oldest_user}")
        self.sessions[oldest_user].close()
        del self.sessions[oldest_user]

    def _feed_loop(self):
        """Consume audio_feed_queue, accumulate per-user, resample to 16kHz, send."""
        import soxr

        TARGET_SR = 16000
        # Accumulate ~0.5s before resampling for quality
        MIN_RESAMPLE_SAMPLES = 8000  # 0.5s at 16kHz worth of input

        resample_buffers: Dict[str, np.ndarray] = {}
        user_sr: Dict[str, int] = {}

        while True:
            audio_entry = self.ctx_swarm["audio_feed_queue"].get()
            if audio_entry is None:
                continue

            user = audio_entry.get("user", "MainframeOperator")
            env = audio_entry.get("env", "voice")
            sr = audio_entry.get("sr", 48000)
            channels = audio_entry.get("channels", 1)
            sample_width_bits = audio_entry.get("sample_width_bits", 16)

            raw_audio = audio_entry["audio"]
            if audio_entry.get("audio_encoding") == "base64":
                import base64
                raw_audio = base64.b64decode(raw_audio)
            elif isinstance(raw_audio, str):
                raw_audio = raw_audio.encode("latin-1")

            sample_width = sample_width_bits // 8
            if sample_width == 2:
                int16 = np.frombuffer(raw_audio, dtype=np.int16)
            elif sample_width == 1:
                int16 = np.frombuffer(raw_audio, dtype=np.int8).astype(np.int16) * 256
            else:
                int16 = np.frombuffer(raw_audio, dtype=np.int16)

            if channels > 1:
                int16 = int16.reshape(-1, channels).mean(axis=1).astype(np.int16)

            float32 = int16.astype(np.float32) / 32768.0
            user_sr[user] = sr

            # Accumulate, then resample as one block
            if user not in resample_buffers:
                resample_buffers[user] = np.array([], dtype=np.float32)
            resample_buffers[user] = np.concatenate([resample_buffers[user], float32])

            # How many input samples = 0.5s worth
            min_input = int(MIN_RESAMPLE_SAMPLES * sr / TARGET_SR)
            if len(resample_buffers[user]) >= min_input:
                buf = resample_buffers[user]
                resample_buffers[user] = np.array([], dtype=np.float32)

                if sr != TARGET_SR:
                    samples_16k = soxr.resample(buf, sr, TARGET_SR).astype(np.float32)
                else:
                    samples_16k = buf

                # Boost quiet audio (SVC sends very low amplitude)
                AUDIO_GAIN = float(os.getenv("STT_AUDIO_GAIN", "9.0"))
                CLIP_PEAKS = os.getenv("STT_CLIP_PEAKS", "true").lower() == "true"
                samples_16k = samples_16k * AUDIO_GAIN
                if CLIP_PEAKS:
                    samples_16k = np.clip(samples_16k, -1.0, 1.0)
                peak = np.max(np.abs(samples_16k))
                print(f"[STT] {user}: {len(samples_16k)} samples, peak={peak:.3f} (gain={AUDIO_GAIN}x, clip={CLIP_PEAKS})")

                with self.lock:
                    session = self._get_or_create_session(user, env)
                    session.send(samples_16k)

    def _on_transcript(self, user: str, env: str, text: str, start: float, end: float):
        """Called from WS thread when Docker returns a transcription."""
        text = text.strip()
        if not text:
            return
        # Filter garbage
        if not text.replace("silence", "").replace("*", "").replace(" ", ""):
            return

        print(f"[STT] {user}: {text}")
        event = EventBase(
            processing_timestamp=time.time_ns(),
            date=eztime(),
            env=env,
            user=user if user else "Developer",
            type="voice",
            msg=text,
        )
        self.ctx_handler.add_message(event)

    def _cleanup_loop(self):
        """Periodically close idle sessions."""
        while True:
            time.sleep(5)
            with self.lock:
                to_remove = [
                    user for user, s in self.sessions.items()
                    if s.idle_time > USER_TIMEOUT or not s.connected
                ]
                for user in to_remove:
                    print(f"[STT] Closing idle session: {user}")
                    self.sessions[user].close()
                    del self.sessions[user]


def run_voice_processor(ctx_swarm):
    processor = VoiceInputProcessor(ctx_swarm)
    processor.feed_thread.join()
