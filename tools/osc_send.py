#!/usr/bin/env python3
"""Send test OSC to osc2dmx.py without Ableton.

  osc_send.py /lm70s/dimmer 1          numbers are floats (0.0-1.0)
  osc_send.py /dmx/11 100i             trailing "i" sends an int (raw DMX 0-255)
  osc_send.py demo [--seconds 20]      circle + color cycle, then dark
"""

import argparse
import colorsys
import math
import time

from pythonosc.udp_client import SimpleUDPClient


def parse_value(text):
    return int(text[:-1]) if text.endswith("i") else float(text)


def demo(client, prefix, seconds):
    send = lambda param, value: client.send_message("%s/%s" % (prefix, param), float(value))
    for param, value in (("strobe", 0), ("white", 0), ("speed", 0), ("master", 1), ("dimmer", 1)):
        send(param, value)
    start = time.monotonic()
    while (t := time.monotonic() - start) < seconds:
        send("pan", 0.5 + 0.15 * math.cos(t))
        send("tilt", 0.5 + 0.15 * math.sin(t))
        r, g, b = colorsys.hsv_to_rgb((t / 6) % 1.0, 1.0, 1.0)
        send("red", r)
        send("green", g)
        send("blue", b)
        time.sleep(1 / 30)
    send("dimmer", 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address", help="OSC address, or 'demo'")
    ap.add_argument("values", nargs="*")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--prefix", default="/lm70s", help="fixture prefix for demo")
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()

    client = SimpleUDPClient(args.host, args.port)
    if args.address == "demo":
        demo(client, args.prefix, args.seconds)
    else:
        client.send_message(args.address, [parse_value(v) for v in args.values])


if __name__ == "__main__":
    main()
