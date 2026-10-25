#!/usr/bin/env python3
"""怪談サイコロ: switch between the prepared ghost-story recordings on the APC40 mkII and light them on the LM70S.

  kaidan_dice.py                 through tools/apc_proxy.py: Live keeps the knobs, SHIFT + MASTER hands the grid over
  kaidan_dice.py --apc direct    own the whole APC40 (Live must not be using it)
  kaidan_dice.py --simulate      no APC40: type commands (h for help)

Clip grid: column = story (stories.json "grid" 1-8), row = part (導入 情景 積み上げ 怪異 結末).
SCENE LAUNCH 1-5: roll the story die / play-pause / next part / "don!" scare / previous part.
TRACK SELECT 1-6: light mood, 7: roll the mood die, 8: mood follows the story. STOP ALL CLIPS: blackout.
CLIP STOP: stop that story. Faders 1-6: voice, ambience, heartbeat, light level, flicker, scare strength.
Master fader: LM70S master. SOLO 1: one-tap demo of everything (~85 s; press again to stop).
LaserCube (through lasercube-local's OSC input): SOLO 2 start/stop, SOLO 3-6 circle / eight / triangle / hands,
SOLO 7 link to the story (colour, speed, hands at scares; never starts or stops it), SOLO 8 next colour,
faders 7-8 brightness / size, crossfader number of hands.
Run build_audio.py once first.
"""

import argparse
import json
import os
import random
import sys
import threading
import time

import mido
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from apc2osc import MASTER_FADER_CC, NOTE_STOP_ALL, NOTE_TRACK_SELECT, TRACK_FADER_CC, grid_note, palette_index  # noqa: E402
from apc_proxy import CROSSFADER_CC, NOTE_CLIP_STOP, NOTE_SOLO, SCENE_LAUNCH_NOTES  # noqa: E402

import apc_io  # noqa: E402
from engine import SR, Engine  # noqa: E402
from laser_link import SHAPES, LaserLink  # noqa: E402
from lights import MOODS, Lights  # noqa: E402

STAGE_BY_PART = ("base", "unease", "tension", "tension", "after")
TENSION_BY_PART = (0.15, 0.35, 0.6, 0.85, 0.3)
FADERS = {0: "voice", 1: "ambience", 2: "heart", 3: "light", 4: "flicker", 5: "scare"}
DEFAULT_FADERS = {"voice": 1.0, "ambience": 0.35, "heart": 0.25, "light": 1.0, "flicker": 0.6, "scare": 0.8}
SL_ROLL, SL_PLAY, SL_NEXT, SL_DON, SL_PREV = SCENE_LAUNCH_NOTES
SL_COLORS = {SL_ROLL: 13, SL_NEXT: 3, SL_DON: 5, SL_PREV: 1}  # palette: yellow, white, red, dark grey
GREEN, DARK_GREEN, WHITE = 21, 23, 3
MOOD_ROLL, MOOD_AUTO = 6, 7  # TRACK SELECT 7 / 8 (channels are 0-based)
ROLL_SECONDS = {"story": 1.2, "mood": 0.8}
BLINK_SECONDS = 0.4
DEMO_BUTTON = 0  # SOLO 1
DEMO_STORY = "oshiire"  # short, with a clear climax
LASER_RUN, LASER_LINK, LASER_COLOUR = 1, 6, 7  # SOLO 2 / 7 / 8
LASER_SHAPE_BUTTONS = {2 + i: shape for i, shape in enumerate(SHAPES)}  # SOLO 3-6
LASER_FADERS = {6: "brightness", 7: "size"}  # track faders 7-8


def dim(rgb, factor):
    return tuple(c // factor for c in rgb)


def build_demo(story):
    """(seconds from start, action, argument): every feature once, about 85 s."""
    parts = story["parts"]
    scare = story["scares"][0] if story["scares"] else parts[3] + 8 * SR
    return [
        (0.0, "faders", {"voice": 1.0, "ambience": 0.35, "heart": 0.0, "light": 1.0, "flicker": 0.6, "scare": 0.8}),
        (0.0, "mood", None),
        (0.0, "roll_story", None),  # the dice animation lands on the demo story, part 1
        (12.0, "part", 1),
        (16.0, "roll_mood", 2),  # candle flicker
        (24.0, "part", 2),
        (24.0, "ramp", ("heart", 0.0, 0.6, 8.0)),
        (24.0, "ramp", ("ambience", 0.35, 0.7, 8.0)),
        (24.0, "ramp", ("flicker", 0.6, 1.0, 8.0)),
        (34.0, "seek", max(parts[3], scare - 7 * SR)),  # plays into the climax: automatic strobe + don
        (45.0, "mood", 4),  # lightning
        (50.0, "don", None),
        (54.0, "blackout", True),
        (57.0, "blackout", False),
        (58.0, "part", 4),
        (58.0, "mood", None),
        (58.0, "ramp", ("heart", 0.6, 0.2, 6.0)),
        (58.0, "ramp", ("ambience", 0.7, 0.35, 6.0)),
        (70.0, "pause", None),
        (72.5, "resume", None),
        (80.0, "ramp", ("voice", 1.0, 0.0, 3.0)),
        (83.5, "end", None),
    ]


class Show:
    def __init__(self, manifest, cache_dir, engine, lights, send_midi=lambda msg: None, log=print, rng=None, laser=None):
        self.engine = engine
        self.lights = lights
        self.laser = laser
        self._laser_version = -1
        self.send_midi = send_midi
        self.log = log
        self.rng = rng or random.Random()
        self.cache_dir = cache_dir
        self.part_names = manifest["part_names"]
        self.columns = [None] * 8
        for story in manifest["stories"]:
            if story.get("grid") and 1 <= story["grid"] <= 8:
                self.columns[story["grid"] - 1] = story
        self.col = None
        self.story = None
        self.part = 0
        self.next_scare = 0
        self.finished = False
        self.mood_override = None
        self.blackout = False
        self.faders = dict(DEFAULT_FADERS)
        self.rolling = None  # [kind, end_time, result]
        self.demo = None
        self._now = 0.0
        self.lock = threading.RLock()
        self._pcm = {}
        self._led = {}
        self._blink_on = True
        self._next_blink = 0.0
        self._calm_at = 0.0
        for name, value in self.faders.items():
            self.set_fader(name, value)
        self._apply_mood()

    @property
    def effective_mood(self):
        return self.mood_override or (self.story or {}).get("mood") or 1

    def _apply_mood(self):
        self.lights.set_mood(self.effective_mood)
        if self.laser:
            self.laser.on_mood(self.effective_mood)

    def pcm(self, story):
        if story["id"] not in self._pcm:
            self._pcm[story["id"]] = np.load(os.path.join(self.cache_dir, story["id"] + ".npy"), mmap_mode="r")
        return self._pcm[story["id"]]

    # --- APC40 input ---

    def handle(self, msg):
        with self.lock:
            if msg.type == "control_change":
                if msg.control == TRACK_FADER_CC and msg.channel in FADERS:
                    self.set_fader(FADERS[msg.channel], msg.value / 127.0)
                elif msg.control == MASTER_FADER_CC and msg.channel == 0:
                    self.lights.set_master(msg.value / 127.0)
                elif self.laser and msg.control == TRACK_FADER_CC and msg.channel in LASER_FADERS:
                    self.laser.set_level(LASER_FADERS[msg.channel], msg.value / 127.0)
                elif self.laser and msg.control == CROSSFADER_CC and msg.channel == 0:
                    self.laser.set_level("density", msg.value / 127.0)
            elif msg.type == "note_on" and msg.velocity > 0:
                self._press(msg.channel, msg.note)

    def _press(self, channel, note):
        if note == NOTE_SOLO and channel == DEMO_BUTTON:
            self.toggle_demo()
            return
        if note == NOTE_SOLO and self.laser and 1 <= channel <= 7:
            self._press_laser(channel)  # the laser is independent of the demo script
            return
        if self.demo:
            self.stop_demo(stop_story=False)  # a person took over
        if channel == 0 and note < 40:
            self.play_column(note % 8, 4 - note // 8)
        elif note == NOTE_CLIP_STOP and channel < 8:
            if channel == self.col:
                self.stop()
        elif note == NOTE_TRACK_SELECT and channel < 8:
            if channel < 6:
                self.set_mood(channel + 1)
            elif channel == MOOD_ROLL:
                self.roll("mood")
            else:
                self.set_mood(None)
        elif channel == 0 and note == SL_ROLL:
            self.roll("story")
        elif channel == 0 and note == SL_PLAY:
            self.toggle_play()
        elif channel == 0 and note == SL_NEXT:
            self.step(+1)
        elif channel == 0 and note == SL_DON:
            self.scare(manual=True)
        elif channel == 0 and note == SL_PREV:
            self.step(-1)
        elif channel == 0 and note == NOTE_STOP_ALL:
            self.toggle_blackout()

    # --- actions ---

    def play_column(self, col, part=0):
        with self.lock:
            story = self.columns[col]
            if not story:
                return
            start = story["parts"][part]
            if story is self.story:
                self.engine.seek(start)
                self.engine.set_playing(True)
            else:
                self.engine.load(self.pcm(story), start)
                self.col, self.story = col, story
                self._apply_mood()
            self.finished = False
            self.next_scare = sum(1 for s in story["scares"] if s < start)
            self._enter_part(part)
            self.log("▶ %s / %s" % (story["title"], self.part_names[part]))

    def stop(self):
        with self.lock:
            if self.story:
                self.log("■ %s" % self.story["title"])
            self.engine.stop()
            self.col = self.story = None
            self.lights.set_stage("off")
            self.draw_all()

    def toggle_play(self):
        with self.lock:
            if not self.story or self.finished:
                self.roll("story")
                return
            _, playing, _ = self.engine.position()
            self.engine.set_playing(not playing)
            self.log("%s %s" % ("‖" if playing else "▶", self.story["title"]))
            self.draw_buttons()

    def step(self, delta):
        with self.lock:
            if self.story:
                self.play_column(self.col, max(0, min(4, self.part + delta)))

    def scare(self, manual=False):
        with self.lock:
            strength = self.faders["scare"]
            self.lights.scare(strength)
            self.engine.don(strength)
            if self.laser:
                self.laser.on_scare()
            self.engine.tension = 1.0
            self._calm_at = self._clock() + 2.5
            self.log("ドン！" if manual else "  (怪異)")

    def roll(self, kind, result=None):
        with self.lock:
            if result is None and kind == "story":
                choices = [c for c in range(8) if self.columns[c] and c != self.col] or [self.col]
                result = self.rng.choice(choices)
            elif result is None:
                result = self.rng.randint(1, 6)
            self.rolling = [kind, self._clock() + ROLL_SECONDS[kind], result]

    def _clock(self):
        return self._now or time.monotonic()

    def _press_laser(self, channel):
        if channel == LASER_RUN:
            self.laser.toggle_run()
        elif channel in LASER_SHAPE_BUTTONS:
            self.laser.set_shape(LASER_SHAPE_BUTTONS[channel])
        elif channel == LASER_LINK:
            self.laser.set_link(not self.laser.link)
            if self.laser.link:
                self.laser.on_mood(self.effective_mood)
                self.laser.on_tension(TENSION_BY_PART[self.part] if self.story else 0.15)
        elif channel == LASER_COLOUR:
            self.laser.next_colour()
        self.draw_buttons()

    # --- demo ---

    def toggle_demo(self):
        with self.lock:
            if self.demo:
                self.stop_demo()
            else:
                self.start_demo()

    def start_demo(self):
        with self.lock:
            col = next((c for c, s in enumerate(self.columns) if s and s["id"] == DEMO_STORY), None)
            if col is None:
                col = next(c for c, s in enumerate(self.columns) if s)
            self.demo = {"t0": self._clock(), "col": col, "steps": build_demo(self.columns[col]), "i": 0,
                         "ramps": [], "saved": (dict(self.faders), self.mood_override, self.blackout)}
            self.log("デモ開始（SOLO 1 をもう一度押すと止まる）")

    def stop_demo(self, stop_story=True):
        with self.lock:
            if not self.demo:
                return
            faders, mood, blackout = self.demo["saved"]
            self.demo = None
            if stop_story:
                self.stop()
            for name, value in faders.items():
                self.set_fader(name, value)
            if self.mood_override != mood:
                self.set_mood(mood)
            if self.blackout != blackout:
                self.toggle_blackout()
            self.log("デモ終了")
            self.draw_buttons()

    def _run_demo(self, now):
        demo = self.demo
        elapsed = now - demo["t0"]
        while self.demo is demo and demo["i"] < len(demo["steps"]) and demo["steps"][demo["i"]][0] <= elapsed:
            _, action, arg = demo["steps"][demo["i"]]
            demo["i"] += 1
            self._demo_action(action, arg, now)
        if self.demo is not demo:
            return
        for ramp in list(demo["ramps"]):
            name, start, end, seconds, began = ramp
            x = min(1.0, (now - began) / seconds)
            self.set_fader(name, start + (end - start) * x)
            if x >= 1.0:
                demo["ramps"].remove(ramp)

    def _demo_action(self, action, arg, now):
        col = self.demo["col"]
        if action == "faders":
            for name, value in arg.items():
                self.set_fader(name, value)
        elif action == "mood":
            self.set_mood(arg)
        elif action == "roll_story":
            self.roll("story", result=col)
        elif action == "roll_mood":
            self.roll("mood", result=arg)
        elif action == "part":
            if self.col != col or self.part != arg:  # the story may already have played into it
                self.play_column(col, arg)
        elif action == "seek":
            part = max(i for i, offset in enumerate(self.story["parts"]) if arg >= offset or i == 0)
            self.engine.seek(arg)
            self.engine.set_playing(True)
            self.next_scare = sum(1 for s in self.story["scares"] if s < arg)
            self._enter_part(part)
            self.log("▶ %s / %s（山場の手前へ）" % (self.story["title"], self.part_names[part]))
        elif action == "ramp":
            self.demo["ramps"].append([*arg, now])
        elif action == "don":
            self.scare(manual=True)
        elif action == "blackout":
            if self.blackout != arg:
                self.toggle_blackout()
        elif action in ("pause", "resume"):
            self.engine.set_playing(action == "resume")
            self.log("‖ 一時停止" if action == "pause" else "▶ 再開")
            self.draw_buttons()
        elif action == "end":
            self.stop_demo()

    def set_mood(self, face):
        with self.lock:
            self.mood_override = face
            self._apply_mood()
            self.log("照明: %s%s" % (MOODS[self.effective_mood]["name"], "" if face else "（話に合わせる）"))
            self.draw_moods()
            self.draw_grid()

    def toggle_blackout(self):
        with self.lock:
            self.blackout = not self.blackout
            self.lights.set_blackout(self.blackout)
            self.log("ブラックアウト %s" % ("ON" if self.blackout else "off"))
            self.draw_buttons()

    def set_fader(self, name, value):
        self.faders[name] = value
        if name in ("voice", "ambience", "heart"):
            self.engine.set_gain(name, value)
        elif name == "light":
            self.lights.set_level(value)
        elif name == "flicker":
            self.lights.set_flicker(value)

    def _enter_part(self, part):
        self.part = part
        self.lights.set_stage(STAGE_BY_PART[part])
        self.engine.tension = TENSION_BY_PART[part]
        if self.laser:
            self.laser.on_tension(TENSION_BY_PART[part])
        self.draw_all()

    # --- time ---

    def tick(self, now):
        with self.lock:
            self._now = now
            if self.demo:
                self._run_demo(now)
            if self.rolling:
                kind, end, result = self.rolling
                if now >= end:
                    self.rolling = None
                    if kind == "story":
                        self.play_column(result, 0)
                    else:
                        self.set_mood(result)
                else:
                    self._draw_roll(kind, end - now)
            if self.laser and self.laser.version != self._laser_version:
                self.draw_buttons()
            if self._calm_at and now >= self._calm_at:
                self._calm_at = 0.0
                self.engine.tension = TENSION_BY_PART[self.part]
            if self.story:
                pos, playing, ended = self.engine.position()
                part = max(i for i, offset in enumerate(self.story["parts"]) if pos >= offset or i == 0)
                if part != self.part:
                    self._enter_part(part)
                    self.log("  %s / %s" % (self.story["title"], self.part_names[part]))
                scares = self.story["scares"]
                while self.next_scare < len(scares) and pos >= scares[self.next_scare]:
                    if pos - scares[self.next_scare] < SR // 2:  # reached by playing, not by jumping past it
                        self.scare()
                    self.next_scare += 1
                if ended and not self.finished:
                    self.finished = True
                    self.lights.set_stage("after")
                    self.log("おわり: %s" % self.story["title"])
                    self.draw_all()
            if now >= self._next_blink and not self.rolling:
                self._next_blink = now + BLINK_SECONDS
                self._blink_on = not self._blink_on
                if self.story:
                    self.draw_grid()
                self.draw_buttons()

    # --- LEDs ---

    def led(self, note, velocity, channel=0):
        key = (channel, note)
        if self._led.get(key) != velocity:
            self._led[key] = velocity
            self.send_midi(mido.Message("note_on", channel=channel, note=note, velocity=velocity))

    def refresh(self):
        with self.lock:
            self._led.clear()
            self.draw_all()

    def draw_all(self):
        self.draw_grid()
        self.draw_moods()
        self.draw_buttons()

    def draw_grid(self, highlight=None):
        _, playing, _ = self.engine.position()
        for col, story in enumerate(self.columns):
            for row in range(5):
                velocity = 0
                if story:
                    rgb = MOODS[self.mood_override or story["mood"]]["pad"] if story is self.story else MOODS[story["mood"]]["pad"]
                    if highlight is not None:
                        rgb = (255, 255, 255) if col == highlight else dim(rgb, 8)
                    elif story is self.story:
                        if row == self.part and not self.finished:
                            rgb = rgb if (self._blink_on or not playing) else dim(rgb, 3)
                        else:
                            rgb = dim(rgb, 2) if row < self.part or self.finished else dim(rgb, 6)
                    else:
                        rgb = dim(rgb, 5)
                    velocity = palette_index(rgb)
                self.led(grid_note(row, col), velocity)

    def draw_moods(self, highlight=None):
        lit = highlight or self.effective_mood
        for channel in range(6):
            self.led(NOTE_TRACK_SELECT, 127 if lit == channel + 1 else 0, channel)
        self.led(NOTE_TRACK_SELECT, 127 if self.rolling and self.rolling[0] == "mood" else 0, MOOD_ROLL)
        self.led(NOTE_TRACK_SELECT, 127 if self.mood_override is None else 0, MOOD_AUTO)

    def draw_buttons(self):
        _, playing, _ = self.engine.position()
        for note, color in SL_COLORS.items():
            self.led(note, color)
        self.led(SL_PLAY, GREEN if playing else DARK_GREEN)
        self.led(NOTE_STOP_ALL, 127 if self.blackout else 0)
        for channel in range(8):
            self.led(NOTE_CLIP_STOP, 127 if channel == self.col and playing else 0, channel)
        self.led(NOTE_SOLO, 127 if not self.demo or self._blink_on else 0, DEMO_BUTTON)  # blinks during the demo
        if self.laser:
            self._laser_version = self.laser.version
            self.led(NOTE_SOLO, 127 if self.laser.running else 0, LASER_RUN)
            for channel, shape in LASER_SHAPE_BUTTONS.items():
                self.led(NOTE_SOLO, 127 if self.laser.shape == shape else 0, channel)
            self.led(NOTE_SOLO, 127 if self.laser.link else 0, LASER_LINK)
            self.led(NOTE_SOLO, 0, LASER_COLOUR)

    def _draw_roll(self, kind, remaining):
        step = int(max(0.0, ROLL_SECONDS[kind] - remaining) ** 0.6 * 14)  # fast at first, slowing down
        if kind == "story":
            filled = [c for c in range(8) if self.columns[c]]
            self.draw_grid(highlight=filled[step % len(filled)])
        else:
            self.draw_moods(highlight=step % 6 + 1)

    def clear_leds(self):
        for col in range(8):
            for row in range(5):
                self.led(grid_note(row, col), 0)
        for channel in range(8):
            self.led(NOTE_TRACK_SELECT, 0, channel)
            self.led(NOTE_CLIP_STOP, 0, channel)
            self.led(NOTE_SOLO, 0, channel)
        for note in SCENE_LAUNCH_NOTES + (NOTE_STOP_ALL,):
            self.led(note, 0)


SIM_HELP = """commands: p COL [PART]  play (COL 1-8, PART 1-5) | r roll story | s play/pause | n next | b back | d don!
          x stop | k blackout | m FACE / m r / m a  mood 1-6, roll, auto | f N V  fader N (1-6) = V (0-1)
          M V  master | demo  one-tap demo (SOLO 1) | i state | q quit
          laser: L  start/stop | L circle/eight/triangle/hands | L link | L colour | L b/z/d V  brightness/size/hands"""


def simulate(show, stop):
    print(SIM_HELP)
    for line in sys.stdin:
        cmd = line.split()
        if not cmd:
            continue
        try:
            op, args = cmd[0], cmd[1:]
            if op == "q":
                break
            if op == "p":
                show.play_column(int(args[0]) - 1, int(args[1]) - 1 if len(args) > 1 else 0)
            elif op == "r":
                show.roll("story")
            elif op == "s":
                show.toggle_play()
            elif op == "n":
                show.step(+1)
            elif op == "b":
                show.step(-1)
            elif op == "d":
                show.scare(manual=True)
            elif op == "x":
                show.stop()
            elif op == "k":
                show.toggle_blackout()
            elif op == "m":
                if args[0] == "r":
                    show.roll("mood")
                else:
                    show.set_mood(None if args[0] == "a" else int(args[0]))
            elif op == "f":
                show.set_fader(FADERS[int(args[0]) - 1], float(args[1]))
            elif op == "M":
                show.lights.set_master(float(args[0]))
            elif op == "demo":
                show.toggle_demo()
            elif op == "L" and show.laser:
                if not args:
                    show._press_laser(LASER_RUN)
                elif args[0] in SHAPES:
                    show._press_laser(next(c for c, s in LASER_SHAPE_BUTTONS.items() if s == args[0]))
                elif args[0] == "link":
                    show._press_laser(LASER_LINK)
                elif args[0] == "colour":
                    show._press_laser(LASER_COLOUR)
                else:
                    show.laser.set_level({"b": "brightness", "z": "size", "d": "density"}[args[0]], float(args[1]))
            elif op == "i":
                pos, playing, _ = show.engine.position()
                print("story=%s part=%s pos=%.1fs playing=%s mood=%s faders=%s" % (
                    show.story and show.story["title"], show.part + 1, pos / SR, playing,
                    MOODS[show.effective_mood]["name"], show.faders))
            else:
                print(SIM_HELP)
        except (IndexError, ValueError, KeyError):
            print(SIM_HELP)
    stop.set()


def parse_hostport(text):
    host, _, port = text.rpartition(":")
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apc", choices=["proxy", "direct"], default="proxy")
    ap.add_argument("--proxy-host", default="127.0.0.1", help="host running tools/apc_proxy.py")
    ap.add_argument("--simulate", action="store_true", help="no APC40; type commands")
    ap.add_argument("--lights", type=parse_hostport, default=("127.0.0.1", 9000),
                    help="LM70S OSC (osc_relay.py or osc2dmx.py), default 127.0.0.1:9000")
    ap.add_argument("--lights-prefix", default="/lm70s")
    ap.add_argument("--no-lights", action="store_true")
    ap.add_argument("--no-audio", action="store_true", help="advance time without sound output")
    ap.add_argument("--demo", action="store_true", help="start the one-tap demo right away (same as SOLO 1)")
    ap.add_argument("--laser", type=parse_hostport, default=("127.0.0.1", 9002),
                    help="lasercube-local's OSC input, default 127.0.0.1:9002 (9000 is the lights relay)")
    ap.add_argument("--laser-http", type=int, default=8765, help="lasercube-local's page port (default 8765)")
    ap.add_argument("--no-laser", action="store_true")
    args = ap.parse_args()

    cache_dir = os.path.join(HERE, "audio", "cache")
    manifest_path = os.path.join(cache_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise SystemExit("音声がまだ切り出されていません。先に kaidan/build_audio.py を実行してください。")
    manifest = json.load(open(manifest_path))

    engine = Engine()
    stop = threading.Event()
    if args.no_audio:
        def clock():
            last = time.monotonic()
            while not stop.wait(1 / 60):
                now = time.monotonic()
                engine.render(int((now - last) * SR))
                last = now
        threading.Thread(target=clock, daemon=True).start()
    else:
        engine.start()

    lights = Lights(*args.lights, prefix=args.lights_prefix, send=(lambda a, v: None) if args.no_lights else None)
    lights.start()
    log = lambda text: print(text, file=sys.stderr, flush=True)
    laser = None
    if not args.no_laser:
        laser = LaserLink(args.laser[0], args.laser[1], args.laser_http, log=log)
        laser.start()
    show = Show(manifest, cache_dir, engine, lights, log=log, laser=laser)

    link = None
    if args.simulate:
        threading.Thread(target=simulate, args=(show, stop), daemon=True).start()
    elif args.apc == "proxy":
        link = apc_io.open_proxy(show.handle, show.refresh, host=args.proxy_host)
        show.send_midi = link.send
        show.refresh()
        log("apc_proxy.py に接続しました。APC40 で SHIFT + MASTER を押すと、グリッドが怪談サイコロに切り替わります。")
    else:
        link = apc_io.open_direct(show.handle)
        show.send_midi = link.send
        show.refresh()
    log("怪談サイコロ: %d 話（照明 %s:%d%s、レーザー %s）。Ctrl+C で終了。" % (
        sum(1 for c in show.columns if c), args.lights[0], args.lights[1], "、送信なし" if args.no_lights else "",
        "なし" if args.no_laser else "%s:%d" % args.laser))
    if args.demo:
        show.start_demo()
    try:
        while not stop.is_set():
            show.tick(time.monotonic())
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        show.clear_leds()
        engine.close()
        lights.close(dark=True)
        if laser:
            laser.close()  # leaves the laser as it is; lasercube-local stops it on its own safety rules
        if link:
            link.close()


if __name__ == "__main__":
    main()
