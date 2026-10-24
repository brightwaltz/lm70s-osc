"""Betopper LM70S (7x8W RGBW mini moving head) DMX model.

Channel layout follows the LM70S user manual (9CH / 14CH modes).
All parameters are stored normalized (0.0-1.0) and rendered to DMX values.
"""

PARAMS = (
    "pan", "tilt", "dimmer", "strobe",
    "red", "green", "blue", "white",
    "speed", "color", "color_speed", "program",
    "master",
)

CHANNEL_LABELS = {
    9: ("Pan", "Tilt", "Master", "R", "G", "B", "W", "Speed", "Reset"),
    14: ("Pan", "PanF", "Tilt", "TiltF", "Speed", "Master", "R", "G", "B", "W",
         "Color", "CSpd", "Prog", "Reset"),
}

# "Master control" channel: dimming and strobe share one channel.
MASTER_RANGES = {
    9: {"dim": (8, 135), "strobe": (136, 240)},
    14: {"dim": (8, 134), "strobe": (135, 239)},
}

RESET_VALUE = {9: 175, 14: 200}
RESET_HOLD_SECONDS = 5.0


def _dmx8(value):
    return int(round(value * 255))


def _dmx16(value):
    v = int(round(value * 65535))
    return v >> 8, v & 0xFF


class LM70S:
    def __init__(self, name, address, mode=14):
        if mode not in CHANNEL_LABELS:
            raise ValueError("LM70S mode must be 9 or 14 (got %r)" % mode)
        if not 1 <= address <= 513 - mode:
            raise ValueError("DMX address %d does not fit %dch fixture" % (address, mode))
        self.name = name
        self.address = address
        self.mode = mode
        self.state = dict.fromkeys(PARAMS, 0.0)
        self.state.update(pan=0.5, tilt=0.5, master=1.0)
        self._reset_until = 0.0

    @property
    def channels(self):
        return range(self.address, self.address + self.mode)

    def set(self, param, value, now):
        """Apply a normalized value. Returns False for unknown parameters."""
        if param == "reset":
            if value > 0:
                self._reset_until = now + RESET_HOLD_SECONDS
            return True
        if param not in self.state:
            return False
        self.state[param] = min(1.0, max(0.0, value))
        return True

    def render(self, blackout, now):
        s = self.state
        rgbw = [_dmx8(s[c]) for c in ("red", "green", "blue", "white")]
        master = self._master_value(blackout)
        reset = RESET_VALUE[self.mode] if now < self._reset_until else 0
        if self.mode == 9:
            return [_dmx8(s["pan"]), _dmx8(s["tilt"]), master, *rgbw, _dmx8(s["speed"]), reset]
        return [
            *_dmx16(s["pan"]), *_dmx16(s["tilt"]), _dmx8(s["speed"]), master, *rgbw,
            _dmx8(s["color"]), _dmx8(s["color_speed"]), _dmx8(s["program"]), reset,
        ]

    def _master_value(self, blackout):
        s = self.state
        level = 0.0 if blackout else s["dimmer"] * s["master"]
        if level <= 0:
            return 0
        if s["strobe"] > 0:
            lo, hi = MASTER_RANGES[self.mode]["strobe"]
            return lo + int(round(s["strobe"] * (hi - lo)))
        lo, hi = MASTER_RANGES[self.mode]["dim"]
        return lo + int(round(level * (hi - lo)))
