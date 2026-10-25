#!/usr/bin/env python3
"""Client side of apc_proxy.py: use an APC40 mkII plugged into another Mac like a local mido port.

    from apc_remote import RemoteAPC40
    apc = RemoteAPC40("192.168.128.20")              # the Mac running apc_proxy.py
    controller = Controller(cfg, send_osc, apc.send)  # apc.send(mido.Message) lights the remote APC40
    apc.callback = controller.handle                  # APC40 input arrives as mido.Message
    apc.on_attach = controller.refresh                # redraw every LED when the proxy hands over
    apc.connect()

The proxy only passes the clip grid, CLIP STOP, SOLO, TRACK SELECT, MASTER, STOP ALL CLIPS, SCENE LAUNCH,
the faders and the crossfader (plus SHIFT). Knobs stay with Ableton Live on the proxy's Mac.
Do not send the init sysex or identity requests: the proxy (and Live) set the APC40 up.

Quick test without any app:  apc_remote.py --proxy <host>   (pressed pads light up, presses are printed)
"""

import argparse
import itertools
import threading
import time

import mido
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient


class RemoteAPC40:
    def __init__(self, proxy_host, proxy_port=9111, listen_port=9110, callback=None, on_attach=None, on_detach=None):
        self.callback = callback
        self.on_attach = on_attach
        self.on_detach = on_detach
        self.listen_port = listen_port
        self.client = SimpleUDPClient(proxy_host, proxy_port)
        dispatcher = Dispatcher()
        dispatcher.map("/apc40/midi", self._on_midi)
        dispatcher.map("/apc40/attach", lambda address, *args: self.on_attach and self.on_attach())
        dispatcher.map("/apc40/detach", lambda address, *args: self.on_detach and self.on_detach())
        self.server = BlockingOSCUDPServer(("0.0.0.0", listen_port), dispatcher)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def connect(self):
        """Tell the proxy where we listen; it answers /apc40/attach if it is already in remote mode."""
        self.client.send_message("/apc40/hello", [self.listen_port])

    def send(self, msg):
        self.client.send_message("/apc40/midi", bytes(msg.bytes()))

    def close(self):
        self.server.shutdown()

    def _on_midi(self, address, *args):
        if self.callback and args and isinstance(args[0], (bytes, bytearray)):
            self.callback(mido.Message.from_bytes(list(args[0])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy", required=True, help="host running apc_proxy.py")
    ap.add_argument("--proxy-port", type=int, default=9111)
    ap.add_argument("--listen", type=int, default=9110)
    args = ap.parse_args()

    colors = itertools.cycle([5, 9, 13, 21, 37, 45, 53, 3])  # red orange yellow green cyan blue magenta white
    apc = RemoteAPC40(args.proxy, args.proxy_port, args.listen)

    def handle(msg):
        print(msg)
        if msg.type == "note_on" and msg.velocity > 0 and msg.note < 40:
            apc.send(mido.Message("note_on", note=msg.note, velocity=next(colors)))

    apc.callback = handle
    apc.on_attach = lambda: print("attach: proxy handed the grid to us (press SHIFT + MASTER to give it back)")
    apc.on_detach = lambda: print("detach: back to Live")
    apc.connect()
    print("hello sent to %s:%d; listening on :%d. Press SHIFT + MASTER on the APC40." % (args.proxy, args.proxy_port, args.listen))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        apc.close()


if __name__ == "__main__":
    main()
