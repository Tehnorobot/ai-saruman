"""WebSocket streaming client for Parakeet STT Docker.

Streams raw 16kHz PCM audio, receives VAD events and transcriptions.
Used for mic input where we need server-side VAD.
"""

import json
import os
import threading
from typing import Callable, Optional

import numpy as np
import websocket

PARAKEET_WS_URL = os.getenv(
    "PARAKEET_STREAM_URL",
    "ws://127.0.0.1:22233/v1/audio/stream",
)


class STTStreamClient:
    """Streams audio to Parakeet Docker, receives transcriptions.

    Usage:
        client = STTStreamClient(on_transcript=callback)
        client.connect()
        client.send_audio(chunk)  # numpy float32 16kHz mono
        ...
        client.disconnect()
    """

    def __init__(
        self,
        on_transcript: Optional[Callable[[str, float, float], None]] = None,
        on_vad: Optional[Callable[[bool], None]] = None,
        url: str = PARAKEET_WS_URL,
    ):
        self.url = url
        self.on_transcript = on_transcript  # (text, start_sec, end_sec)
        self.on_vad = on_vad  # (is_speech)
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._connected = False
        self._send_count = 0

    def connect(self):
        self._ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever, daemon=True
        )
        self._ws_thread.start()

        # Wait for connection
        import time
        deadline = time.time() + 5.0
        while not self._connected and time.time() < deadline:
            time.sleep(0.05)
        if not self._connected:
            print("[STTStream] Connection timeout")

    def disconnect(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        self._connected = False

    def send_audio(self, audio_chunk: np.ndarray):
        """Send float32 16kHz mono audio chunk."""
        if not self._connected or self._ws is None:
            return
        self._send_count += 1
        if self._send_count % 20 == 1:
            level = np.max(np.abs(audio_chunk)) if len(audio_chunk) > 0 else 0
            print(f"[STTStream] send#{self._send_count} samples={len(audio_chunk)} level={level:.4f}")
        int16_data = (audio_chunk * 32767).astype(np.int16)
        try:
            self._ws.send(int16_data.tobytes(), opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception:
            pass

    @property
    def is_connected(self):
        return self._connected

    def _on_open(self, ws):
        self._connected = True
        print(f"[STTStream] Connected to {self.url}")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        event = data.get("event")
        print(f"[STTStream] Received: {data}")
        if event == "transcript" and self.on_transcript:
            self.on_transcript(
                data.get("text", ""),
                data.get("start", 0.0),
                data.get("end", 0.0),
            )
        elif event == "vad" and self.on_vad:
            self.on_vad(data.get("speech", False))

    def _on_error(self, ws, error):
        print(f"[STTStream] Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        was_connected = self._connected
        self._connected = False
        if was_connected:
            print("[STTStream] Disconnected")
