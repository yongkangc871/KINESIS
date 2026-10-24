"""PC-side sound (mirrors the device buzzer, with adjustable volume) and optional TTS."""
from __future__ import annotations

import queue
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

try:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtMultimedia import QSoundEffect
except ImportError:      # QtMultimedia missing: sound falls back to the device buzzer only
    QSoundEffect = None

RATE = 44100
# Good rep: one beep. Bad rep: two beeps. Fatigue: one long tone. (Same as the device buzzer.)
PATTERNS = {"ok": [(880, 0.08)], "bad": [(440, 0.08), (0, 0.1), (440, 0.08)], "long": [(330, 1.2)]}


def _write_wav(path: Path, pattern):
    chunks = []
    for f, d in pattern:
        t = np.arange(int(RATE * d)) / RATE
        tone = np.sin(2 * np.pi * f * t) if f else np.zeros_like(t)
        ramp = np.minimum(1, np.minimum(t, d - t) / 0.005)   # 5 ms fades, no clicks
        chunks.append(tone * ramp)
    data = (np.concatenate(chunks) * 0.8 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


class HostAudio:
    def __init__(self, volume: float = 0.7, enabled: bool = True):
        self.enabled = enabled and QSoundEffect is not None
        self.effects = {}
        self.volume = volume
        if not self.enabled:
            return
        d = Path(tempfile.gettempdir()) / "wristmon_sounds"
        d.mkdir(exist_ok=True)
        for kind, pattern in PATTERNS.items():
            # The file name carries the pattern, so a changed pattern is never served stale.
            sig = "_".join(f"{f}-{int(t * 1000)}" for f, t in pattern)
            p = d / f"{kind}_{sig}.wav"
            if not p.exists():
                _write_wav(p, pattern)
            e = QSoundEffect()
            e.setSource(QUrl.fromLocalFile(str(p)))
            e.setVolume(volume)
            self.effects[kind] = e

    def set_volume(self, v: float):
        self.volume = v
        for e in self.effects.values():
            e.setVolume(v)

    def play(self, kind: str):
        e = self.effects.get(kind)
        if e is not None and self.volume > 0:
            e.play()


class Speaker:
    """Optional text-to-speech prompts (pyttsx3) on a background thread; never blocks the GUI."""

    def __init__(self, enabled: bool):
        self.q: queue.Queue = queue.Queue(maxsize=2)
        self.enabled = False
        if not enabled:
            return
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            return
        self.enabled = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        while True:
            text = self.q.get()
            engine.say(text)
            engine.runAndWait()

    def say(self, text: str):
        if self.enabled:
            try:
                self.q.put_nowait(text)
            except queue.Full:
                pass    # never queue up stale prompts
