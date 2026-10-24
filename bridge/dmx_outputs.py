"""DMX output backends. Each backend exposes send(universe: bytes[512]) and close()."""

import socket
import struct
import sys
import time

from lm70s import CHANNEL_LABELS

KNOWN_VIDS = {0x0403: "FTDI", 0x16D0: "Enttec/DMXKing"}
USB_SERIAL_NAMES = ("usbserial", "usbmodem", "ttyUSB", "ttyACM")  # macOS cu.* / Linux tty*


def find_serial_port():
    from serial.tools import list_ports

    candidates = [
        p for p in list_ports.comports()
        if not p.device.startswith("/dev/tty.")  # macOS lists cu.* and tty.*; cu.* is the one to open
        and (p.vid or any(name in p.device for name in USB_SERIAL_NAMES))
    ]
    if not candidates:
        raise SystemExit("USB-DMXインターフェースが見つかりません "
                         "(macOS: /dev/cu.usbserial* / usbmodem*, Linux: /dev/ttyUSB* / ttyACM*)。--port で指定してください。")
    candidates.sort(key=lambda p: p.vid not in KNOWN_VIDS)
    port = candidates[0]
    print("serial port: %s (%s)" % (port.device, port.description), file=sys.stderr)
    return port.device


class EnttecProOutput:
    """ENTTEC DMX USB Pro protocol (also DMXKing ultraDMX Micro and other 'Pro compatible' boxes)."""

    def __init__(self, port):
        import serial

        self.ser = serial.Serial(port or find_serial_port(), 57600, timeout=1, write_timeout=1)

    def send(self, universe):
        payload = b"\x00" + universe
        n = len(payload)
        self.ser.write(bytes([0x7E, 6, n & 0xFF, n >> 8]) + payload + b"\xE7")

    def close(self):
        self.ser.close()


class OpenDmxOutput:
    """Microcontroller-less FTDI/RS485 cables (ENTTEC Open DMX USB and clones): we generate break + frame."""

    def __init__(self, port):
        import serial

        self.ser = serial.Serial(
            port or find_serial_port(), 250000,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_TWO,
            write_timeout=1,
        )

    def send(self, universe):
        self.ser.break_condition = True
        time.sleep(0.000176)
        self.ser.break_condition = False
        time.sleep(0.000012)
        self.ser.write(b"\x00" + universe)
        self.ser.flush()

    def close(self):
        self.ser.close()


class ArtNetOutput:
    def __init__(self, host, universe=0):
        self.addr = (host, 6454)
        self.universe = universe
        self.sequence = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def send(self, universe):
        self.sequence = self.sequence % 255 + 1
        header = b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14)
        header += bytes([self.sequence, 0]) + struct.pack("<H", self.universe) + struct.pack(">H", len(universe))
        self.sock.sendto(header + universe, self.addr)

    def close(self):
        self.sock.close()


class MonitorOutput:
    """Prints fixture channel values to the terminal whenever they change (no hardware needed)."""

    def __init__(self, fixtures, max_rate=10.0):
        self.fixtures = fixtures
        self.min_interval = 1.0 / max_rate
        self.last = None
        self.last_time = 0.0
        for f in fixtures:
            labels = " ".join("%5s" % l for l in CHANNEL_LABELS[f.mode])
            print("%-14s %s" % ("%s@%d" % (f.name, f.address), labels), file=sys.stderr)

    def send(self, universe):
        now = time.monotonic()
        if universe == self.last or now - self.last_time < self.min_interval:
            return
        self.last, self.last_time = universe, now
        rows = []
        for f in self.fixtures:
            values = " ".join("%5d" % universe[ch - 1] for ch in f.channels)
            rows.append("%-14s %s" % ("%s@%d" % (f.name, f.address), values))
        print("\n".join(rows), file=sys.stderr)

    def close(self):
        pass
