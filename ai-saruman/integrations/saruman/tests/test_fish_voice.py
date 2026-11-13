"""Verify the actual Fish request carries the intended reference and transcript."""

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from scipy.io import wavfile

from character.character_manager import CharacterManager
from tts.fish import fish_gr


class FishVoiceTests(unittest.TestCase):
    def test_full_long_utterance_reaches_fish_without_replacement_or_token_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            generated=Path(directory)/'long.wav'
            wavfile.write(generated,44100,np.zeros(100,dtype=np.int16))
            client=Mock();client.predict.return_value=(str(generated),None)
            reference_text,reference=self.character.resolve_voice_ref('neutral')
            text=('This is the complete requested reply, without a fabricated ending. '*10).strip()
            with patch.object(fish_gr,'client',client):fish_gr.fish_tts(text,reference,reference_text)
            self.assertEqual(client.predict.call_args.kwargs['text'],text)
            self.assertEqual(client.predict.call_args.kwargs['max_new_tokens'],0)

    def setUp(self):
        self.character = CharacterManager("Saruman", force_new=True)
        context = patch.object(fish_gr, "character", self.character)
        context.start()
        self.addCleanup(context.stop)

    def test_request_uses_uploaded_reference_and_current_transcript_without_server_cache(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "generated.wav"
            wavfile.write(
                generated, 44100, np.array([0, 16384, -16384], dtype=np.int16)
            )
            client = Mock()
            client.predict.return_value = (str(generated), None)
            text, reference = self.character.resolve_voice_ref("neutral")
            with (
                patch.object(fish_gr, "client", client),
                patch.object(fish_gr, "change_pitch_speed") as pitch,
            ):
                samples, rate = fish_gr.fish_tts(
                    "Русская проба", reference, text, seed=42
                )
                fish_gr.fish_tts("Вторая проба", reference, text + " Updated.")
            self.assertEqual(rate, 44100)
            self.assertAlmostEqual(float(samples[1]), 0.5, places=3)
            pitch.assert_not_called()
            for request in client.predict.call_args_list:
                self.assertEqual(request.kwargs["reference_audio"]["path"], reference)
                self.assertEqual(request.kwargs["reference_id"], "")
                self.assertEqual(request.kwargs["use_memory_cache"], "off")
            self.assertEqual(client.predict.call_args_list[0].kwargs["seed"], 42)
            self.assertEqual(
                client.predict.call_args.kwargs["reference_text"], text + " Updated."
            )

    def test_emotion_routes_to_operator_recording_in_actual_speech_entry(self):
        for emotion, filename in (
            ("neutral", "neutral.wav"),
            ("happy", "neutral.wav"),
            ("angry", "evil.wav"),
            ("battle", "evil.wav"),
            ("combat", "evil.wav"),
            ("rage", "evil.wav"),
            ("evil", "evil.wav"),
        ):
            with (
                self.subTest(emotion=emotion),
                patch.object(fish_gr, "fish_tts") as synthesize,
                redirect_stdout(StringIO()) as output,
            ):
                fish_gr.fish_tts_emo({"text": "Русская речь", "emotion": emotion})
                request = synthesize.call_args.kwargs
                self.assertEqual(Path(request["ref"]).name, filename)
                self.assertEqual(
                    request["ref_text"], self.character.resolve_voice_ref(emotion)[0]
                )
                self.assertIn("character=Saruman", output.getvalue())
                self.assertIn("sha256=", output.getvalue())
                self.assertEqual(request["seed"], 42)
                self.assertEqual(
                    request["temperature"], 0.7 if filename == "evil.wav" else 0.4
                )

    def test_angry_sampling_reaches_actual_fish_api(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "angry.wav"
            wavfile.write(
                generated, 44100, np.array([0, 16000, -16000], dtype=np.int16)
            )
            client = Mock()
            client.predict.return_value = (str(generated), None)
            with patch.object(fish_gr, "client", client):
                fish_gr.fish_tts_emo({"text": "Привет всем!", "emotion": "angry"})
                fish_gr.fish_tts_emo({"text": "Привет всем!", "emotion": "neutral"})
            angry, neutral = client.predict.call_args_list
            self.assertEqual(angry.kwargs["temperature"], 0.7)
            self.assertEqual(neutral.kwargs["temperature"], 0.4)
            self.assertEqual(angry.kwargs["seed"], neutral.kwargs["seed"])
            self.assertEqual(
                Path(angry.kwargs["reference_audio"]["path"]).name, "evil.wav"
            )

    def test_float_wav_preserves_generated_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "float.wav"
            original = np.array([0, 0.75, -0.5], dtype=np.float32)
            wavfile.write(generated, 44100, original)
            client = Mock()
            client.predict.return_value = (str(generated), None)
            text, reference = self.character.resolve_voice_ref("neutral")
            with patch.object(fish_gr, "client", client):
                samples, _ = fish_gr.fish_tts("Text", reference, text, seed=42)
            np.testing.assert_array_equal(samples, original)

    def test_missing_audio_or_transcript_never_synthesizes_unconditioned_voice(self):
        text, reference = self.character.resolve_voice_ref("neutral")
        with patch.object(fish_gr, "client", Mock()) as client:
            with self.assertRaises(FileNotFoundError):
                fish_gr.fish_tts("Text", "nonexistent-voice-reference.wav", text)
            with self.assertRaises(ValueError):
                fish_gr.fish_tts("Text", reference, "")
            client.predict.assert_not_called()
