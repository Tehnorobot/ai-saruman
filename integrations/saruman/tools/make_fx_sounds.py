#!/usr/bin/env python3
"""
Генератор звуковых сигналов интерфейса (resources/Audio/fx).

Все сигналы синтезируются здесь же из синусоид и шума — никаких внешних
сэмплов, никаких чужих прав. Запускается без зависимостей, только stdlib.

Запуск:
    python tools/make_fx_sounds.py          # пересобрать все сигналы
    python tools/make_fx_sounds.py --list   # показать, что будет создано

Реплики диктора (папка records) этим скриптом не создаются — они синтезируются
речью, см. docs/help/CUSTOMIZATION.md, раздел про звуки.
"""

import argparse
import math
import os
import random
import struct
import wave

SR = 44100
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "resources", "Audio", "fx")


# ------------------------------------------------------------------ примитивы

def silence(seconds):
    return [0.0] * int(SR * seconds)


def tone(freq, seconds, volume=0.5, freq_end=None, wave_type="sine"):
    """Тон с необязательным линейным сдвигом частоты (свип)."""
    n = int(SR * seconds)
    out = []
    phase = 0.0
    for i in range(n):
        f = freq if freq_end is None else freq + (freq_end - freq) * (i / max(1, n - 1))
        phase += 2 * math.pi * f / SR
        if wave_type == "square":
            v = 1.0 if math.sin(phase) >= 0 else -1.0
        elif wave_type == "saw":
            v = 2.0 * ((phase / (2 * math.pi)) % 1.0) - 1.0
        else:
            v = math.sin(phase)
        out.append(v * volume)
    return out


def noise(seconds, volume=0.3, seed=0):
    rnd = random.Random(seed)
    return [(rnd.random() * 2 - 1) * volume for _ in range(int(SR * seconds))]


def envelope(samples, attack=0.01, release=0.05):
    """Сглаживает начало и конец, иначе на стыке слышен щелчок."""
    n = len(samples)
    a = max(1, int(SR * attack))
    r = max(1, int(SR * release))
    out = list(samples)
    for i in range(min(a, n)):
        out[i] *= i / a
    for i in range(min(r, n)):
        out[n - 1 - i] *= i / r
    return out


def decay(samples, power=3.0):
    """Экспоненциальное затухание — так звучат колокол и щелчки."""
    n = len(samples)
    return [s * math.exp(-power * i / n) for i, s in enumerate(samples)]


def mix(*tracks):
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    return out


def seq(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def write_wav(rel_path, samples):
    path = os.path.join(ROOT, rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    peak = max((abs(s) for s in samples), default=0.0)
    if peak > 0.99:
        samples = [s * (0.99 / peak) for s in samples]
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767))
                      for s in samples)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames)
    return path, len(samples) / SR


# -------------------------------------------------------------------- сигналы

def build():
    """Возвращает {относительный путь: сэмплы}. Имена совпадают с fx_manifest.yml."""
    s = {}

    # тишина-заглушка: используется в манифесте как пауза
    s["none.wav"] = silence(0.12)

    # включение: короткий подъём частоты
    s["alarms/power_on.wav"] = envelope(tone(180, 0.55, 0.45, freq_end=780))
    # то же, но громче и длиннее — для заметного старта
    s["alarms/power_on_loud.wav"] = envelope(
        mix(tone(140, 0.85, 0.5, freq_end=900),
            tone(280, 0.85, 0.22, freq_end=1800)))
    # выключение: спад
    s["alarms/power_off.wav"] = envelope(tone(700, 0.7, 0.45, freq_end=120))

    # предупреждение: две чередующиеся ноты
    beep_hi = envelope(tone(880, 0.16, 0.5, wave_type="square"), 0.005, 0.02)
    beep_lo = envelope(tone(660, 0.16, 0.5, wave_type="square"), 0.005, 0.02)
    s["alarms/warning.wav"] = seq(beep_hi, silence(0.07), beep_lo, silence(0.07),
                                  beep_hi, silence(0.07), beep_lo)

    # готовность: восходящая двузвучная квинта
    s["alarms/ready.wav"] = seq(
        envelope(tone(660, 0.13, 0.42), 0.005, 0.03),
        envelope(tone(990, 0.22, 0.42), 0.005, 0.08))

    # занято: пульсирующий низкий тон
    pulse = seq(envelope(tone(320, 0.12, 0.4), 0.01, 0.03), silence(0.1))
    s["alarms/busy.wav"] = seq(pulse, pulse, pulse)

    # операция: короткий блип
    s["alarms/operation.wav"] = envelope(tone(1200, 0.09, 0.4), 0.004, 0.03)

    # запись в базу: щелчок плюс блип
    s["alarms/database_add.wav"] = seq(
        decay(noise(0.04, 0.35, seed=1), 8.0),
        envelope(tone(1500, 0.1, 0.35), 0.004, 0.04))

    # размышление: тихое тиканье
    tick = seq(decay(noise(0.03, 0.18, seed=2), 10.0), silence(0.17))
    s["alarms/thinking.wav"] = seq(tick, tick, tick, tick)

    # колокол: стопка обертонов с затуханием
    s["alarms/bell.wav"] = decay(
        mix(tone(587, 1.6, 0.34), tone(1174, 1.6, 0.16), tone(1761, 1.6, 0.07)),
        3.2)

    # крупное событие: глубокий нарастающий гул
    s["alarms/event_major.wav"] = envelope(
        mix(tone(70, 1.6, 0.45, freq_end=110), tone(105, 1.6, 0.2, freq_end=165)),
        0.5, 0.5)

    # критический отказ: нисходящий гул с шумом
    s["alarms/failure.wav"] = envelope(
        mix(tone(300, 1.9, 0.42, freq_end=55), decay(noise(1.9, 0.12, seed=3), 1.2)),
        0.05, 0.6)

    # отказ в доступе: две низкие ноты подряд
    s["alarms/denied.wav"] = seq(
        envelope(tone(240, 0.16, 0.45, wave_type="square"), 0.005, 0.03),
        silence(0.05),
        envelope(tone(180, 0.28, 0.45, wave_type="square"), 0.005, 0.1))

    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="только показать список")
    args = ap.parse_args()

    sounds = build()
    if args.list:
        for name in sorted(sounds):
            print(name)
        return

    total = 0
    for name in sorted(sounds):
        path, dur = write_wav(name, sounds[name])
        total += 1
        print("%-32s %5.2f с  %6d байт" % (name, dur, os.path.getsize(path)))
    print("\nсоздано файлов: %d" % total)


if __name__ == "__main__":
    main()
