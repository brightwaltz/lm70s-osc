"""Connects a Controller to the APC40 mkII: through tools/apc_proxy.py (Live keeps the knobs) or directly."""

import os
import sys
import time

import mido

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from apc2osc import LIVE_MODE, find_port, query_device_id  # noqa: E402
from apc_remote import RemoteAPC40  # noqa: E402


class ApcLink:
    """send(msg) lights the APC40; close() releases it. Input goes to handle(msg)."""

    def __init__(self, send, close):
        self.send = send
        self.close = close


def open_proxy(handle, on_attach, host="127.0.0.1", proxy_port=9111, listen_port=9110):
    """Grid, faders and buttons come from apc_proxy.py after SHIFT + MASTER; knobs stay with Live."""
    apc = RemoteAPC40(host, proxy_port, listen_port, callback=handle, on_attach=on_attach)
    apc.connect()
    return ApcLink(apc.send, apc.close)


def open_direct(handle, port_hint="APC40 mkII"):
    """Owns the whole APC40 (Live must not use it). Knobs come through handle() too."""
    out = mido.open_output(find_port(mido.get_output_names(), port_hint))
    input_name = find_port(mido.get_input_names(), port_hint)
    device_id = query_device_id(out, input_name)
    out.send(mido.Message("sysex", data=[0x47, device_id, 0x29, 0x60, 0x00, 0x04, LIVE_MODE, 0x08, 0x02, 0x01]))
    time.sleep(0.2)
    inp = mido.open_input(input_name, callback=handle)

    def close():
        inp.close()
        out.close()

    return ApcLink(out.send, close)
