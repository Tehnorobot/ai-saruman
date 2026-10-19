"""HTTP client for Parakeet ONNX Docker STT endpoint.

OpenAI-compatible POST /v1/audio/transcriptions.
Same interface as FasterWhisperSTT: transcribe_audio(AudioSegment) -> str.
"""

import io
import os

import requests
from pydub import AudioSegment

PARAKEET_URL = os.getenv(
    "PARAKEET_API_URL",
    "http://127.0.0.1:22233/v1/audio/transcriptions",
)
PARAKEET_LANGUAGE = os.getenv("PARAKEET_LANGUAGE", "ru")


class ParakeetSTT:
    def __init__(self, **kwargs):
        self.url = PARAKEET_URL
        self.language = PARAKEET_LANGUAGE
        print(f"[ParakeetSTT] Using endpoint: {self.url}")

    def transcribe_audio(self, audio: AudioSegment) -> str:
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        buf.seek(0)

        try:
            resp = requests.post(
                self.url,
                files={"file": ("audio.wav", buf, "audio/wav")},
                data={"language": self.language},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()
        except requests.RequestException as e:
            print(f"[ParakeetSTT] Request failed: {e}")
            return ""
