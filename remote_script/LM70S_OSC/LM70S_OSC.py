"""Ableton Live control surface script: Rack macros on the "LM70S" track -> OSC.

Runs in its own Control Surface slot next to the APC40 mkII script, so the APC40 keeps
clip launching while its Device knobs drive the macros. With an MPK mini mk2 as this
slot's Input, its knobs, pads and joystick drive the same macros (see midi_input.py).
"""

import socket

from _Framework.ControlSurface import ControlSurface

from . import config
from .midi_input import MidiInput
from .osc import osc_message

KNOWN_PARAMS = set(config.MACROS.values())


def _ptr(obj):
    return getattr(obj, "_live_ptr", id(obj))


def _param_key(name):
    return name.strip().lower().replace(" ", "_")


def _normalized(param):
    span = param.max - param.min
    return (param.value - param.min) / span if span else 0.0


def _fader(param):
    return min(1.0, param.value / config.FADER_UNITY)


class LM70S_OSC(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._target = (config.OSC_HOST, config.OSC_PORT)
        self._observers = []  # (subject, callback) value listeners
        self._senders = []
        self._params = {}  # osc name -> bound parameter, for the MIDI controller
        self._signature = None
        self._ticks = 0
        self._last_report = None
        self._midi = MidiInput(self, lambda: self._params)
        try:
            self._rebuild()
        except Exception as e:
            self.log_message("LM70S_OSC: initial scan failed: %r" % (e,))

    def disconnect(self):
        self._clear_observers()
        self._sock.close()
        ControlSurface.disconnect(self)

    def build_midi_map(self, midi_map_handle):
        ControlSurface.build_midi_map(self, midi_map_handle)
        if getattr(self, "_midi", None):  # Live may build the map before __init__ finishes
            self._midi.forward(self._c_instance.handle(), midi_map_handle)

    def receive_midi(self, midi_bytes):
        try:
            if getattr(self, "_midi", None) and self._midi.receive(midi_bytes):
                return
        except Exception as e:
            self.log_message("LM70S_OSC: MIDI %r failed: %r" % (midi_bytes, e))
            return
        ControlSurface.receive_midi(self, midi_bytes)

    def update_display(self):
        ControlSurface.update_display(self)
        try:
            self._midi.tick()
        except Exception as e:
            self.log_message("LM70S_OSC: joystick failed: %r" % (e,))
        self._ticks += 1
        try:
            if self._ticks % config.STRUCTURE_CHECK_TICKS == 0 and self._scan()[0] != self._signature:
                self._rebuild()
        except Exception as e:  # e.g. a device deleted mid-scan; retry on the next check
            self.log_message("LM70S_OSC: scan failed: %r" % (e,))
            self._signature = None
        if self._ticks % config.RESEND_TICKS == 0:
            self._send_all()

    def _scan(self):
        """Find the target track/rack. Returns (signature, track, [(osc_name, param, convert)])."""
        track = next((t for t in self.song().tracks
                      if t.name.strip().lower() == config.TRACK_NAME.lower()), None)
        if track is None:
            return None, None, []

        rack = next((d for d in track.devices if d.can_have_chains), None)
        bindings = []
        if rack is not None:
            params = list(rack.parameters)
            macros = [p for p in params if getattr(p, "original_name", "").startswith("Macro")] or params[1:17]
            named = {}
            for p in macros:
                key = _param_key(p.name)
                if key in KNOWN_PARAMS and key not in named:
                    named[key] = p
            claimed = set(_ptr(p) for p in named.values())
            for index, p in enumerate(macros, start=1):
                key = config.MACROS.get(index)
                if key and key not in named and _ptr(p) not in claimed:
                    named[key] = p
            bindings = [(name, p, _normalized) for name, p in sorted(named.items())]

        if config.FADER_PARAM and track.has_audio_output:
            bindings.append((config.FADER_PARAM, track.mixer_device.volume, _fader))

        signature = (_ptr(track), rack and _ptr(rack),
                     tuple((name, _ptr(p)) for name, p, _ in bindings))
        return signature, track, bindings

    def _rebuild(self):
        self._clear_observers()
        self._signature, track, bindings = self._scan()
        if track is None:
            self._report("track '%s' not found" % config.TRACK_NAME)
            return
        for name, param, convert in bindings:
            self._bind(name, param, convert)
        self._params = dict((name, param) for name, param, convert in bindings if convert is _normalized)
        self._report("track '%s': %s -> %s:%d" % (
            track.name, ", ".join(name for name, _, _ in bindings) or "no Rack macros",
            config.OSC_HOST, config.OSC_PORT))
        self._send_all()

    def _bind(self, name, param, convert):
        address = "%s/%s" % (config.OSC_PREFIX, name)

        def send():
            self._send(address, convert(param))

        param.add_value_listener(send)
        self._observers.append((param, send))
        self._senders.append(send)

    def _clear_observers(self):
        for param, callback in self._observers:
            try:
                if param.value_has_listener(callback):
                    param.remove_value_listener(callback)
            except Exception:
                pass  # the device may already be deleted
        self._observers = []
        self._senders = []
        self._params = {}

    def _send_all(self):
        for send in self._senders:
            try:
                send()
            except Exception:
                pass

    def _send(self, address, value):
        try:
            self._sock.sendto(osc_message(address, float(value)), self._target)
        except OSError:
            pass  # bridge not running

    def _report(self, text):
        if text != self._last_report:
            self._last_report = text
            self.log_message("LM70S_OSC: " + text)
            self.show_message("LM70S_OSC: " + text)
