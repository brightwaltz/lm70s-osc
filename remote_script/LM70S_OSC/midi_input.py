"""A MIDI controller on this script's own Control Surface input (default: AKAI MPK mini mk2) -> Rack macros.

The knobs, pads and joystick change the LM70S macros inside Live, so Live stays the one place that
holds the light state: the APC40 rings follow, clip automation still works, and the once-a-second
resend never fights a second controller.
"""

import time

import Live

from . import config

_STEP = 1.5 / 127  # about one knob step


def _normalized(param):
    span = param.max - param.min
    return (param.value - param.min) / span if span else 0.0


def _set_normalized(param, value):
    value = min(1.0, max(0.0, value))
    param.value = param.min + value * (param.max - param.min)


class _Pickup:
    """Soft takeover for absolute knobs: ignore a knob until it reaches the parameter's current value."""

    def __init__(self):
        self.engaged = False
        self.last_knob = None
        self.last_set = None

    def accept(self, knob, current):
        if self.engaged and self.last_set is not None and abs(current - self.last_set) > _STEP:
            self.engaged = False  # moved by something else (APC40, automation, joystick)
        if not self.engaged:
            prev = self.last_knob
            crossed = prev is not None and (prev - current) * (knob - current) <= 0
            self.engaged = crossed or abs(knob - current) <= _STEP
        self.last_knob = knob
        return self.engaged


class MidiInput:
    def __init__(self, surface, params):
        self._surface = surface
        self._params = params  # callable -> {osc_name: macro parameter}
        self._channel = config.MIDI_CHANNEL
        self._knobs = dict(config.MIDI_KNOBS)
        self._pads = dict(config.MIDI_PADS)
        self._pickups = dict((cc, _Pickup()) for cc in self._knobs)
        self._waiting = set()
        self._bend = 0.0
        self._last_tick = None

    def forward(self, script_handle, midi_map_handle):
        for cc in self._knobs:
            Live.MidiMap.forward_midi_cc(script_handle, midi_map_handle, self._channel, cc)
        for note in self._pads:
            Live.MidiMap.forward_midi_note(script_handle, midi_map_handle, self._channel, note)
        if config.JOYSTICK_PAN_SPEED:
            Live.MidiMap.forward_midi_pitchbend(script_handle, midi_map_handle, self._channel)

    def receive(self, midi_bytes):
        """Handle one MIDI message. Returns False when it is not ours."""
        if len(midi_bytes) != 3:
            return False
        status, data1, data2 = midi_bytes
        kind, channel = status & 0xF0, status & 0x0F
        if channel != self._channel:
            return False
        if kind == 0xB0 and data1 in self._knobs:
            self._knob(data1, data2 / 127.0)
        elif kind in (0x80, 0x90) and data1 in self._pads:
            if kind == 0x90 and data2 > 0:
                self._pad(data1)
        elif kind == 0xE0 and config.JOYSTICK_PAN_SPEED:
            bend = ((data2 << 7 | data1) - 8192) / 8192.0
            self._bend = 0.0 if abs(bend) < config.JOYSTICK_DEADZONE else bend
        else:
            return False
        return True

    def tick(self):
        """Called from update_display (about every 100 ms): the held joystick moves pan."""
        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else min(0.5, now - self._last_tick)
        self._last_tick = now
        param = self._params().get("pan") if self._bend else None
        if param is not None:
            _set_normalized(param, _normalized(param) + self._bend * config.JOYSTICK_PAN_SPEED * dt)

    def _knob(self, cc, value):
        name = self._knobs[cc]
        param = self._params().get(name)
        if param is None:
            return
        if name in config.MIDI_PICKUP:
            pickup = self._pickups[cc]
            current = _normalized(param)
            if not pickup.accept(value, current):
                if cc not in self._waiting:
                    self._waiting.add(cc)
                    self._surface.show_message("LM70S_OSC: MPK %s knob picks up at %d" % (name, round(current * 127)))
                return
            self._waiting.discard(cc)
            pickup.last_set = value
        _set_normalized(param, value)

    def _pad(self, note):
        label, values = self._pads[note]
        params = self._params()
        for name, value in values.items():
            if name in params:
                _set_normalized(params[name], value)
        self._surface.show_message("LM70S_OSC: MPK pad %s" % label)
