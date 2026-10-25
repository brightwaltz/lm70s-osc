"""Drives lasercube-local (github.com/ideos2040kyui/lasercube-local) from the kaidan dice through its OSC input.

lasercube-local keeps every safety gate: real output needs the operator to arm it in its browser page, and the page
must stay open (it stops 3 s after its heartbeat ends). This link only sends OSC parameters plus start/stop that a
person pressed; story events never turn the laser on or off.
"""

import json
import threading
import time
import urllib.error
import urllib.request

from pythonosc.udp_client import SimpleUDPClient

SHAPES = ("circle", "eight", "triangle", "hands")
SHAPE_NAMES = {"circle": "円", "eight": "8の字", "triangle": "三角", "hands": "赤い手形"}
COLOURS = ("#ed1c24", "#ff6e0a", "#ffd21e", "#28ff6e", "#2ee6ff", "#3c64ff", "#b43cff", "#ffffff")
MOOD_COLOURS = {1: "#5078ff", 2: "#ff6e0a", 3: "#ff0000", 4: "#c8c8ff", 5: "#5a28c8", 6: "#28ff6e"}
SCARE_SECONDS = 3.0
POLL_SECONDS = 0.5


class LaserLink:
    def __init__(self, host="127.0.0.1", osc_port=9002, http_port=8765, log=print, send=None, fetch=None):
        self.osc_port = osc_port
        self.base = "http://%s:%d" % (host, http_port)
        self._client = None if send else SimpleUDPClient(host, osc_port)
        self._send = send or (lambda address, args: self._client.send_message(address, args))
        self._fetch = fetch or self._http
        self.log = log
        self.state = None  # lasercube-local's /api/state, or None while it is not running
        self.version = 0  # bumps when running / shape / reachability change (for LEDs)
        self.link = False
        self.colour_index = 0
        self._token = None
        self._burst = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    # --- lasercube-local's HTTP API (state and OSC set-up only) ---

    def _http(self, method, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Local-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(json.loads(e.read() or b"{}").get("error") or str(e))

    def poll(self):
        try:
            state = self._fetch("GET", "/api/state")
        except Exception:
            state = None
        with self._lock:
            if state and state.get("token") != self._token:  # lasercube-local (re)started
                self._token = state.get("token")
                self._enable_osc(state)
            key = lambda s: s and (s["running"], s["params"]["shape"], s["mode"])
            if key(state) != key(self.state):
                self.version += 1
            self.state = state
            if self._burst and time.monotonic() >= self._burst["until"]:
                self._end_burst()

    def _enable_osc(self, state):
        osc = state.get("osc") or {}
        if osc.get("rx_enabled") and osc.get("rx_port") == self.osc_port:
            return
        try:
            self._fetch("POST", "/api/osc/config", {"rx_enabled": True, "rx_port": self.osc_port}, state["token"])
            self.log("LaserCube Local: OSC の受信を 127.0.0.1:%d で有効にしました" % self.osc_port)
        except Exception as e:
            self.log("LaserCube Local の OSC 受信を有効にできませんでした: %s" % e)

    def _loop(self):
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(POLL_SECONDS)

    # --- state ---

    @property
    def reachable(self):
        return self.state is not None

    @property
    def running(self):
        return bool(self.state and self.state["running"])

    @property
    def shape(self):
        return self.state["params"]["shape"] if self.state else None

    # --- operator actions ---

    def send(self, address, *args):
        self._send(address, list(args))

    def toggle_run(self):
        with self._lock:
            if not self.reachable:
                self.log("LaserCube Local が起動していません")
                return
            stop = self.running
            self.send("/laser/stop" if stop else "/laser/start")
            self.log("レーザー %s" % ("停止" if stop else "開始（実機は画面で出力を許可してあるときだけ）"))
            self._optimistic(running=not stop)

    def set_shape(self, shape):
        with self._lock:
            self._burst = None  # a person chose the shape: do not restore over it
            self.send("/laser/shape", shape)
            self.log("レーザー: %s" % SHAPE_NAMES[shape])
            self._optimistic(shape=shape)

    def next_colour(self):
        with self._lock:
            self._burst = None
            self.colour_index = (self.colour_index + 1) % len(COLOURS)
            self.send("/laser/colour", COLOURS[self.colour_index])
            self.log("レーザーの色: %s" % COLOURS[self.colour_index])

    def set_level(self, name, value):
        """name: brightness / size / density / speed (0.0-1.0, mapped by lasercube-local)."""
        self.send("/laser/" + name, float(min(1.0, max(0.0, value))))

    def set_link(self, on):
        with self._lock:
            self.link = on
            self.log("レーザーを怪談に連動: %s" % ("ON" if on else "OFF"))
            self.version += 1

    # --- story events (only when linked; never start or stop the laser) ---

    def on_mood(self, face):
        if self.link and face in MOOD_COLOURS and not self._burst:
            self.send("/laser/colour", MOOD_COLOURS[face])

    def on_tension(self, tension):
        if self.link:
            self.set_level("speed", 0.05 + 0.45 * tension)

    def on_scare(self):
        with self._lock:
            if not self.link or not self.state:
                return
            params = self.state["params"]
            saved = self._burst["saved"] if self._burst else (params["shape"], params["colour"], params["density"])
            self._burst = {"until": time.monotonic() + SCARE_SECONDS, "saved": saved}
            self.send("/laser/shape", "hands")
            self.send("/laser/colour", "#ff0000")
            self.set_level("density", 1.0)

    def _end_burst(self):
        shape, colour, density = self._burst["saved"]
        self._burst = None
        self.send("/laser/shape", shape)
        self.send("/laser/colour", colour)
        self.set_level("density", (density - 1) / 7)

    def _optimistic(self, **changes):
        """Show the press on the LEDs right away; the next poll corrects it (e.g. a start refused while unarmed)."""
        if self.state:
            state = dict(self.state, params=dict(self.state["params"]))
            if "running" in changes:
                state["running"] = changes["running"]
            if "shape" in changes:
                state["params"]["shape"] = changes["shape"]
            self.state = state
            self.version += 1
