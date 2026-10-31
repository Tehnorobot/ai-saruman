"""PCM conversion and buffered speech output, separate from music/FX."""

import threading

import numpy as np
import sounddevice as sd


def float_pcm(samples):
    """Convert scipy WAV samples to normalized float32 without attenuating floats."""
    samples = np.asarray(samples)
    if samples.dtype.kind == "u" and samples.dtype.itemsize == 1:
        samples = (samples.astype(np.float32) - 128) / 128
    elif samples.dtype.kind == "i":
        samples = samples.astype(np.float32) / (2 ** (samples.dtype.itemsize * 8 - 1))
    elif samples.dtype.kind != "f":
        raise ValueError(f"Unsupported audio format: {samples.dtype}")
    if samples.ndim not in (1, 2) or not np.isfinite(samples).all():
        raise ValueError("Audio must contain finite mono or multichannel PCM")
    return np.ascontiguousarray(np.clip(samples, -1, 1), dtype=np.float32)


class Playback:
    def __init__(self):
        self.done = threading.Event()
        self.error = None
        self.interrupted = False

    def wait(self, timeout=None):
        return self.done.wait(timeout)

    def result(self):
        self.wait()
        if self.error is not None:
            raise RuntimeError(f"Speech playback failed: {self.error}") from self.error


def output_device(name):
    """Resolve the configured endpoint; prefer its WASAPI representation on Windows."""
    apis = sd.query_hostapis()
    matches = [
        (index, device)
        for index, device in enumerate(sd.query_devices())
        if device["max_output_channels"] > 0 and device["name"] == name
    ]
    if not matches:
        raise ValueError(f"Speech output device is unavailable: {name}")
    matches.sort(key=lambda item: "WASAPI" not in apis[item[1]["hostapi"]]["name"])
    return matches[0]


def write_speech(name, samples, rate, interrupted):
    """Write every frame and drain on normal completion; abort only on interruption."""
    index, device = output_device(name)
    samples = float_pcm(samples)
    if samples.ndim == 1:
        samples = samples[:, None]
    # Mirror mono onto both speakers rather than relying on host channel mapping.
    if samples.shape[1] == 1 and device["max_output_channels"] >= 2:
        samples = np.repeat(samples, 2, axis=1)
    if samples.shape[1] > device["max_output_channels"]:
        raise ValueError("Speech has more channels than the output device")
    with sd.OutputStream(
        device=index,
        samplerate=rate,
        channels=samples.shape[1],
        dtype="float32",
        latency="high",
        blocksize=0,
    ) as stream:
        for offset in range(0, len(samples), max(1, rate // 10)):
            if interrupted():
                stream.abort()
                return True
            if stream.write(samples[offset : offset + max(1, rate // 10)]):
                print("[Audio] Speech output underflow; check device load")
        # OutputStream's context manager stops (drains), then closes the stream.
    return False
