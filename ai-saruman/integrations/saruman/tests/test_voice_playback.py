"""Check the real speech path without emitting audio or calling a model."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from scipy.io import wavfile

from tts import playback, voice_diagnostics
from tts import simple_tts_handler as handler
from tts.audio_device import AudioProcessor


class VoicePlaybackTests(unittest.TestCase):
    def test_pcm_formats_keep_volume(self):
        for samples in (
            np.array([0, 16384, -16384], dtype=np.int16),
            np.array([0, 1073741824, -1073741824], dtype=np.int32),
            np.array([128, 192, 64], dtype=np.uint8),
            np.array([0, 0.5, -0.5], dtype=np.float32),
        ):
            with self.subTest(dtype=samples.dtype):
                np.testing.assert_array_equal(
                    playback.float_pcm(samples), [0, 0.5, -0.5]
                )
        with self.assertRaises(ValueError):
            playback.float_pcm(np.array([np.nan]))

    def test_wasapi_output_writes_tail_and_drains_or_aborts(self):
        devices = [
            {"name": "Speakers", "hostapi": 0, "max_output_channels": 2},
            {"name": "Speakers", "hostapi": 1, "max_output_channels": 2},
        ]
        audio = np.linspace(-0.5, 0.5, 111, dtype=np.float32)
        for stop in (False, True):
            stream = Mock()
            stream.write.return_value = False
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock(return_value=False)
            with (
                patch.object(playback.sd, "query_devices", return_value=devices),
                patch.object(
                    playback.sd,
                    "query_hostapis",
                    return_value=[{"name": "MME"}, {"name": "Windows WASAPI"}],
                ),
                patch.object(
                    playback.sd, "OutputStream", return_value=stream
                ) as output,
            ):
                self.assertEqual(
                    playback.write_speech(
                        "Speakers", audio, 1000, lambda stop=stop: stop
                    ),
                    stop,
                )
                self.assertEqual(output.call_args.kwargs["device"], 1)
            if stop:
                stream.abort.assert_called_once()
                stream.write.assert_not_called()
            else:
                written = np.concatenate(
                    [call.args[0] for call in stream.write.call_args_list]
                )
                self.assertEqual(written.shape, (111, 2))
                np.testing.assert_array_equal(written[:, 0], audio)
                stream.abort.assert_not_called()
            stream.__exit__.assert_called_once()

    def test_resample_stereo_preserves_duration_and_pitch(self):
        processor = AudioProcessor.__new__(AudioProcessor)
        processor.device_sr = processor.fx_device_sr = 48000
        processor.main_speaker = SimpleNamespace(name="Speakers")
        t = np.arange(4410) / 44100
        mono = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        for audio in (mono, np.column_stack([mono, mono])):

            def complete(speaker, samples, rate, handle, requested_at):
                handle.done.set()

            with patch.object(processor, "_play_safe", side_effect=complete) as write:
                processor.play_sound(audio, 44100, blocking=True)
                samples = write.call_args.args[1]
                self.assertEqual(samples.shape, (4800,) + audio.shape[1:])
                channel = samples if samples.ndim == 1 else samples[:, 0]
                peak = np.argmax(np.abs(np.fft.rfft(channel))) * 48000 / len(channel)
                self.assertAlmostEqual(peak, 440, places=2)
                self.assertAlmostEqual(float(np.max(channel)), 0.4, places=2)

    def test_playback_failure_is_signaled_and_interrupt_cancels_queued_audio(self):
        processor = AudioProcessor.__new__(AudioProcessor)
        processor._speech_lock = threading.Lock()
        processor._last_interrupt_time = 0
        speaker = SimpleNamespace(name="Speakers")
        done = playback.Playback()
        with patch(
            "tts.audio_device.write_speech", side_effect=ValueError("device gone")
        ):
            processor._play_safe(speaker, np.zeros(100), 48000, done, 1)
        with self.assertRaisesRegex(RuntimeError, "device gone"):
            done.result()
        processor._last_interrupt_time = 2
        done = playback.Playback()
        with patch("tts.audio_device.write_speech") as write:
            processor._play_safe(speaker, np.zeros(100), 48000, done, 1)
        self.assertTrue(done.wait(0))
        self.assertTrue(done.interrupted)
        write.assert_not_called()

    def test_queue_replays_approved_file_and_waits_for_real_completion(self):
        ctx = {
            "tts_queue": [
                {"text": "Preview", "emotion": "neutral", "voice_preview": "neutral"}
            ],
            "voice": {},
            "vtube": {"state": "old"},
            "env": {"actived": True},
        }
        processor = Mock()
        processor.main_speaker.name = "Speakers"
        handle = playback.Playback()
        waits = []

        def wait(timeout=None):
            waits.append(ctx["voice"]["is_speaking"])
            if len(waits) == 3:
                handle.done.set()
            return handle.done.is_set()

        processor.play_sound.return_value = handle
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "approved.wav"
            original = np.array([0, 0.25, -0.25], dtype=np.float32)
            wavfile.write(path, 44100, original)
            with (
                patch.dict(voice_diagnostics.PREVIEWS, neutral=path),
                patch.object(voice_diagnostics, "LATEST", Path(directory) / "last.wav"),
                patch.object(
                    voice_diagnostics, "DETAILS", Path(directory) / "last.json"
                ),
                patch.object(handle, "wait", side_effect=wait),
                patch.object(handler, "fish_tts_emo") as synthesize,
            ):
                handler.check_tts_queue(processor, ctx)
                synthesize.assert_not_called()
                np.testing.assert_array_equal(
                    processor.play_sound.call_args.args[0], original
                )
                self.assertEqual(
                    voice_diagnostics.latest_recording()[1]["status"], "played"
                )
        self.assertEqual(waits, [True, True, True, True])  # includes result() wait
        self.assertFalse(ctx["voice"]["is_speaking"])
        self.assertEqual(ctx["vtube"]["state"], "old")

    def test_queue_failure_cleans_up_and_leaves_next_speech_available(self):
        ctx = {
            "tts_queue": [
                {"text": "First", "emotion": "neutral"},
                {"text": "Second", "emotion": "angry"},
            ],
            "voice": {},
            "vtube": {"state": "old"},
            "env": {"actived": True},
        }
        processor = Mock()
        with patch.object(
            handler, "fish_tts_emo", side_effect=RuntimeError("Fish unavailable")
        ):
            handler.check_tts_queue(processor, ctx)
        self.assertEqual(ctx["tts_queue"][0]["text"], "Second")
        self.assertFalse(ctx["voice"]["is_speaking"])
        self.assertEqual(ctx["voice"]["text_chunk"], "")
        self.assertEqual(ctx["vtube"]["state"], "old")
        processor.play_sound.assert_not_called()

    def test_interrupt_poll_does_not_consume_next_speech(self):
        next_entry = {"text": "Next", "emotion": "neutral"}
        ctx = {"tts_queue": [{"text": "interrupt"}, next_entry]}
        self.assertTrue(handler.check_tts_queue(Mock(), ctx, check_interrupt=True))
        self.assertFalse(handler.check_tts_queue(Mock(), ctx, check_interrupt=True))
        self.assertEqual(ctx["tts_queue"], [next_entry])

    def test_preview_selection_never_accepts_arbitrary_file_paths(self):
        ctx = {"tts_queue": []}
        voice_diagnostics.queue_preview(ctx, "../other-file.wav")
        self.assertEqual(ctx["tts_queue"], [])
