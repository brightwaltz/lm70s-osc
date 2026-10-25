#!/usr/bin/env python3
"""AI-Driven Bio-Soundnovel「二人弟切・脈」: the teller tells the story on the APC40 mkII, the survivor holds still.

  soundnovel.py                 through tools/apc_proxy.py (SHIFT + MASTER hands the grid over, Live keeps the knobs)
  soundnovel.py --apc direct    own the whole APC40 (Live must not be using it)
  soundnovel.py --simulate      no APC40: type commands (h for help)
  soundnovel.py --demo          play the whole story once, teller and survivor both simulated

Clip grid (novel.json): each pad is a phrase group of the story, in reading order down each column. The bottom
rows are the cut-and-paste mistakes: pressing one turns the horror into a comedy and refills the meter.
SCENE LAUNCH 1-5: start / reset the game, pause / resume, next pad, "don!" scare, silence (stop the voice).
TRACK SELECT 1-6: light mood, 8: mood follows the pad. STOP ALL CLIPS: blackout. CLIP STOP row: the meter.
Faders 1-6: voice, ambience, heartbeat, light level, flicker, scare strength. Master fader: LM70S master.
SOLO 1: one-tap demo. SOLO 2-8, faders 7-8, crossfader: LaserCube, as in kaidan_dice.py.

Heart rate: OSC /bio/hr <bpm> on --bio-port (default 9006) from any sensor. Without it a simulated survivor
reacts to the story. /bio/meter <0-1> sets the meter directly if another system computes it.
Meter page for the audience: http://<this Mac>:8790/ (--web-port). Run novel_build.py once first.
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mido
import numpy as np
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from apc2osc import MASTER_FADER_CC, NOTE_STOP_ALL, NOTE_TRACK_SELECT, TRACK_FADER_CC, grid_note, palette_index  # noqa: E402
from apc_proxy import CROSSFADER_CC, NOTE_CLIP_STOP, NOTE_SOLO, SCENE_LAUNCH_NOTES  # noqa: E402

import apc_io  # noqa: E402
from engine import SR, Engine  # noqa: E402
from kaidan_dice import DEFAULT_FADERS, FADERS, LASER_FADERS, LASER_SHAPE_BUTTONS, dim, parse_hostport  # noqa: E402
from laser_link import LaserLink  # noqa: E402
from lights import MOODS, Lights  # noqa: E402
from meter import Meter, SimSurvivor  # noqa: E402

# What each kind of pad does to the room: light mood, darkness stage, tension of the sound bed, pad colour.
KIND_STYLE = {
    "calm": {"mood": 2, "stage": "base", "tension": 0.15, "pad": (255, 150, 40), "name": "穏やか"},
    "eerie": {"mood": 1, "stage": "unease", "tension": 0.4, "pad": (70, 110, 255), "name": "不穏"},
    "fear": {"mood": 5, "stage": "tension", "tension": 0.65, "pad": (150, 40, 255), "name": "恐怖"},
    "scare": {"mood": 3, "stage": "tension", "tension": 0.8, "pad": (255, 0, 0), "name": "襲撃"},
    "end": {"mood": 1, "stage": "after", "tension": 0.3, "pad": (255, 255, 255), "name": "結末"},
    "trap": {"pad": (255, 220, 0), "name": "切り貼りミス"},
}
SL_START, SL_PLAY, SL_NEXT, SL_DON, SL_SILENCE = SCENE_LAUNCH_NOTES
SL_COLORS = {SL_START: 21, SL_NEXT: 3, SL_DON: 5, SL_SILENCE: 1}  # palette: green, white, red, dark grey
GREEN, DARK_GREEN, RED, YELLOW = 21, 23, 5, 13
MOOD_AUTO = 7  # TRACK SELECT 8
DEMO_BUTTON, LASER_RUN, LASER_LINK, LASER_COLOUR = 0, 1, 6, 7  # SOLO 1 / 2 / 7 / 8
BLINK_SECONDS = 0.4
BIO_FRESH = 3.0  # seconds a sensor reading counts as current
COMEDY_SECONDS = 4.0
PHASE_NAMES = {"ready": "準備", "calibrating": "平常心を測定中", "playing": "プレイ中",
               "teller": "テラーの勝利", "survivor": "サバイバーの勝利"}

# The one-tap demo: the main line with pauses where a teller would hold back ("緩急"), and one wrong pad.
DEMO_SCRIPT = [
    ("pad", "c1r1"), ("gap", 0.6), ("pad", "c1r2"), ("gap", 0.6), ("pad", "c1r3"), ("gap", 2.2),
    ("pad", "c2r1"), ("gap", 0.5), ("pad", "c2r2"), ("gap", 2.4), ("pad", "c2r3"), ("gap", 3.0),
    ("pad", "c3r1"), ("gap", 0.4), ("pad", "c3r2"), ("gap", 0.4), ("pad", "c3r3"), ("gap", 0.2),
    ("pad", "c3r4"), ("gap", 2.5),  # the wrong button: "どうも、お世話になります"
    ("pad", "c4r1"), ("gap", 0.4), ("pad", "c4r2"), ("gap", 1.6), ("pad", "c4r3"), ("gap", 3.0),
    ("pad", "c5r1"), ("gap", 0.5), ("pad", "c5r2"), ("gap", 0.5), ("pad", "c5r3"), ("gap", 1.2),
    ("pad", "c6r1"), ("gap", 2.6), ("pad", "c6r2"), ("gap", 2.0), ("pad", "c6r3"), ("gap", 2.0),
    ("pad", "c7r1"), ("gap", 0.8), ("pad", "c7r2"), ("gap", 4.0),
]


class Novel:
    def __init__(self, manifest, cache_dir, engine, lights, send_midi=lambda msg: None, log=print,
                 laser=None, events=None, simulate_survivor=True):
        self.engine = engine
        self.lights = lights
        self.laser = laser
        self._laser_version = -1
        self.events = events
        self.send_midi = send_midi
        self.log = log
        self.cache_dir = cache_dir
        self.title = manifest["title"]
        self.pads = {(p["col"] - 1, p["row"] - 1): p for p in manifest["pads"]}
        self.by_id = {p["id"]: p for p in manifest["pads"]}
        self.main_line = [p for _, p in sorted(self.pads.items()) if p["kind"] != "trap"]
        self.meter = Meter()
        self.survivor = SimSurvivor() if simulate_survivor else None
        self.lock = threading.RLock()
        self._pcm = {}
        self._led = {}
        self.current = None
        self.next_cue = {"scare": 0, "creak": 0}
        self.comedy_done = False
        self.played = set()
        self.phase = "ready"
        self.started_at = None
        self.ended_at = None
        self.mood_override = None
        self.blackout = False
        self.faders = dict(DEFAULT_FADERS)
        self.hr = None
        self.hr_source = None
        self._bio_hr = None
        self._bio_at = -1e9
        self._meter_override = None
        self.history = []  # (seconds since start, hr, meter)
        self.demo = None
        self._now = 0.0
        self._last_meter_step = None
        self._blink_on = True
        self._next_blink = 0.0
        self._calm_at = 0.0
        self._laugh_until = 0.0
        self._voice_was_playing = False
        for name, value in self.faders.items():
            self.set_fader(name, value)
        self.lights.set_mood(2)
        self.lights.set_stage("base")

    # --- helpers ---

    def _clock(self):
        return self._now or time.monotonic()

    def pcm(self, pad):
        if pad["id"] not in self._pcm:
            self._pcm[pad["id"]] = np.load(os.path.join(self.cache_dir, pad["id"] + ".npy"), mmap_mode="r")
        return self._pcm[pad["id"]]

    def _emit(self, address, *args):
        if self.events:
            try:
                self.events.send_message(address, list(args))
            except OSError:
                pass

    @property
    def effective_mood(self):
        if self.mood_override:
            return self.mood_override
        kind = self.current["kind"] if self.current else "calm"
        return KIND_STYLE.get(kind, {}).get("mood") or self.lights.mood

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
            self._press_laser(channel)
            return
        if self.demo:
            self.stop_demo()  # a person took over
        if channel == 0 and note < 40:
            pad = self.pads.get((note % 8, 4 - note // 8))
            if pad:
                self.play_pad(pad)
        elif note == NOTE_TRACK_SELECT and channel < 8:
            if channel < 6:
                self.set_mood(channel + 1)
            elif channel == MOOD_AUTO:
                self.set_mood(None)
        elif channel == 0 and note == SL_START:
            self.start_game()
        elif channel == 0 and note == SL_PLAY:
            self.toggle_play()
        elif channel == 0 and note == SL_NEXT:
            self.next_pad()
        elif channel == 0 and note == SL_DON:
            self.scare(manual=True)
        elif channel == 0 and note == SL_SILENCE:
            self.silence()
        elif channel == 0 and note == NOTE_STOP_ALL:
            self.toggle_blackout()

    def _press_laser(self, channel):
        if channel == LASER_RUN:
            self.laser.toggle_run()
        elif channel in LASER_SHAPE_BUTTONS:
            self.laser.set_shape(LASER_SHAPE_BUTTONS[channel])
        elif channel == LASER_LINK:
            self.laser.set_link(not self.laser.link)
            if self.laser.link:
                self.laser.on_mood(self.effective_mood)
        elif channel == LASER_COLOUR:
            self.laser.next_colour()
        self.draw_buttons()

    # --- game ---

    def start_game(self):
        with self.lock:
            now = self._clock()
            self.meter.reset(now)
            if self.survivor:
                self.survivor.reset(now)
            self.played.clear()
            self.history = []
            self._meter_override = None
            self.phase = "calibrating"
            self.started_at, self.ended_at = now, None
            self.engine.stop()
            self.current = None
            self.mood_override = None
            self.lights.set_mood(2)
            self.lights.set_stage("base")
            self.engine.tension = 0.15
            self.log("ゲーム開始: %d 秒、サバイバーの平常心を測ります" % self.meter.calibrate_seconds)
            self._emit("/novel/phase", self.phase)
            self.draw_all()

    def _finish(self, winner):
        if self.phase not in ("calibrating", "playing"):
            return
        self.phase = winner
        self.ended_at = self._clock()
        if winner == "teller":
            self.engine.set_playing(False)
            self.lights.scare(1.0)
            self.engine.don(1.0)
            self.mood_override = 3
            self.log("ドキドキ・メーターが尽きた → テラーの勝利！")
        else:
            self.mood_override = 6
            self.lights.set_stage("base")
            self.log("最後まで耐え抜いた → サバイバーの勝利！（残り %d%%）" % round(self.meter.level * 100))
        self._apply_mood()
        self._emit("/novel/phase", self.phase)
        self.draw_all()

    # --- story ---

    def play_pad(self, pad):
        with self.lock:
            if self.phase == "ready":
                self.start_game()
            now = self._clock()
            self.engine.load(self.pcm(pad), 0)
            self.current = pad
            self.played.add(pad["id"])
            self.next_cue = {"scare": 0, "creak": 0}
            self.comedy_done = False
            self._voice_was_playing = True
            style = KIND_STYLE[pad["kind"]]
            if pad["kind"] != "trap":
                self.lights.set_stage(style["stage"])
                self.engine.tension = style["tension"]
                if self.laser:
                    self.laser.on_tension(style["tension"])
                self._apply_mood()
            if self.survivor and pad["kind"] != "trap":  # a wrong pad lands only when its line is over
                self.survivor.on_pad(pad["kind"], now)
            self.log("▶ %s %s「%s」" % (pad["id"], style["name"], pad["title"]))
            self._emit("/novel/pad", pad["id"], pad["kind"], pad["title"])
            self.draw_all()

    def next_pad(self):
        with self.lock:
            if self.current and self.current["kind"] != "trap" and self.current in self.main_line:
                i = self.main_line.index(self.current) + 1
            else:
                i = next((k for k, p in enumerate(self.main_line) if p["id"] not in self.played), len(self.main_line))
            if i < len(self.main_line):
                self.play_pad(self.main_line[i])

    def toggle_play(self):
        with self.lock:
            if not self.current:
                self.next_pad()
                return
            _, playing, ended = self.engine.position()
            if ended:
                self.next_pad()
                return
            self.engine.set_playing(not playing)
            self.draw_buttons()

    def silence(self):
        with self.lock:
            self.engine.stop()
            self.log("（間）")
            self.draw_all()

    def scare(self, manual=False):
        with self.lock:
            strength = self.faders["scare"]
            self.lights.scare(strength)
            self.engine.don(strength)
            if self.laser:
                self.laser.on_scare()
            self.engine.tension = 1.0
            self._calm_at = self._clock() + 2.5
            if self.survivor:
                self.survivor.on_scare(self._clock())
            self.log("ドン！" if manual else "  (襲撃)")
            self._emit("/novel/scare", 1 if manual else 0)

    def comedy(self):
        with self.lock:
            now = self._clock()
            self.lights.comedy()
            self.engine.boing(max(0.4, self.faders["scare"]))
            if self.survivor:
                self.survivor.on_laugh(now)
            self._laugh_until = now + COMEDY_SECONDS
            self.log("  ……ホラーがコメディに（メーター回復）")
            self._emit("/novel/comedy", self.current["id"] if self.current else "")

    def set_mood(self, face):
        with self.lock:
            self.mood_override = face
            self._apply_mood()
            self.log("照明: %s%s" % (MOODS[self.effective_mood]["name"], "" if face else "（パッドに合わせる）"))
            self.draw_moods()

    def _apply_mood(self):
        self.lights.set_mood(self.effective_mood)
        if self.laser:
            self.laser.on_mood(self.effective_mood)

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

    # --- heart rate ---

    def on_bio_hr(self, bpm):
        with self.lock:
            if 30 <= bpm <= 220:
                self._bio_hr, self._bio_at = float(bpm), self._clock()

    def on_bio_meter(self, level):
        with self.lock:
            self._meter_override = min(1.0, max(0.0, float(level)))

    def _step_heart(self, now):
        dt = 0.0 if self._last_meter_step is None else min(0.5, now - self._last_meter_step)
        self._last_meter_step = now
        sim_hr = self.survivor.step(now, dt) if self.survivor else None
        if now - self._bio_at <= BIO_FRESH:
            self.hr, self.hr_source = self._bio_hr, "sensor"
        elif sim_hr is not None:
            self.hr, self.hr_source = sim_hr, "sim"
        else:
            self.hr, self.hr_source = None, None
        laughing = now < self._laugh_until
        if self.phase in ("calibrating", "playing"):
            self.meter.feed(now, self.hr, laughing=laughing)
            if self._meter_override is not None:
                self.meter.level = self._meter_override
            if self.phase == "calibrating" and not self.meter.calibrating:
                self.phase = "playing"
                self.log("平常心 %.0f bpm（%s）。ここから心拍が乱れるとメーターが減ります" % (
                    self.meter.baseline, "センサー" if self.hr_source == "sensor" else "シミュレーション"))
                self._emit("/novel/phase", self.phase)
            if self.phase == "playing" and self.meter.level <= 0.0:
                self._finish("teller")
        self.engine.heart_bpm = self.hr if self.phase != "ready" else None
        if self.started_at is not None and self.phase in ("calibrating", "playing"):
            t = now - self.started_at
            if not self.history or t - self.history[-1][0] >= 0.25:
                self.history.append((round(t, 2), round(self.hr or 0, 1), round(self.meter.level, 3)))
                self._emit("/novel/meter", self.meter.level, self.hr or 0.0)

    # --- demo ---

    def toggle_demo(self):
        with self.lock:
            if self.demo:
                self.stop_demo()
            else:
                self.start_demo()

    def start_demo(self):
        with self.lock:
            self.start_game()
            self.demo = {"i": 0, "wait_until": self._clock() + 1.0, "waiting_pad": None}
            self.log("デモ開始（SOLO 1 をもう一度押すと止まる）")

    def stop_demo(self):
        with self.lock:
            if self.demo:
                self.demo = None
                self.log("デモ終了（ここから手動）")
                self.draw_buttons()

    def _run_demo(self, now):
        demo = self.demo
        if self.phase in ("teller", "survivor"):
            self.demo = None
            self.log("デモ終了")
            self.draw_buttons()
            return
        if demo["waiting_pad"]:
            _, _, ended = self.engine.position()
            if not ended and self.current and self.current["id"] == demo["waiting_pad"]:
                return
            demo["waiting_pad"] = None
            demo["wait_until"] = now
        if now < demo["wait_until"]:
            return
        if demo["i"] >= len(DEMO_SCRIPT):
            self.demo = None
            self.log("デモ終了")
            self.draw_buttons()
            return
        action, arg = DEMO_SCRIPT[demo["i"]]
        demo["i"] += 1
        if action == "pad":
            self.play_pad(self.by_id[arg])
            demo["waiting_pad"] = arg
        else:
            demo["wait_until"] = now + arg

    # --- time ---

    def tick(self, now):
        with self.lock:
            self._now = now
            if self.demo:
                self._run_demo(now)
            if self._calm_at and now >= self._calm_at:
                self._calm_at = 0.0
                if self.current and self.current["kind"] in KIND_STYLE and "tension" in KIND_STYLE[self.current["kind"]]:
                    self.engine.tension = KIND_STYLE[self.current["kind"]]["tension"]
            if self.laser and self.laser.version != self._laser_version:
                self.draw_buttons()
            pos, playing, ended = self.engine.position()
            pad = self.current
            if pad and (playing or ended):
                for kind in ("scare", "creak"):
                    cues = pad[kind]
                    while self.next_cue[kind] < len(cues) and pos >= cues[self.next_cue[kind]]:
                        if pos - cues[self.next_cue[kind]] < SR // 2:  # reached by playing, not by jumping
                            if kind == "scare":
                                self.scare()
                            else:
                                self.lights.flinch()
                                if self.survivor:
                                    self.survivor.on_creak(now)
                        self.next_cue[kind] += 1
                if pad["kind"] == "trap" and not self.comedy_done and pos >= pad["length"] - int(0.15 * SR):
                    self.comedy_done = True
                    self.comedy()
            if playing:
                self._voice_was_playing = True
            elif self._voice_was_playing:  # the voice just stopped: the pad ended, or a pause / silence
                self._voice_was_playing = False
                if ended and pad and pad["kind"] == "end" and self.phase == "playing":
                    self._finish("survivor")
                elif self.survivor:
                    self.survivor.on_silence(now)
                self.draw_all()
            self._step_heart(now)
            if now >= self._next_blink:
                self._next_blink = now + BLINK_SECONDS
                self._blink_on = not self._blink_on
                self.draw_grid()
                self.draw_meter()
                self.draw_buttons()

    # --- state for the meter page ---

    def state(self):
        with self.lock:
            now = self._clock()
            pad = self.current
            pos, playing, _ = self.engine.position()
            return {
                "title": self.title, "phase": self.phase, "phase_name": PHASE_NAMES[self.phase],
                "meter": round(self.meter.level, 3), "baseline": self.meter.baseline,
                "hr": round(self.hr, 1) if self.hr else None, "hr_source": self.hr_source,
                "elapsed": round((self.ended_at or now) - self.started_at, 1) if self.started_at is not None else 0,
                "pad": {"id": pad["id"], "title": pad["title"], "kind": pad["kind"],
                        "kind_name": KIND_STYLE[pad["kind"]]["name"], "text": pad["text"],
                        "progress": round(min(1.0, pos / max(1, pad["length"])), 3) if playing else None} if pad else None,
                "laughing": now < self._laugh_until,
                "played": len(self.played), "total": len(self.main_line),
                "history": self.history[-480:],
            }

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
        self.draw_meter()
        self.draw_buttons()

    def draw_grid(self):
        _, playing, _ = self.engine.position()
        over = self.phase in ("teller", "survivor")
        celebrate = over and self._clock() - self.ended_at < 6.0  # blink the whole grid, then hold
        for col in range(8):
            for row in range(5):
                pad = self.pads.get((col, row))
                velocity = 0
                if pad:
                    rgb = KIND_STYLE[pad["kind"]]["pad"]
                    if over:
                        rgb = (255, 0, 0) if self.phase == "teller" else (0, 255, 60)
                        rgb = rgb if self._blink_on or not celebrate else dim(rgb, 4)
                    elif pad is self.current and playing:
                        rgb = rgb if self._blink_on else dim(rgb, 3)
                    elif pad["id"] in self.played:
                        rgb = dim(rgb, 6)
                    velocity = palette_index(rgb)
                self.led(grid_note(row, col), velocity)

    def draw_meter(self):
        """CLIP STOP 1-8 as the ドキドキ・メーター: full segments lit, the partly drained one blinking."""
        full, rest = divmod(self.meter.level * 8, 1)
        for channel in range(8):
            on = channel < full or (channel == full and rest > 0.02 and self._blink_on)
            self.led(NOTE_CLIP_STOP, 127 if on else 0, channel)

    def draw_moods(self):
        for channel in range(6):
            self.led(NOTE_TRACK_SELECT, 127 if self.effective_mood == channel + 1 else 0, channel)
        self.led(NOTE_TRACK_SELECT, 0, 6)
        self.led(NOTE_TRACK_SELECT, 127 if self.mood_override is None else 0, MOOD_AUTO)

    def draw_buttons(self):
        _, playing, _ = self.engine.position()
        for note, color in SL_COLORS.items():
            if note == SL_START and self.phase in ("teller", "survivor"):
                color = RED if self.phase == "teller" else GREEN  # press to play again
            self.led(note, color)
        self.led(SL_PLAY, GREEN if playing else DARK_GREEN)
        self.led(NOTE_STOP_ALL, 127 if self.blackout else 0)
        self.led(NOTE_SOLO, 127 if not self.demo or self._blink_on else 0, DEMO_BUTTON)
        if self.laser:
            self._laser_version = self.laser.version
            self.led(NOTE_SOLO, 127 if self.laser.running else 0, LASER_RUN)
            for channel, shape in LASER_SHAPE_BUTTONS.items():
                self.led(NOTE_SOLO, 127 if self.laser.shape == shape else 0, channel)
            self.led(NOTE_SOLO, 127 if self.laser.link else 0, LASER_LINK)
            self.led(NOTE_SOLO, 0, LASER_COLOUR)

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


# --- meter page ---

def serve_web(novel, port):
    page = open(os.path.join(HERE, "meter.html"), "rb").read()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/state"):
                body, ctype = json.dumps(novel.state(), ensure_ascii=False).encode(), "application/json; charset=utf-8"
            elif self.path in ("/", "/index.html"):
                body, ctype = page, "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def serve_bio(novel, port):
    dispatcher = Dispatcher()
    dispatcher.map("/bio/hr", lambda address, *args: args and novel.on_bio_hr(float(args[0])))
    dispatcher.map("/bio/meter", lambda address, *args: args and novel.on_bio_meter(float(args[0])))
    server = ThreadingOSCUDPServer(("0.0.0.0", port), dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


SIM_HELP = """commands: p C R  play pad (column 1-8, row 1-5) | n next pad | s pause/resume | d don! | x silence
          start  start / reset the game | hr BPM  pretend sensor heart rate | k blackout | m FACE / m a  mood
          f N V  fader N (1-6) = V (0-1) | demo  one-tap demo | i state | q quit"""


def simulate(novel, stop):
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
                pad = novel.pads.get((int(args[0]) - 1, int(args[1]) - 1))
                if pad:
                    novel.play_pad(pad)
                else:
                    print("そこにパッドはありません")
            elif op == "n":
                novel.next_pad()
            elif op == "s":
                novel.toggle_play()
            elif op == "d":
                novel.scare(manual=True)
            elif op == "x":
                novel.silence()
            elif op == "start":
                novel.start_game()
            elif op == "hr":
                novel.on_bio_hr(float(args[0]))
            elif op == "k":
                novel.toggle_blackout()
            elif op == "m":
                novel.set_mood(None if args[0] == "a" else int(args[0]))
            elif op == "f":
                novel.set_fader(FADERS[int(args[0]) - 1], float(args[1]))
            elif op == "demo":
                novel.toggle_demo()
            elif op == "i":
                s = novel.state()
                print("phase=%s meter=%d%% hr=%s(%s) pad=%s played=%d/%d" % (
                    s["phase"], s["meter"] * 100, s["hr"], s["hr_source"], s["pad"] and s["pad"]["title"],
                    s["played"], s["total"]))
            else:
                print(SIM_HELP)
        except (IndexError, ValueError, KeyError):
            print(SIM_HELP)
    stop.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apc", choices=["proxy", "direct"], default="proxy")
    ap.add_argument("--proxy-host", default="127.0.0.1", help="host running tools/apc_proxy.py")
    ap.add_argument("--simulate", action="store_true", help="no APC40; type commands")
    ap.add_argument("--demo", action="store_true", help="start the one-tap demo right away (same as SOLO 1)")
    ap.add_argument("--lights", type=parse_hostport, default=("127.0.0.1", 9000),
                    help="LM70S OSC (osc_relay.py or osc2dmx.py), default 127.0.0.1:9000")
    ap.add_argument("--lights-prefix", default="/lm70s")
    ap.add_argument("--no-lights", action="store_true")
    ap.add_argument("--no-audio", action="store_true", help="advance time without sound output")
    ap.add_argument("--bio-port", type=int, default=9006, help="OSC /bio/hr input (default 9006)")
    ap.add_argument("--no-sim-survivor", action="store_true", help="the meter only moves with a real sensor")
    ap.add_argument("--web-port", type=int, default=8790, help="meter page (default 8790, 0 = off)")
    ap.add_argument("--events", type=parse_hostport, help="also send /novel/... game events to HOST:PORT")
    ap.add_argument("--laser", type=parse_hostport, default=("127.0.0.1", 9002))
    ap.add_argument("--laser-http", type=int, default=8765)
    ap.add_argument("--no-laser", action="store_true")
    args = ap.parse_args()

    cache_dir = os.path.join(HERE, "audio", "cache", "novel")
    manifest_path = os.path.join(cache_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise SystemExit("パッドの音声がまだありません。先に kaidan/novel_build.py を実行してください。")
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
    events = SimpleUDPClient(*args.events) if args.events else None
    novel = Novel(manifest, cache_dir, engine, lights, log=log, laser=laser, events=events,
                  simulate_survivor=not args.no_sim_survivor)
    serve_bio(novel, args.bio_port)
    if args.web_port:
        serve_web(novel, args.web_port)

    link = None
    if args.simulate:
        threading.Thread(target=simulate, args=(novel, stop), daemon=True).start()
    elif args.apc == "proxy":
        link = apc_io.open_proxy(novel.handle, novel.refresh, host=args.proxy_host)
        novel.send_midi = link.send
        novel.refresh()
        log("apc_proxy.py に接続しました。APC40 で SHIFT + MASTER を押すと、グリッドがサウンドノベルに切り替わります。")
    else:
        link = apc_io.open_direct(novel.handle)
        novel.send_midi = link.send
        novel.refresh()
    log("サウンドノベル「%s」: 本筋 %d パッド + 切り貼りミス %d（照明 %s:%d%s、心拍 OSC :%d、メーター画面 %s）。Ctrl+C で終了。" % (
        novel.title, len(novel.main_line), len(novel.pads) - len(novel.main_line), args.lights[0], args.lights[1],
        "、送信なし" if args.no_lights else "", args.bio_port,
        "http://<この Mac>:%d/" % args.web_port if args.web_port else "なし"))
    if args.demo:
        novel.start_demo()
    try:
        while not stop.is_set():
            novel.tick(time.monotonic())
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        novel.clear_leds()
        engine.close()
        lights.close(dark=True)
        if laser:
            laser.close()
        if link:
            link.close()


if __name__ == "__main__":
    main()
