"""Local microphone capture via soundcard.

Captures audio from a physical mic, outputs 16kHz mono float32 chunks
to a callback. Runs in its own thread.
"""

import os
import threading
import warnings
from typing import Callable, List, Optional

import numpy as np
import soundcard as sc

# Suppress soundcard's noisy "data discontinuity" warnings
warnings.filterwarnings("ignore", message="data discontinuity", module="soundcard")

MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
MIC_CHUNK_DURATION = 0.1  # 100ms chunks -> 1600 samples at 16kHz

MIC_DEVICE_NAME = os.getenv("SPEECH_MIC_DEVICE", "")


class MicCapture:
    """Captures audio from local microphone, calls back with float32 PCM chunks."""

    def __init__(
        self,
        callback: Callable[[np.ndarray], None],
        device_name: str = MIC_DEVICE_NAME,
        sample_rate: int = MIC_SAMPLE_RATE,
        chunk_duration: float = MIC_CHUNK_DURATION,
    ):
        self.callback = callback
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration)
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.mic = self._find_mic(device_name)

    @staticmethod
    def _find_mic(device_name: str):
        if device_name:
            for m in sc.all_microphones(include_loopback=False):
                if device_name in m.name:
                    print(f"[MicCapture] Found mic: {m.name}")
                    return m
        default = sc.default_microphone()
        print(f"[MicCapture] Using default mic: {default.name}")
        return default

    @staticmethod
    def list_devices() -> List[str]:
        """List available microphone names (for Gradio dropdown)."""
        try:
            devices = [m.name for m in sc.all_microphones(include_loopback=False)]
            if not devices:
                print("[MicCapture] No microphones found")
            return devices
        except Exception as e:
            print(f"[MicCapture] list_devices error: {e}")
            return []

    def set_device(self, device_name: str):
        """Switch microphone device by name."""
        was_running = self.running
        if was_running:
            self.stop()
        self.mic = self._find_mic(device_name)
        if was_running:
            self.start()

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _capture_loop(self):
        try:
            with self.mic.recorder(
                samplerate=self.sample_rate, channels=MIC_CHANNELS
            ) as recorder:
                while self.running:
                    chunk = recorder.record(numframes=self.chunk_size)
                    audio = chunk[:, 0] if chunk.ndim > 1 else chunk
                    self.callback(audio)
        except Exception as e:
            print(f"[MicCapture] Error: {e}")
            self.running = False
