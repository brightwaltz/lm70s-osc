"""Real-time mixer: the story voice plus synthesized ambience, heartbeat and the "don" hit.

All effects are generated with numpy (no sample files). The voice is int16 mono PCM from build_audio.py.
"""

import threading

import numpy as np

SR = 44100
SWITCH_FADE = 0.15  # seconds of fade when jumping to another story or part


def _shaped_noise(n, lo, hi, rng):
    """Band-limited noise made in the frequency domain, so it loops without a seam."""
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spec[(freqs < lo) | (freqs > hi)] = 0
    out = np.fft.irfft(spec, n)
    return out / np.abs(out).max()


def synth_ambience(seconds=24, seed=7):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    # whole cycles per loop for every partial, so the loop point is seamless
    drone = sum(a * np.sin(2 * np.pi * f * t + p) for f, a, p in ((41.25, 0.5, 0.0), (55.0, 0.35, 1.3), (61.75, 0.2, 2.1)))
    drone *= 0.6 + 0.4 * np.sin(2 * np.pi * t / seconds)
    wind = _shaped_noise(n, 120, 900, rng) * (0.55 + 0.45 * np.sin(2 * np.pi * 2 * t / seconds + 0.5))
    out = 0.6 * drone / np.abs(drone).max() + 0.4 * wind
    return (0.5 * out).astype(np.float32)


def synth_heartbeat():
    def thump(freq, length, amp):
        t = np.arange(int(length * SR)) / SR
        return amp * np.sin(2 * np.pi * freq * t) * np.exp(-t * 18) * (1 - np.exp(-t * 400))

    beat = np.zeros(int(0.6 * SR))
    lub, dub = thump(52, 0.25, 1.0), thump(60, 0.22, 0.7)
    beat[:len(lub)] += lub
    start = int(0.28 * SR)
    beat[start:start + len(dub)] += dub
    return (0.9 * beat / np.abs(beat).max()).astype(np.float32)


def synth_boing():
    """A springy cartoon "boing" for the moment the horror falls apart into a comedy."""
    t = np.arange(int(0.6 * SR)) / SR
    freq = 170 + 480 * np.exp(-t * 6) * (1 + 0.3 * np.sin(2 * np.pi * 13 * t))
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 4.5) * (1 - np.exp(-t * 300))
    return (0.8 * tone / np.abs(tone).max()).astype(np.float32)


def synth_don(seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(int(1.8 * SR)) / SR
    freq = 35 + 55 * np.exp(-t * 4)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 2.2)
    hit = _shaped_noise(len(t), 40, 2500, rng) * np.exp(-t * 30)
    out = boom + 0.6 * hit
    return (out / np.abs(out).max()).astype(np.float32)


class Engine:
    """Call start() for sound-card output, or render(frames) to pull samples (tests, --no-audio)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.voice = None
        self.pos = 0
        self.playing = False
        self.ended = False
        self.tension = 0.0
        self.heart_bpm = None  # a real (or simulated) heart rate; None = follow the tension
        self.gain = {"voice": 1.0, "ambience": 0.35, "heart": 0.25, "fx": 0.8}
        self._smooth = dict(self.gain)
        self._ambience = synth_ambience()
        self._amb_pos = 0
        self._heart = synth_heartbeat()
        self._don = synth_don()
        self._boing = synth_boing()
        self._next_beat = 0
        self._oneshots = []  # [samples, position, gain key]
        self._stream = None

    # --- control (any thread) ---

    def load(self, pcm, pos=0, play=True):
        with self.lock:
            self._fade_out_current()
            self.voice = pcm
            self.pos = max(0, min(pos, len(pcm)))
            self.playing = play
            self.ended = False

    def seek(self, pos):
        with self.lock:
            if self.voice is not None:
                self._fade_out_current()
                self.pos = max(0, min(pos, len(self.voice)))
                self.ended = False

    def set_playing(self, playing):
        with self.lock:
            if self.voice is not None and not self.ended:
                if not playing:
                    self._fade_out_current()
                self.playing = playing

    def stop(self):
        with self.lock:
            self._fade_out_current()
            self.voice = None
            self.playing = False

    def don(self, strength=1.0):
        with self.lock:
            self._oneshots.append([self._don * strength, 0, "fx"])

    def boing(self, strength=1.0):
        with self.lock:
            self._oneshots.append([self._boing * strength, 0, "fx"])

    def set_gain(self, key, value):
        with self.lock:
            self.gain[key] = float(value)

    def position(self):
        with self.lock:
            return self.pos, self.playing, self.ended

    # --- output ---

    def start(self):
        import sounddevice as sd

        def callback(outdata, frames, time_info, status):
            mono = self.render(frames)
            outdata[:, 0] = mono
            outdata[:, 1] = mono

        self._stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=512, callback=callback)
        self._stream.start()

    def close(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def render(self, frames):
        with self.lock:
            out = np.zeros(frames, dtype=np.float32)
            g = {}
            for key, target in self.gain.items():  # per-block linear ramp avoids zipper noise
                g[key] = np.linspace(self._smooth[key], target, frames, dtype=np.float32)
                self._smooth[key] = target

            if self.voice is not None and self.playing:
                chunk = self.voice[self.pos:self.pos + frames].astype(np.float32) / 32768.0
                out[:len(chunk)] += chunk * g["voice"][:len(chunk)]
                self.pos += len(chunk)
                if self.pos >= len(self.voice):
                    self.playing = False
                    self.ended = True

            idx = (self._amb_pos + np.arange(frames)) % len(self._ambience)
            self._amb_pos = (self._amb_pos + frames) % len(self._ambience)
            out += self._ambience[idx] * g["ambience"] * (0.35 + 0.65 * self.tension)

            if self.gain["heart"] > 0.01:
                self._next_beat -= frames
                if self._next_beat <= 0:
                    self._oneshots.append([self._heart, 0, "heart"])
                    bpm = self.heart_bpm or 52 + 80 * self.tension
                    self._next_beat += int(SR * 60 / bpm)
            else:
                self._next_beat = 0

            keep = []
            for shot in self._oneshots:
                samples, start, key = shot
                piece = samples[start:start + frames]
                out[:len(piece)] += piece * (g[key][:len(piece)] if key in g else 1.0)
                shot[1] += frames
                if shot[1] < len(samples):
                    keep.append(shot)
            self._oneshots = keep
            return np.clip(out, -1.0, 1.0)

    def _fade_out_current(self):
        """Hand the next 150 ms of the current voice to a one-shot that fades out (called with lock held)."""
        if self.voice is None or not self.playing:
            return
        n = int(SWITCH_FADE * SR)
        tail = self.voice[self.pos:self.pos + n].astype(np.float32) / 32768.0
        if len(tail):
            tail *= np.linspace(1.0, 0.0, len(tail), dtype=np.float32)
            self._oneshots.append([tail, 0, "voice"])
