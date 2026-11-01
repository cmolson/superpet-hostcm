"""Glue between a transport and a Session: line splitting, parity, tracing."""

import logging
import threading

from .server import Session

trace = logging.getLogger("hostcm.trace")
MAX_LINE = 8192

_NAMES = {0x00: "NUL", 0x0A: "LF", 0x0D: "CR", 0x11: "XON", 0x13: "XOFF", 0x1B: "ESC",
          0x7F: "DEL", 0xFF: "FF"}


def describe(data: bytes) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "<%s>" % _NAMES.get(b, "%02X" % b) for b in data)
    return "%s  |%s|" % (data.hex(" ").upper(), text)


def add_parity(data: bytes, mode: str) -> bytes:
    if mode == "none":
        return data
    out = bytearray()
    for b in data:
        b &= 0x7F
        ones = bin(b).count("1")
        bit = {"even": ones & 1, "odd": (ones & 1) ^ 1, "mark": 1, "space": 0}[mode]
        out.append(b | (bit << 7))
    return bytes(out)


class Link:
    def __init__(self, transport, session: Session, tx_parity="none", idle_flush=2.0):
        self.t = transport
        self.s = session
        self.tx_parity = tx_parity
        self.idle_flush = idle_flush
        self.buf = bytearray()
        self.stop = threading.Event()

    def run(self):
        lineend = self.s.line.lineend[0] & 0x7F
        idle = 0.0
        while not self.stop.is_set():
            data = self.t.read(0.2)
            if not data:
                idle += 0.2
                if self.buf and idle >= self.idle_flush:
                    trace.debug("RX discard (no lineend)  %s", describe(bytes(self.buf)))
                    self.buf.clear()
                continue
            idle = 0.0
            for b in data:
                if (b & 0x7F) == lineend:
                    line = bytes(self.buf)
                    self.buf.clear()
                    self._line(line)
                    if self.s.quit and self.s.opts.exit_on_quit:
                        return
                elif len(self.buf) < MAX_LINE:
                    self.buf.append(b)

    def _line(self, line: bytes):
        trace.debug("RX %s", describe(line + self.s.line.lineend))
        reply = self.s.handle_line(line)
        if reply:
            trace.debug("TX %s", describe(reply))
            self.t.write(add_parity(reply, self.tx_parity))
