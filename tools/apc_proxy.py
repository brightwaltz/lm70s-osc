#!/usr/bin/env python3
"""Share one APC40 mkII between Ableton Live and a remote app (e.g. the kaidan dice on PC-A).

Live uses the virtual MIDI ports "APC40 proxy" instead of the APC40 itself; this proxy sits in between.
SHIFT + MASTER toggles who owns the clip grid, CLIP STOP, SOLO, TRACK SELECT, MASTER, STOP ALL CLIPS,
SCENE LAUNCH, the faders and the crossfader:

  live    everything goes to Live, as if the proxy were not there
  remote  those controls go to the remote app over OSC, and its LED messages light them.
          The knobs and every other button stay with Live, so the LM70S knobs keep working.
          The MASTER button is lit while in remote mode.

Remote protocol (OSC over UDP; tools/apc_remote.py is the client side):
  proxy  -> remote  /apc40/midi b    raw MIDI from the APC40 (owned controls in remote mode; SHIFT always)
                    /apc40/attach    remote mode started: redraw every LED
                    /apc40/detach    back to Live mode
  remote -> proxy   /apc40/hello i   the remote app started; i = its OSC port (address = sender's IP)
                    /apc40/midi b    LED MIDI for the APC40 (only owned controls are applied, no sysex)
"""

import argparse
import sys
import threading
import time

import mido
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from apc2osc import (LIVE_MODE, MASTER_FADER_CC, NOTE_MASTER_SELECT, NOTE_SHIFT, NOTE_STOP_ALL,
                     NOTE_TRACK_SELECT, TRACK_FADER_CC, find_port, query_device_id)

# From Ableton's APC40_MkII script: scene launch 82-86 (ch0), clip stop 52 / solo 49 (ch = track),
# crossfader CC 15 (ch0). The clip grid is notes 0-39 on ch0; for LEDs the channel selects blink/pulse.
NOTE_SOLO = 49
NOTE_CLIP_STOP = 52
SCENE_LAUNCH_NOTES = tuple(range(82, 87))
CROSSFADER_CC = 15
GRID_NOTES = tuple(range(40))
TRACK_BUTTON_NOTES = (NOTE_SOLO, NOTE_TRACK_SELECT, NOTE_CLIP_STOP)
GLOBAL_OWNED_NOTES = (NOTE_MASTER_SELECT, NOTE_STOP_ALL) + SCENE_LAUNCH_NOTES
OWNED_NOTES = frozenset(GRID_NOTES + TRACK_BUTTON_NOTES + GLOBAL_OWNED_NOTES)
LED_ON = 127
PROXY_PORT_NAME = "APC40 proxy"


def is_note(msg):
    return msg.type in ("note_on", "note_off")


def owned_input(msg):
    if is_note(msg):
        return msg.note in OWNED_NOTES
    if msg.type == "control_change":
        return msg.control == TRACK_FADER_CC or (msg.channel == 0 and msg.control in (MASTER_FADER_CC, CROSSFADER_CC))
    return False


def led_key(msg):
    """Grid and global buttons are keyed by note (the channel is the LED mode); track buttons by (track, note)."""
    return (msg.channel, msg.note) if msg.note in TRACK_BUTTON_NOTES else msg.note


class Router:
    """All routing decisions; ports are injected so it can be tested without hardware."""

    def __init__(self, to_apc, to_live, to_remote, notify_remote, mode="live", log=lambda text: None):
        self.to_apc = to_apc
        self.to_live = to_live
        self.to_remote = to_remote
        self.notify_remote = notify_remote
        self.log = log
        self.mode = mode
        self.shift = False
        self.swallow_master_release = False
        self.live_leds = {}
        self.remote_leds = {}
        self.lock = threading.RLock()

    def from_apc(self, msg):
        with self.lock:
            if is_note(msg) and msg.channel == 0 and msg.note == NOTE_SHIFT:
                self.shift = msg.type == "note_on" and msg.velocity > 0
                self.to_live(msg)
                if self.mode == "remote":
                    self.to_remote(msg)
                return
            if is_note(msg) and msg.channel == 0 and msg.note == NOTE_MASTER_SELECT:
                pressed = msg.type == "note_on" and msg.velocity > 0
                if pressed and self.shift:
                    self.swallow_master_release = True
                    self.set_mode("live" if self.mode == "remote" else "remote")
                    return
                if not pressed and self.swallow_master_release:
                    self.swallow_master_release = False
                    return
            if self.mode == "remote" and owned_input(msg):
                self.to_remote(msg)
            else:
                self.to_live(msg)

    def from_live(self, msg):
        with self.lock:
            if is_note(msg) and msg.note in OWNED_NOTES:
                self.live_leds[led_key(msg)] = msg
                if self.mode == "remote":
                    return
            self.to_apc(msg)

    def from_remote(self, msg):
        with self.lock:
            if not (is_note(msg) and msg.note in OWNED_NOTES):
                return  # sysex, knob rings, ...: Live owns those
            self.remote_leds[led_key(msg)] = msg
            if self.mode == "remote" and msg.note != NOTE_MASTER_SELECT:
                self.to_apc(msg)

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode
            self._clear_owned_leds()
            leds = self.remote_leds if mode == "remote" else self.live_leds
            for msg in leds.values():
                if not (mode == "remote" and msg.note == NOTE_MASTER_SELECT):
                    self.to_apc(msg)
            if mode == "remote":
                self.to_apc(mido.Message("note_on", note=NOTE_MASTER_SELECT, velocity=LED_ON))
            self.notify_remote("attach" if mode == "remote" else "detach")
            self.log("mode: %s" % ("remote (grid, faders -> remote app)" if mode == "remote" else "live"))

    def _clear_owned_leds(self):
        for note in GRID_NOTES + GLOBAL_OWNED_NOTES:
            self.to_apc(mido.Message("note_on", note=note, velocity=0))
        for track in range(8):
            for note in TRACK_BUTTON_NOTES:
                self.to_apc(mido.Message("note_on", channel=track, note=note, velocity=0))


class RemoteLink:
    """OSC side: learns the remote app's address from /apc40/hello and sends it MIDI as blobs."""

    def __init__(self, router_ref, remote=None, log=lambda text: None):
        self.router_ref = router_ref
        self.client = SimpleUDPClient(*remote) if remote else None
        self.remote = remote
        self.log = log

    def send_midi(self, msg):
        if self.client:
            self.client.send_message("/apc40/midi", bytes(msg.bytes()))

    def notify(self, event):
        if self.client:
            self.client.send_message("/apc40/" + event, [])

    def on_hello(self, client_address, address, *args):
        port = int(args[0]) if args else 9110
        remote = (client_address[0], port)
        if remote != self.remote:
            self.remote = remote
            self.client = SimpleUDPClient(*remote)
            self.log("remote app at %s:%d" % remote)
        if self.router_ref().mode == "remote":
            self.notify("attach")

    def on_midi(self, address, *args):
        if args and isinstance(args[0], (bytes, bytearray)):
            try:
                self.router_ref().from_remote(mido.Message.from_bytes(list(args[0])))
            except ValueError as e:
                self.log("bad MIDI from remote: %s" % e)


def parse_hostport(text):
    host, sep, port = text.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, got %r" % text)
    return host, int(port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", type=int, default=9111, help="OSC port for the remote app's messages (default 9111)")
    ap.add_argument("--remote", type=parse_hostport, metavar="HOST:PORT",
                    help="remote app address; normally learned from its /apc40/hello")
    ap.add_argument("--midi-port", default="APC40 mkII", help="part of the real APC40's MIDI port name")
    ap.add_argument("--start-mode", choices=["live", "remote"], default="live")
    args = ap.parse_args()

    log = lambda text: print(text, file=sys.stderr, flush=True)
    names_in = [n for n in mido.get_input_names() if PROXY_PORT_NAME not in n]
    names_out = [n for n in mido.get_output_names() if PROXY_PORT_NAME not in n]
    apc_out = mido.open_output(find_port(names_out, args.midi_port))
    apc_in_name = find_port(names_in, args.midi_port)
    device_id = query_device_id(apc_out, apc_in_name)
    apc_out.send(mido.Message("sysex", data=[0x47, device_id, 0x29, 0x60, 0x00, 0x04, LIVE_MODE, 0x08, 0x02, 0x01]))

    live_out = mido.open_output(PROXY_PORT_NAME, virtual=True)
    router = None
    link = RemoteLink(lambda: router, args.remote, log)
    router = Router(apc_out.send, live_out.send, link.send_midi, link.notify, mode="live", log=log)
    live_in = mido.open_input(PROXY_PORT_NAME, virtual=True, callback=router.from_live)
    apc_in = mido.open_input(apc_in_name, callback=router.from_apc)

    dispatcher = Dispatcher()
    dispatcher.map("/apc40/hello", link.on_hello, needs_reply_address=True)
    dispatcher.map("/apc40/midi", link.on_midi)
    server = BlockingOSCUDPServer(("0.0.0.0", args.listen), dispatcher)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    if args.start_mode == "remote":
        router.set_mode("remote")
    log("APC40 (device id 0x%02X) <-> Live via virtual port '%s'; remote app OSC on :%d. SHIFT + MASTER toggles."
        % (device_id, PROXY_PORT_NAME, args.listen))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if router.mode == "remote":
            router.set_mode("live")
        server.shutdown()
        for port in (apc_in, live_in, live_out, apc_out):
            port.close()


if __name__ == "__main__":
    main()
