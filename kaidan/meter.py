"""ドキドキ・メーター and a pretend survivor.

The meter starts full. While the survivor's heart rate stays above their calm baseline it drains, a sudden
jump takes a chunk out of it, and laughing (the story fell apart into a comedy) refills it a little.
Empty meter = the teller wins; reaching the end of the story with something left = the survivor wins.

SimSurvivor stands in when no heart-rate sensor is connected. It reacts to what the teller plays, and a scare
hits harder after calm and after a while without scares, so pacing ("緩急") pays off just as with a person.
"""

import math
import random
import statistics
from collections import deque


class Meter:
    def __init__(self, drain_per_bpm=0.0007, deadband=5.0, jump_bpm=12.0, jump_drop=0.05,
                 laugh_refill=0.025, calibrate_seconds=8.0):
        self.drain_per_bpm = drain_per_bpm  # per second, per bpm above baseline + deadband
        self.deadband = deadband
        self.jump_bpm = jump_bpm  # rise within JUMP_WINDOW that counts as a startle
        self.jump_drop = jump_drop
        self.laugh_refill = laugh_refill  # per second while laughing
        self.calibrate_seconds = calibrate_seconds
        self.reset(0.0)

    JUMP_WINDOW = 2.0
    JUMP_COOLDOWN = 3.0

    def reset(self, now):
        self.level = 1.0
        self.baseline = None
        self._samples = []
        self._calibrate_until = now + self.calibrate_seconds
        self._recent = deque()
        self._last_t = None
        self._last_jump = -1e9

    @property
    def calibrating(self):
        return self.baseline is None

    def feed(self, now, hr, laughing=False):
        """Call several times a second with the current heart rate (bpm). Returns the meter level 0-1."""
        if hr is None:
            self._last_t = now
            return self.level
        if self.baseline is None:
            self._samples.append(hr)
            if now >= self._calibrate_until:
                self.baseline = statistics.median(self._samples)
            self._last_t = now
            return self.level
        dt = 0.0 if self._last_t is None else min(0.5, max(0.0, now - self._last_t))
        self._last_t = now
        self._recent.append((now, hr))
        while self._recent and now - self._recent[0][0] > self.JUMP_WINDOW:
            self._recent.popleft()
        if laughing:
            self.level = min(1.0, self.level + self.laugh_refill * dt)
            return self.level
        excess = max(0.0, hr - self.baseline - self.deadband)
        self.level -= self.drain_per_bpm * excess * dt
        lowest = min(h for _, h in self._recent)
        if hr - lowest >= self.jump_bpm and now - self._last_jump >= self.JUMP_COOLDOWN:
            self._last_jump = now
            self.level -= self.jump_drop
        self.level = max(0.0, self.level)
        return self.level


class SimSurvivor:
    REST = 72.0
    AROUSAL_BPM = 38.0  # heart rate at full arousal above rest
    TARGET = {"calm": 0.04, "eerie": 0.22, "fear": 0.42, "scare": 0.5, "end": 0.25, "trap": 0.05}

    def __init__(self, rng=None):
        self.rng = rng or random.Random(1)
        self.reset(0.0)

    def reset(self, now):
        self.arousal = 0.0
        self.target = 0.0
        self.spike = 0.0
        self.last_scare = now - 60.0
        self.laugh_until = 0.0
        self.hr = self.REST
        self._wobble = 0.0

    def on_pad(self, kind, now):
        self.target = self.TARGET.get(kind, self.target)

    def on_silence(self, now):
        """Nothing is being told: tension ebbs slowly, but does not vanish."""
        self.target *= 0.7

    def on_scare(self, now):
        since = now - self.last_scare
        freshness = min(1.0, since / 30.0)  # scares in a row wear off
        contrast = 1.0 + max(0.0, 0.45 - self.arousal)  # a scare out of calm hits harder
        self.spike += 26.0 * (0.3 + 0.7 * freshness) * contrast
        self.last_scare = now

    def on_creak(self, now):
        self.spike += 5.0

    def on_laugh(self, now):
        self.laugh_until = now + 4.0
        self.target = 0.0
        self.arousal *= 0.4
        self.spike *= 0.3

    def laughing(self, now):
        return now < self.laugh_until

    def step(self, now, dt):
        self.arousal += (self.target - self.arousal) * min(1.0, dt / 6.0)
        self.spike *= math.exp(-dt / 5.0)
        self._wobble = 0.9 * self._wobble + 0.1 * self.rng.uniform(-1, 1)
        laugh = 6.0 if self.laughing(now) else 0.0
        self.hr = self.REST + self.AROUSAL_BPM * self.arousal + self.spike + laugh + 2.0 * self._wobble
        return self.hr
