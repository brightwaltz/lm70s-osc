"""LM70S lighting cues for the kaidan dice: mood x tension stage, plus flicker effects made in Python."""

import math
import random
import threading
import time

from pythonosc.udp_client import SimpleUDPClient

# Light mood faces 1-6. rgbw is the base color; "pad" is the APC40 pad color for that mood.
MOODS = {
    1: {"name": "青白い月光", "rgbw": (0.15, 0.30, 1.00, 0.25), "fx": None, "pad": (80, 120, 255)},
    2: {"name": "蝋燭のゆらぎ", "rgbw": (1.00, 0.35, 0.03, 0.08), "fx": "candle", "pad": (255, 110, 10)},
    3: {"name": "赤い非常灯", "rgbw": (1.00, 0.00, 0.00, 0.00), "fx": "pulse", "pad": (255, 0, 0)},
    4: {"name": "稲光", "rgbw": (0.25, 0.30, 1.00, 0.40), "fx": "lightning", "pad": (190, 190, 255)},
    5: {"name": "闇", "rgbw": (0.10, 0.08, 0.35, 0.00), "fx": None, "pad": (60, 30, 140)},
    6: {"name": "霧の緑", "rgbw": (0.10, 1.00, 0.35, 0.10), "fx": None, "pad": (40, 255, 110)},
}

# Dimmer per tension stage (before the light-level fader). The scare is a sequence in Lights.scare().
STAGES = {"base": 0.40, "unease": 0.28, "tension": 0.18, "after": 0.08, "off": 0.0}


class Lights:
    def __init__(self, host, port, prefix="/lm70s", send=None, fps=30.0):
        self.prefix = prefix
        self._client = None if send else SimpleUDPClient(host, port)
        self._send_raw = send or (lambda address, value: self._client.send_message(address, value))
        self.interval = 1.0 / fps
        self.mood = 1
        self.stage = "off"
        self.blackout = False
        self.level = 1.0  # light-level fader
        self.flicker = 0.6  # flicker-depth fader
        self._sent = {}
        self._hold_until = 0.0  # the effect loop stays quiet during a scare sequence
        self._flash_end = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def close(self, dark=True):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1)
        if dark:
            self._send("strobe", 0.0, force=True)
            self._send("dimmer", 0.0, force=True)

    # --- cues ---

    def set_mood(self, face):
        with self._lock:
            self.mood = face
            self._apply()

    def set_stage(self, stage):
        with self._lock:
            self.stage = stage
            self._apply()

    def set_blackout(self, on):
        with self._lock:
            self.blackout = on
            if on:  # immediate, even in the middle of a scare sequence
                self._send("strobe", 0.0)
                self._send("dimmer", 0.0)
            else:
                self._apply()

    def set_level(self, value):
        with self._lock:
            self.level = value
            self._apply()

    def set_flicker(self, value):
        with self._lock:
            self.flicker = value

    def set_master(self, value):
        self._send("master", value, force=True)

    def scare(self, strength=1.0):
        """White strobe burst -> 1 s blackout -> the mood color, dark ("after"). Runs in its own thread."""
        if strength <= 0.02:
            return

        def run():
            with self._lock:
                self._hold_until = time.monotonic() + 2.2
                if not self.blackout:
                    for param, value in (("red", 1.0), ("green", 1.0), ("blue", 1.0), ("white", 1.0),
                                         ("strobe", 0.55 + 0.4 * strength), ("dimmer", min(1.0, 0.5 + strength))):
                        self._send(param, value)
            time.sleep(0.5)
            with self._lock:
                self._send("strobe", 0.0)
                self._send("dimmer", 0.0)
            time.sleep(1.0)
            with self._lock:
                self.stage = "after"
                self._hold_until = 0.0
                self._apply()

        threading.Thread(target=run, daemon=True).start()

    def comedy(self, seconds=2.6):
        """Bright colour carousel: the horror just fell apart into a comedy. Runs in its own thread."""
        colours = ((1, 0, 0, 0), (1, 0.8, 0, 0), (0, 1, 0, 0), (0, 0.6, 1, 0), (0.8, 0, 1, 0), (1, 1, 1, 1))

        def run():
            end = time.monotonic() + seconds
            with self._lock:
                self._hold_until = end + 0.3
            i = 0
            while time.monotonic() < end and not self._stop.is_set():
                with self._lock:
                    if not self.blackout:
                        for param, value in zip(("red", "green", "blue", "white"), colours[i % len(colours)]):
                            self._send(param, value)
                        self._send("strobe", 0.0)
                        self._send("dimmer", 0.9 * self.level)
                i += 1
                time.sleep(0.18)
            with self._lock:
                self._hold_until = 0.0
                self._apply()

        threading.Thread(target=run, daemon=True).start()

    def flinch(self):
        """A quick dip of the light, e.g. on each creak of the empty swing."""
        def run():
            with self._lock:
                base = self._base_dimmer()
                if base <= 0 or time.monotonic() < self._hold_until:
                    return
                self._hold_until = time.monotonic() + 0.3
                self._send("dimmer", base * 0.2)
            time.sleep(0.15)
            with self._lock:
                self._hold_until = 0.0
                self._send("dimmer", self._base_dimmer())

        threading.Thread(target=run, daemon=True).start()

    # --- output ---

    def _base_dimmer(self):
        return 0.0 if self.blackout else STAGES.get(self.stage, 0.0) * self.level

    def _apply(self):
        if time.monotonic() < self._hold_until:
            return
        for param, value in zip(("red", "green", "blue", "white"), MOODS[self.mood]["rgbw"]):
            self._send(param, value)
        self._send("strobe", 0.0)
        self._send("dimmer", self._base_dimmer())

    def _send(self, param, value, force=False):
        value = round(min(1.0, max(0.0, value)), 3)
        if not force and self._sent.get(param) == value:
            return
        self._sent[param] = value
        self._send_raw("%s/%s" % (self.prefix, param), value)

    def _loop(self):
        t0 = time.monotonic()
        wobble = 0.0
        next_flash = time.monotonic() + random.uniform(4, 9)
        while not self._stop.wait(self.interval):
            with self._lock:
                now = time.monotonic()
                base = self._base_dimmer()
                fx = MOODS[self.mood]["fx"]
                if now < self._hold_until or base <= 0 or not fx:
                    continue
                depth = self.flicker
                if fx == "candle":
                    wobble = 0.8 * wobble + 0.2 * random.uniform(-1, 1)
                    self._send("dimmer", base * (1 + depth * (0.5 * wobble - 0.1) + 0.04 * random.random()))
                elif fx == "pulse":
                    swing = 0.5 - 0.5 * math.sin((now - t0) * 2.4)
                    self._send("dimmer", base * (1 - 0.6 * depth * swing))
                elif fx == "lightning":
                    if now >= next_flash and depth > 0.05:
                        self._send("dimmer", min(1.0, base + 0.8 * depth))
                        self._send("white", 1.0)
                        next_flash = now + random.uniform(5, 12) / max(depth, 0.2)
                        self._flash_end = now + random.uniform(0.06, 0.15)
                    elif self._flash_end and now >= self._flash_end:
                        self._flash_end = 0.0
                        self._send("white", MOODS[self.mood]["rgbw"][3])
                        self._send("dimmer", base)
