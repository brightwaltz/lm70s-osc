#!/usr/bin/env python3
"""OSC -> DMX bridge for Betopper LM70S.

OSC address space (float = normalized 0.0-1.0, int = raw DMX 0-255):
  /<fixture>/<param>   pan tilt dimmer strobe red green blue white speed
                       color color_speed program master reset
  /all/<param>         same, applied to every fixture
  /blackout            1 = all fixtures dark, 0 = release
  /dmx/<channel>       raw override of one DMX channel (1-512)
  /dmx/clear           drop all raw overrides
"""

import argparse
import sys
import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer

from dmx_outputs import ArtNetOutput, EnttecProOutput, MonitorOutput, OpenDmxOutput
from lm70s import LM70S


def to_normalized(arg):
    if isinstance(arg, bool):
        return 1.0 if arg else 0.0
    if isinstance(arg, int):
        return arg / 255.0
    if isinstance(arg, float):
        return arg
    raise ValueError("unsupported OSC argument %r" % (arg,))


class Bridge:
    def __init__(self, fixtures, verbose=False):
        self.fixtures = {}  # unique key -> fixture
        self.groups = {}  # OSC name -> fixtures; fixtures given the same name move together
        for i, f in enumerate(fixtures, start=1):
            self.fixtures[f.name if f.name not in self.fixtures else "%s#%d" % (f.name, i)] = f
            self.groups.setdefault(f.name, []).append(f)
        self.verbose = verbose
        self.blackout = False
        self.raw = {}
        self.lock = threading.Lock()
        self._warned = set()

    def handle(self, address, *args):
        if self.verbose:
            print("OSC %s %s" % (address, " ".join(map(str, args))), file=sys.stderr)
        parts = address.strip("/").split("/")
        if parts == ["dmx", "clear"]:
            with self.lock:
                self.raw.clear()
            return
        if not args:
            return
        try:
            value = to_normalized(args[0])
        except ValueError as e:
            self._warn(address, str(e))
            return

        now = time.monotonic()
        with self.lock:
            if parts == ["blackout"]:
                self.blackout = value > 0
            elif len(parts) == 2 and parts[0] == "dmx" and parts[1].isdigit() and 1 <= int(parts[1]) <= 512:
                self.raw[int(parts[1])] = int(round(min(1.0, max(0.0, value)) * 255))
            elif len(parts) == 2 and (parts[0] == "all" or parts[0] in self.groups):
                targets = self.fixtures.values() if parts[0] == "all" else self.groups[parts[0]]
                if not all([f.set(parts[1], value, now) for f in targets]):
                    self._warn(address, "unknown parameter")
            else:
                self._warn(address, "unknown address")

    def frame(self):
        universe = bytearray(512)
        now = time.monotonic()
        with self.lock:
            for f in self.fixtures.values():
                start = f.address - 1
                values = f.render(self.blackout, now)
                universe[start:start + len(values)] = bytes(values)
            for channel, value in self.raw.items():
                universe[channel - 1] = value
        return bytes(universe)

    def _warn(self, address, reason):
        if address not in self._warned:
            self._warned.add(address)
            print("ignored %s (%s)" % (address, reason), file=sys.stderr)


def parse_fixture(spec):
    """name=address[:mode], e.g. lm70s=1:14"""
    try:
        name, rest = spec.split("=", 1)
        address, _, mode = rest.partition(":")
        return LM70S(name.strip("/"), int(address), int(mode or 14))
    except ValueError as e:
        raise argparse.ArgumentTypeError("bad --fixture %r (%s)" % (spec, e))


def check_overlaps(fixtures):
    used = {}
    for f in fixtures:
        for ch in f.channels:
            if ch in used:
                raise SystemExit("DMX ch%d is used by both %s and %s" % (ch, used[ch], f.name))
            used[ch] = f.name


def open_output(args, fixtures):
    outputs = []
    if args.output == "enttec-pro":
        outputs.append(EnttecProOutput(None if args.port == "auto" else args.port))
    elif args.output == "open-dmx":
        outputs.append(OpenDmxOutput(None if args.port == "auto" else args.port))
    elif args.output == "artnet":
        outputs.append(ArtNetOutput(args.artnet_host, args.artnet_universe))
    if args.output == "monitor" or args.monitor:
        outputs.append(MonitorOutput(fixtures))
    return outputs


def output_loop(bridge, outputs, fps, stop):
    interval = 1.0 / fps
    last_error = None
    while not stop.is_set():
        started = time.monotonic()
        frame = bridge.frame()
        for out in outputs:
            try:
                out.send(frame)
            except Exception as e:  # e.g. USB unplugged / write timeout: keep running, report once
                if repr(e) != last_error:
                    last_error = repr(e)
                    print("DMX output error: %s" % e, file=sys.stderr)
        stop.wait(max(0.0, interval - (time.monotonic() - started)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="0.0.0.0:9000", help="OSC listen host:port (default 0.0.0.0:9000)")
    ap.add_argument("--output", choices=["monitor", "enttec-pro", "open-dmx", "artnet"], default="monitor",
                    help="DMX output (default: monitor = print only, no hardware)")
    ap.add_argument("--port", default="auto", help="serial port for enttec-pro / open-dmx (default: auto)")
    ap.add_argument("--artnet-host", default="255.255.255.255", help="Art-Net node IP (default broadcast)")
    ap.add_argument("--artnet-universe", type=int, default=0)
    ap.add_argument("--fixture", action="append", type=parse_fixture, metavar="NAME=ADDR[:MODE]",
                    help="fixture definition, repeatable; the same NAME twice moves both (default: lm70s=1:14)")
    ap.add_argument("--fps", type=float, default=40.0)
    ap.add_argument("--monitor", action="store_true", help="also print DMX values when using hardware output")
    ap.add_argument("--verbose", action="store_true", help="print every incoming OSC message")
    args = ap.parse_args()

    fixtures = args.fixture or [LM70S("lm70s", 1, 14)]
    check_overlaps(fixtures)
    bridge = Bridge(fixtures, verbose=args.verbose)

    dispatcher = Dispatcher()
    dispatcher.set_default_handler(bridge.handle)
    host, _, port = args.listen.rpartition(":")
    server = BlockingOSCUDPServer((host or "0.0.0.0", int(port)), dispatcher)

    outputs = open_output(args, fixtures)
    stop = threading.Event()
    sender = threading.Thread(target=output_loop, args=(bridge, outputs, args.fps, stop), daemon=True)
    sender.start()

    for f in fixtures:
        print("fixture %s: DMX %d-%d (%dch)  OSC /%s/..." % (f.name, f.address, f.channels[-1], f.mode, f.name),
              file=sys.stderr)
    print("listening OSC on %s, output=%s  (Ctrl+C to quit)" % (args.listen, args.output), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Go dark without moving the head, then stop.
        with bridge.lock:
            bridge.blackout = True
        time.sleep(0.3)
        stop.set()
        sender.join()
        for out in outputs:
            out.close()


if __name__ == "__main__":
    main()
