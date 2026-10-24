"""Minimal OSC 1.0 message encoder (Live's embedded Python has no python-osc)."""

import struct


def _pad(data):
    return data + b"\0" * (4 - len(data) % 4)


def osc_message(address, *args):
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        else:
            tags += "s"
            payload += _pad(str(arg).encode("utf-8"))
    return _pad(address.encode("utf-8")) + _pad(tags.encode("ascii")) + payload
