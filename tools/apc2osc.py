#!/usr/bin/env python3
"""APC40 mkII -> OSC for osc2dmx.py, without Ableton Live.

Knobs, faders and clip-grid presets send OSC; the APC40's LEDs show the current state.
The MIDI layout and init sysex follow Ableton's own APC40_MkII control surface script.
Mapping and presets live in apc2osc_config.py.
"""

import argparse
import importlib.util
import os
import sys
import time

import mido
from pythonosc.udp_client import SimpleUDPClient

# Introduction sysex F0 47 7F 29 60 00 04 <mode> <version x3> F7 (0x29 = APC40 mkII).
# 0x41 = Ableton Live mode: knobs send absolute values on ch0 and the host owns every LED.
LIVE_MODE = 0x41

DEVICE_KNOB_CC = 16  # 16-23, LED ring mode on CC + 8
TRACK_KNOB_CC = 48  # 48-55, LED ring mode on CC + 8
RING_MODE_OFFSET = 8
RING_OFF, RING_SINGLE, RING_VOLUME = 0, 1, 2
TRACK_FADER_CC = 7  # channel = track
MASTER_FADER_CC = 14
NOTE_TRACK_SELECT = 51  # channel = track
NOTE_MASTER_SELECT = 80
NOTE_STOP_ALL = 81
NOTE_SHIFT = 98
TRACK_NOTES = (48, 49, 50, 51, 52, 66)  # arm, solo, mute, select, stop, crossfade
GLOBAL_NOTES = tuple(range(80, 104))  # scene launch, transport, bank, ... buttons
LED_ON = 127

DEFAULTS = {"pan": 0.5, "tilt": 0.5, "master": 1.0}
POSITION_PARAMS = ("pan", "tilt")

# APC40 mkII pad palette (velocity -> RGB), from Ableton's APC40_MkII/Colors.py
PALETTE = (
    0x000000, 0x1E1E1E, 0x7F7F7F, 0xFFFFFF, 0xFF4C4C, 0xFF0000, 0x590000, 0x190000,
    0xFFBD6C, 0xFF5400, 0x591D00, 0x271B00, 0xFFFF4C, 0xFFFF00, 0x595900, 0x191900,
    0x88FF4C, 0x54FF00, 0x1D5900, 0x142B00, 0x4CFF4C, 0x00FF00, 0x005900, 0x001900,
    0x4CFF5E, 0x00FF19, 0x00590D, 0x001902, 0x4CFF88, 0x00FF55, 0x00591D, 0x001F12,
    0x4CFFB7, 0x00FF99, 0x005935, 0x001912, 0x4CC3FF, 0x00A9FF, 0x004152, 0x001019,
    0x4C88FF, 0x0055FF, 0x001D59, 0x000819, 0x4C4CFF, 0x0000FF, 0x000059, 0x000019,
    0x874CFF, 0x5400FF, 0x190064, 0x0F0030, 0xFF4CFF, 0xFF00FF, 0x590059, 0x190019,
    0xFF4C87, 0xFF0054, 0x59001D, 0x220013, 0xFF1500, 0x993500, 0x795100, 0x436400,
    0x033900, 0x005735, 0x00547F, 0x0000FF, 0x00454F, 0x2500CC, 0x7F7F7F, 0x202020,
    0xFF0000, 0xBDFF2D, 0xAFED06, 0x64FF09, 0x108B00, 0x00FF87, 0x00A9FF, 0x002AFF,
    0x3F00FF, 0x7A00FF, 0xB21A7D, 0x402100, 0xFF4A00, 0x88E106, 0x72FF15, 0x00FF00,
    0x3BFF26, 0x59FF71, 0x38FFCC, 0x5B8AFF, 0x3151C6, 0x877FE9, 0xD31DFF, 0xFF005D,
    0xFF7F00, 0xB9B000, 0x90FF00, 0x835D07, 0x392B00, 0x144C10, 0x0D5038, 0x15152A,
    0x16205A, 0x693C1C, 0xA8000A, 0xDE513D, 0xD86A1C, 0xFFE126, 0x9EE12F, 0x67B50F,
    0x1E1E30, 0xDCFF6B, 0x80FFBD, 0x9A99FF, 0x8E66FF, 0x404040, 0x757575, 0xE0FFFF,
    0xA00000, 0x350000, 0x1AD000, 0x074200, 0xB9B000, 0x3F3100, 0xB35F00, 0x4B1502,
)


def palette_index(rgb):
    r, g, b = rgb

    def distance(i):
        c = PALETTE[i]
        return (r - (c >> 16)) ** 2 + (g - (c >> 8 & 0xFF)) ** 2 + (b - (c & 0xFF)) ** 2

    return min(range(1, len(PALETTE)), key=distance)


def grid_note(row, col):
    """row 0 = top row of the clip grid"""
    return 32 + col - 8 * row


def normalized(value):
    return value / 255.0 if isinstance(value, int) else float(value)


class Controller:
    def __init__(self, cfg, send_osc, send_midi, log=lambda text: None):
        self.cfg = cfg
        self.send_osc = send_osc
        self.send_midi = send_midi
        self.log = log
        self.fixtures = list(cfg.FIXTURES)[:8]
        self.state = {t: {} for t in ["all"] + self.fixtures}
        self.target = "all"
        self.shift = False
        self.blackout = False
        self.active_pad = {}
        self.knobs = {DEVICE_KNOB_CC + i: p for i, p in enumerate(cfg.DEVICE_KNOBS[:8]) if p}
        self.knobs.update({TRACK_KNOB_CC + i: p for i, p in enumerate(cfg.TRACK_KNOBS[:8]) if p})
        self.presets = {
            grid_note(r, c): (r, c, preset)
            for r, row in enumerate(cfg.PRESET_ROWS[:5])
            for c, preset in enumerate(row[:8]) if preset
        }

    def value(self, param, target=None):
        return self.state[target or self.target].get(param, DEFAULTS.get(param, 0.0))

    # --- input ---

    def handle(self, msg):
        if msg.type == "control_change":
            self._handle_cc(msg.channel, msg.control, msg.value / 127.0)
        elif msg.type in ("note_on", "note_off"):
            pressed = msg.type == "note_on" and msg.velocity > 0
            if msg.note == NOTE_SHIFT and msg.channel == 0:
                self.shift = pressed
            elif pressed:
                self._handle_press(msg.channel, msg.note)

    def _handle_cc(self, channel, control, value):
        if channel == 0 and control in self.knobs:
            self.set(self.knobs[control], value)
        elif control == TRACK_FADER_CC and self.cfg.TRACK_FADER and channel < len(self.fixtures):
            self.set(self.cfg.TRACK_FADER, value, self.fixtures[channel])
        elif channel == 0 and control == MASTER_FADER_CC and self.cfg.MASTER_FADER:
            self.set(self.cfg.MASTER_FADER, value, "all")

    def _handle_press(self, channel, note):
        if note == NOTE_TRACK_SELECT and channel < len(self.fixtures):
            self.select(self.fixtures[channel])
        elif channel != 0:
            return
        elif note == NOTE_MASTER_SELECT:
            self.select("all")
        elif note == NOTE_STOP_ALL:
            if self.shift:
                self.send_osc("/%s/reset" % self.target, 1.0)
                self.log("reset %s" % self.target)
            else:
                self.blackout = not self.blackout
                self.send_osc("/blackout", 1.0 if self.blackout else 0.0)
                self.send_midi(mido.Message("note_on", note=NOTE_STOP_ALL, velocity=LED_ON if self.blackout else 0))
                self.log("blackout %s" % ("ON" if self.blackout else "off"))
        elif note in self.presets:
            row, col, (label, _, params) = self.presets[note]
            for param, value in params.items():
                self.set(param, value)
            self.active_pad[row] = col
            self.draw_row(row)
            self.log("%s -> %s" % (label, self.target))

    # --- actions ---

    def set(self, param, value, target=None):
        target = target or self.target
        self.send_osc("/%s/%s" % (target, param), value)
        for t in (self.state if target == "all" else [target]):
            self.state[t][param] = normalized(value)
        for cc, knob_param in self.knobs.items():
            if knob_param == param:
                self.draw_ring_value(cc)

    def select(self, target):
        self.target = target
        self.draw_targets()
        for cc in self.knobs:
            self.draw_ring_value(cc)
        self.log("target: %s" % target)

    # --- LEDs ---

    def refresh(self):
        self.clear()
        for row in range(5):
            self.draw_row(row)
        for cc, param in self.knobs.items():
            mode = RING_SINGLE if param in POSITION_PARAMS else RING_VOLUME
            self.send_midi(mido.Message("control_change", control=cc + RING_MODE_OFFSET, value=mode))
            self.draw_ring_value(cc)
        self.draw_targets()

    def clear(self):
        notes = [(0, n) for n in list(range(40)) + list(GLOBAL_NOTES)]
        notes += [(ch, n) for ch in range(8) for n in TRACK_NOTES]
        for channel, note in notes:
            self.send_midi(mido.Message("note_on", channel=channel, note=note, velocity=0))
        for cc in list(range(DEVICE_KNOB_CC, DEVICE_KNOB_CC + 8)) + list(range(TRACK_KNOB_CC, TRACK_KNOB_CC + 8)):
            self.send_midi(mido.Message("control_change", control=cc + RING_MODE_OFFSET, value=RING_OFF))

    def draw_row(self, row):
        for col in range(8):
            entry = self.presets.get(grid_note(row, col))
            velocity = 0
            if entry:
                rgb = entry[2][1]
                if self.active_pad.get(row) != col:
                    rgb = tuple(c // 4 for c in rgb)
                velocity = palette_index(rgb)
            self.send_midi(mido.Message("note_on", note=grid_note(row, col), velocity=velocity))

    def draw_ring_value(self, cc):
        value = int(round(self.value(self.knobs[cc]) * 127))
        self.send_midi(mido.Message("control_change", control=cc, value=value))

    def draw_targets(self):
        for channel in range(8):
            lit = channel < len(self.fixtures) and self.fixtures[channel] == self.target
            self.send_midi(mido.Message("note_on", channel=channel, note=NOTE_TRACK_SELECT,
                                        velocity=LED_ON if lit else 0))
        self.send_midi(mido.Message("note_on", note=NOTE_MASTER_SELECT,
                                    velocity=LED_ON if self.target == "all" else 0))


def load_config(path):
    spec = importlib.util.spec_from_file_location("apc2osc_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def query_device_id(out, input_name, attempts=3, timeout=0.5):
    """Ask the APC40 for its SysEx device id (as Live does); fall back to 0, Live's default."""
    replies = []
    probe = mido.open_input(input_name, callback=replies.append)
    time.sleep(0.2)  # a freshly opened CoreMIDI input misses the ~10 ms reply otherwise
    try:
        for _ in range(attempts):
            out.send(mido.Message("sysex", data=[0x7E, 0x7F, 0x06, 0x01]))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for msg in list(replies):
                    data = msg.data if msg.type == "sysex" else ()
                    if len(data) > 12 and data[0] == 0x7E and tuple(data[2:6]) == (0x06, 0x02, 0x47, 0x29):
                        return data[12]
                time.sleep(0.02)
    finally:
        probe.close()
    print("APC40 did not answer the identity request; using device id 0", file=sys.stderr)
    return 0x00


def find_port(names, hint):
    for name in names:
        if hint.lower() in name.lower():
            return name
    raise SystemExit("MIDI ポート '%s' が見つかりません。候補: %s" % (hint, ", ".join(names) or "(なし)"))


def main():
    default_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apc2osc_config.py")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="osc2dmx.py host (default: OSC_HOST in config)")
    ap.add_argument("--port", type=int, help="osc2dmx.py port (default: OSC_PORT in config)")
    ap.add_argument("--midi-port", default="APC40 mkII", help="part of the MIDI port name")
    ap.add_argument("--config", default=default_config)
    ap.add_argument("--verbose", action="store_true", help="print every OSC message sent")
    args = ap.parse_args()

    cfg = load_config(args.config)
    host, port = args.host or cfg.OSC_HOST, args.port or cfg.OSC_PORT
    client = SimpleUDPClient(host, port)

    def send_osc(address, value):
        client.send_message(address, value)
        if args.verbose:
            print("OSC %s %s" % (address, value), file=sys.stderr)

    out = mido.open_output(find_port(mido.get_output_names(), args.midi_port))
    input_name = find_port(mido.get_input_names(), args.midi_port)
    device_id = query_device_id(out, input_name)
    out.send(mido.Message("sysex", data=[0x47, device_id, 0x29, 0x60, 0x00, 0x04, LIVE_MODE, 0x08, 0x02, 0x01]))
    time.sleep(0.2)
    controller = Controller(cfg, send_osc, out.send, log=lambda text: print(text, file=sys.stderr))
    controller.refresh()
    inp = mido.open_input(input_name, callback=controller.handle)

    print("APC40 mkII (device id 0x%02X) -> OSC %s:%d  fixtures: %s  (Ctrl+C to quit)"
          % (device_id, host, port, ", ".join(controller.fixtures)), file=sys.stderr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        inp.close()
        controller.clear()
        out.close()


if __name__ == "__main__":
    main()
