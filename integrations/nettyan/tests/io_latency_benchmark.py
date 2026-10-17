"""Speech synthesis / recognition latency benchmark.

Measures REAL latency of the TTS and STT nodes used in the pipeline, using the
SAME client code paths as production (fish_gr.fish_tts_emo, ParakeetSTT,
FasterWhisperSTT). Reports cold-start vs warm latency, mean/min/max over N runs,
and real-time factor (RTF = audio_duration / synth_time) for TTS.

Usage:
    python tests/io_latency_benchmark.py --tts          # Fish Speech + edge-tts
    python tests/io_latency_benchmark.py --stt          # Parakeet + Faster-Whisper
    python tests/io_latency_benchmark.py --all --runs 7
"""

import argparse
import os
import statistics
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

# Representative Russian phrases (short / medium streamer-style replies).
PHRASES = [
    ("short", "Привет, как дела?"),
    ("short", "Ого, ты это видел?"),
    ("medium", "Слушай, я тут подумала — давай построим базу у реки, там безопаснее."),
    ("medium", "Не трогай мой сундук, а то превращу тебя в курицу, поняла меня?"),
]


def _stats(samples):
    return {
        "mean": statistics.mean(samples),
        "min": min(samples),
        "max": max(samples),
        "stdev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
        "n": len(samples),
    }


def _print_block(title, rows, stats):
    print(f"\n--- {title} ---")
    print(f"{'Run':>4} | {'Phrase':>7} | {'Latency':>9} | {'AudioLen':>9} | {'RTF':>6}")
    print(f"{'-'*4}-+-{'-'*7}-+-{'-'*9}-+-{'-'*9}-+-{'-'*6}")
    for r in rows:
        rtf = f"{r['rtf']:.2f}" if r.get("rtf") else "  -"
        alen = f"{r['audio']:.2f}s" if r.get("audio") else "    -"
        print(f"{r['i']:>4} | {r['kind']:>7} | {r['lat']*1000:>7.0f}ms | {alen:>9} | {rtf:>6}")
    if stats:
        print(f"  warm latency (мс): mean {stats['mean']*1000:.0f}, "
              f"min {stats['min']*1000:.0f}, max {stats['max']*1000:.0f}, "
              f"stdev {stats['stdev']*1000:.0f}  (n={stats['n']})")


# --- TTS ---

def bench_fish(runs):
    from tts.fish.fish_gr import fish_tts_emo
    from scipy.io import wavfile  # noqa: F401  (used inside fish_gr)

    rows, warm = [], []
    # Cold call (first request after container start may trigger compile/load).
    t0 = time.perf_counter()
    audio, sr = fish_tts_emo({"text": PHRASES[0][1], "emotion": "neutral"})
    cold = time.perf_counter() - t0
    print(f"\n[Fish Speech 1.5] cold start (первый запрос): {cold*1000:.0f}ms "
          f"(audio {len(audio)/sr:.2f}s)")

    i = 0
    for _ in range(runs):
        for kind, text in PHRASES:
            i += 1
            t0 = time.perf_counter()
            audio, sr = fish_tts_emo({"text": text, "emotion": "neutral"})
            lat = time.perf_counter() - t0
            adur = len(audio) / sr
            warm.append(lat)
            rows.append({"i": i, "kind": kind, "lat": lat, "audio": adur,
                         "rtf": adur / lat if lat else 0})
    _print_block("Fish Speech 1.5 (GPU, контейнер :22231)", rows, _stats(warm))
    return cold, _stats(warm)


def bench_edge(runs):
    import asyncio
    import edge_tts

    VOICE = "ru-RU-SvetlanaNeural"

    async def synth_once(text):
        comm = edge_tts.Communicate(text, VOICE)
        t0 = time.perf_counter()
        t_first = None
        nbytes = 0
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                if t_first is None:
                    t_first = time.perf_counter()
                nbytes += len(chunk["data"])
        t_end = time.perf_counter()
        return (t_first - t0) if t_first else (t_end - t0), t_end - t0, nbytes

    rows, first_chunk = [], []
    # Cold call (DNS + TLS handshake to the cloud endpoint).
    ttfa, total, _ = asyncio.run(synth_once(PHRASES[0][1]))
    print(f"\n[edge-tts] cold start (DNS+TLS+первый чанк): {ttfa*1000:.0f}ms")

    i = 0
    for _ in range(runs):
        for kind, text in PHRASES:
            i += 1
            ttfa, total, _ = asyncio.run(synth_once(text))
            first_chunk.append(ttfa)
            rows.append({"i": i, "kind": kind, "lat": ttfa, "audio": None, "rtf": None})
    _print_block("edge-tts (облако, время до первого аудиочанка)", rows, _stats(first_chunk))
    return None, _stats(first_chunk)


# --- STT ---

def _load_test_audio():
    """Real Russian speech samples from the character voice references."""
    from pydub import AudioSegment
    base = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "resources", "Characters", "Private", "NetTyan", "Voice",
    )
    out = []
    for name in ["arguing", "cursing", "need_help", "AmeliaVoiceShortCuteLaugh"]:
        p = os.path.join(base, name + ".wav")
        if os.path.exists(p):
            seg = AudioSegment.from_wav(p)
            out.append((name, seg))
    return out


def bench_stt(make_client, label, runs):
    audios = _load_test_audio()
    if not audios:
        print(f"[{label}] no test audio found, skipping")
        return None, None
    client = make_client()
    rows, warm = [], []
    # Cold call (model load on first request).
    name0, seg0 = audios[0]
    t0 = time.perf_counter()
    _ = client.transcribe_audio(seg0)
    cold = time.perf_counter() - t0
    print(f"\n[{label}] cold start (первый запрос, загрузка модели): {cold*1000:.0f}ms")

    i = 0
    for _ in range(runs):
        for name, seg in audios:
            i += 1
            dur = len(seg) / 1000.0
            t0 = time.perf_counter()
            txt = client.transcribe_audio(seg)
            lat = time.perf_counter() - t0
            warm.append(lat)
            rows.append({"i": i, "kind": f"{dur:.1f}s", "lat": lat,
                         "audio": dur, "rtf": dur / lat if lat else 0})
    _print_block(label, rows, _stats(warm))
    return cold, _stats(warm)


def make_parakeet():
    from data_collectors.stt.stt_parakeet import ParakeetSTT
    return ParakeetSTT()


def make_fasterwhisper():
    from data_collectors.stt.stt_fasterwhisper import FasterWhisperSTT
    return FasterWhisperSTT(device="cpu")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", action="store_true")
    ap.add_argument("--stt", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    if not (args.tts or args.stt or args.all):
        args.all = True

    summary = {}
    if args.tts or args.all:
        print("\n" + "=" * 70 + "\n  TTS LATENCY\n" + "=" * 70)
        try:
            summary["fish"] = bench_fish(args.runs)
        except Exception as e:
            print(f"[Fish] FAILED: {e}")
        try:
            summary["edge"] = bench_edge(args.runs)
        except Exception as e:
            print(f"[edge-tts] FAILED: {e}")

    if args.stt or args.all:
        print("\n" + "=" * 70 + "\n  STT LATENCY\n" + "=" * 70)
        try:
            summary["parakeet"] = bench_stt(make_parakeet, "Parakeet ONNX (:22233, ЦП)", args.runs)
        except Exception as e:
            print(f"[Parakeet] FAILED: {e}")
        try:
            summary["fasterwhisper"] = bench_stt(make_fasterwhisper, "Faster-Whisper (ЦП)", args.runs)
        except Exception as e:
            print(f"[Faster-Whisper] FAILED: {e}")

    print("\n" + "=" * 70 + "\n  SUMMARY (warm mean / min / max, мс)\n" + "=" * 70)
    for k, v in summary.items():
        if v and v[1]:
            s = v[1]
            cold = f"{v[0]*1000:.0f}ms" if v[0] else "n/a"
            print(f"  {k:<16} cold {cold:>8} | warm {s['mean']*1000:.0f} / "
                  f"{s['min']*1000:.0f} / {s['max']*1000:.0f}")


if __name__ == "__main__":
    main()
