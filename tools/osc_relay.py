#!/usr/bin/env python3
"""Relay this project's OSC to another OSC -> DMX bridge (e.g. the other party's Mac on UDP 9004).

Senders (Ableton's LM70S_OSC, apc2osc.py, osc_send.py) keep sending to 127.0.0.1:9000.
This relay forwards everything to --target, either unchanged or as per-channel DMX values.

  osc_relay.py --target 192.168.10.5:9004                          forward as-is
  osc_relay.py --target 192.168.10.5:9004 --rename /lm70s=/light/1  forward with another prefix
  osc_relay.py --target 192.168.10.5:9004 --mode channels           send /dmx/<ch> <0-255>
  osc_relay.py --target 192.168.10.5:9004 --dry-run                 print only, send nothing
"""

import argparse
import os
import socket
import sys
import threading
import time

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge"))
from lm70s import LM70S  # noqa: E402
from osc2dmx import Bridge, parse_fixture  # noqa: E402


def parse_hostport(text):
    host, sep, port = text.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, got %r" % text)
    return host, int(port)


def parse_rename(text):
    old, sep, new = text.partition("=")
    if not sep or not old.startswith("/") or not new.startswith("/"):
        raise argparse.ArgumentTypeError("expected /OLD=/NEW, got %r" % text)
    return old.rstrip("/"), new.rstrip("/")


class Sender:
    def __init__(self, target, dry_run, verbose):
        host, port = target
        self.dry_run = dry_run
        self.verbose = verbose
        self.sent = 0
        self._last_error = None
        self.client = None
        if not dry_run:
            ip = socket.gethostbyname(host)  # resolve once; Wi-Fi mDNS lookups per packet are slow
            self.client = SimpleUDPClient(ip, port)
            self.label = "%s:%d" % (ip, port) if ip == host else "%s (%s):%d" % (host, ip, port)
        else:
            self.label = "%s:%d (dry run)" % target

    def send(self, address, args):
        self.sent += 1
        if self.dry_run or self.verbose:
            print("-> %s %s" % (address, " ".join(map(str, args))), file=sys.stderr)
        if self.client is None:
            return
        try:
            self.client.send_message(address, args)
        except OSError as e:  # e.g. Wi-Fi dropped: keep running, report once
            if repr(e) != self._last_error:
                self._last_error = repr(e)
                print("send failed: %s" % e, file=sys.stderr)


class Passthrough:
    def __init__(self, sender, renames, changes_only=False):
        self.sender = sender
        self.renames = renames
        self.changes_only = changes_only
        self.last = {}  # (sender's ip:port, address) -> args
        self.current = {}  # address -> args last forwarded, replayed by run()
        self.lock = threading.Lock()

    def handle(self, client, address, *args):
        for old, new in self.renames:
            if address == old or address.startswith(old + "/"):
                address = new + address[len(old):]
                break
        if self.changes_only:
            # Compare per sender: Live's once-a-second resend of an unchanged value is dropped,
            # even while another app keeps changing the same address.
            key = (client, address)
            if self.last.get(key) == args:
                return
            self.last[key] = args
        with self.lock:
            self.current[address] = args
        self.sender.send(address, list(args))

    def run(self, stop, interval):
        """Resend the last forwarded value of every address, so a restarted bridge catches up."""
        while not stop.wait(interval):
            with self.lock:
                items = [(a, args) for a, args in self.current.items() if not a.endswith("/reset")]
            for address, args in items:
                self.sender.send(address, list(args))


class ChannelRelay:
    """Keeps LM70S state with the same model as osc2dmx.py and sends changed DMX channels."""

    def __init__(self, bridge, sender, address_template, value_format, fps, resend):
        self.bridge = bridge
        self.sender = sender
        self.address_template = address_template
        self.value_format = value_format
        self.interval = 1.0 / fps
        self.resend = resend
        self.last = {}

    def _channels(self):
        chans = set()
        for f in self.bridge.fixtures.values():
            chans.update(f.channels)
        with self.bridge.lock:
            chans.update(self.bridge.raw)
        return sorted(chans)

    def _value(self, v):
        return v if self.value_format == "int" else round(v / 255.0, 4)

    def run(self, stop):
        next_full = 0.0
        while not stop.is_set():
            frame = self.bridge.frame()
            now = time.monotonic()
            full = self.resend > 0 and now >= next_full
            if full:
                next_full = now + self.resend
            for ch in self._channels():
                v = frame[ch - 1]
                if full or self.last.get(ch) != v:
                    self.last[ch] = v
                    self.sender.send(self.address_template.format(ch=ch, ch0=ch - 1), [self._value(v)])
            stop.wait(self.interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, type=parse_hostport, help="other bridge HOST:PORT, e.g. 192.168.10.5:9004")
    ap.add_argument("--listen", default="127.0.0.1:9000", help="where our senders send to (default 127.0.0.1:9000)")
    ap.add_argument("--mode", choices=["passthrough", "channels"], default="passthrough")
    ap.add_argument("--rename", action="append", type=parse_rename, default=[], metavar="/OLD=/NEW",
                    help="passthrough: rewrite an address prefix, repeatable")
    ap.add_argument("--changes-only", action="store_true",
                    help="passthrough: forward a message only when its value changed (another app shares the lights)")
    ap.add_argument("--channel-address", default="/dmx/{ch}",
                    help="channels: address template, {ch} = 1-512, {ch0} = 0-511 (default /dmx/{ch})")
    ap.add_argument("--channel-value", choices=["int", "float"], default="int",
                    help="channels: int 0-255 (default) or float 0.0-1.0")
    ap.add_argument("--fixture", action="append", type=parse_fixture, metavar="NAME=ADDR[:MODE]",
                    help="channels: fixture definition like osc2dmx.py (default lm70s=1:14)")
    ap.add_argument("--fps", type=float, default=30.0, help="channels: max sends per second per channel")
    ap.add_argument("--resend", type=float, default=1.0,
                    help="channels, or passthrough with --changes-only: resend the current values this often, 0 = off")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    ap.add_argument("--verbose", action="store_true", help="print every forwarded message")
    args = ap.parse_args()

    sender = Sender(args.target, args.dry_run, args.verbose)
    dispatcher = Dispatcher()
    stop = threading.Event()
    worker = None

    if args.mode == "passthrough":
        relay = Passthrough(sender, args.rename, args.changes_only)
        dispatcher.set_default_handler(relay.handle, needs_reply_address=True)
        if args.changes_only and args.resend > 0:  # without --changes-only the senders' own resends pass through
            worker = threading.Thread(target=relay.run, args=(stop, args.resend), daemon=True)
            worker.start()
    else:
        bridge = Bridge(args.fixture or [LM70S("lm70s", 1, 14)])
        dispatcher.set_default_handler(bridge.handle)
        relay = ChannelRelay(bridge, sender, args.channel_address, args.channel_value, args.fps, args.resend)
        worker = threading.Thread(target=relay.run, args=(stop,), daemon=True)
        worker.start()

    host, _, port = args.listen.rpartition(":")
    try:
        server = BlockingOSCUDPServer((host or "127.0.0.1", int(port)), dispatcher)
    except OSError as e:
        raise SystemExit("cannot listen on %s (%s). Is osc2dmx.py still running on that port?" % (args.listen, e))

    print("relay %s -> %s  mode=%s  (Ctrl+C to quit)" % (args.listen, sender.label, args.mode), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if worker:
            worker.join()
        print("sent %d messages" % sender.sent, file=sys.stderr)


if __name__ == "__main__":
    main()
