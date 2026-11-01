"""A simulated SuperPET speaking HOSTCM byte-for-byte, over a raw fd or socket."""

import os
import select
import time

from hostcm.protocol import LineConfig, checksum


class ProtocolError(Exception):
    pass


class SimMicro:
    def __init__(self, write, read, line=None, verify=True):
        self._write = write
        self._read = read
        self.line = line or LineConfig()
        self.rx = bytearray()
        self.verify = verify

    @classmethod
    def over_fd(cls, fd, **kw):
        def rd(timeout):
            r, _, _ = select.select([fd], [], [], timeout)
            return os.read(fd, 4096) if r else b""
        return cls(lambda b: os.write(fd, b), rd, **kw)

    @classmethod
    def over_socket(cls, sock, **kw):
        def rd(timeout):
            r, _, _ = select.select([sock], [], [], timeout)
            return sock.recv(4096) if r else b""
        return cls(sock.sendall, rd, **kw)

    def send_raw(self, data: bytes):
        self._write(data)

    def send(self, message: str):
        self._write(self.line.frame_request(message))

    def read_until(self, token: bytes, timeout=3.0):
        deadline = time.monotonic() + timeout
        while token not in self.rx:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("waiting for %r, have %r" % (token, bytes(self.rx)))
            self.rx += self._read(left)
        i = self.rx.index(token) + len(token)
        out = bytes(self.rx[:i])
        del self.rx[:i]
        return out

    def recv_frame(self, timeout=3.0):
        """Return (message, checksum_ok): skip to RS, read to LE, then consume PR."""
        self.read_until(self.line.response, timeout)
        body = self.read_until(self.line.lineend, timeout)[:-1]
        self.read_until(self.line.prompt, timeout)
        text = body.decode("latin-1")
        if text == "N":
            return "N", True
        msg, cs = text[:-1], text[-1:]
        return msg, checksum(msg) == cs

    def request(self, message: str, timeout=3.0) -> str:
        """Send, and NAK until a reply with a good checksum arrives."""
        self.send(message)
        for _ in range(5):
            msg, ok = self.recv_frame(timeout)
            if msg == "N":
                self.send(message)
                continue
            if ok:
                return msg
            self.send("N")
        raise ProtocolError("no clean reply to %r" % message)

    def expect_silence(self, timeout=0.5):
        data = self._read(timeout)
        if data:
            raise ProtocolError("unexpected %r" % data)

    # ---- helpers mirroring what Waterloo software does ----------------

    def open(self, mode, ftype, name, bufsize=80):
        assert self.request("v%d" % bufsize) == "b"
        return self.request("o%s%s %s" % (mode, ftype, name))

    def read_records(self, fnum):
        records, cur = [], ""
        while True:
            r = self.request("g%d" % fnum)
            if r == "e":
                return records
            assert r[0] == "b" and r[1] in "nz", r
            cur += r[2:]
            if r[1] == "z":
                records.append(cur)
                cur = ""

    def write_record(self, fnum, data, chunk=70):
        parts = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [""]
        for i, part in enumerate(parts):
            kind = "z" if i == len(parts) - 1 else "n"
            assert self.request("p%d%s%s" % (fnum, kind, part)) == "b"

    def rom_write_line(self, fnum, data: bytes, binary=False, bufsize=79):
        """Mimic the ROM's hstwrbuf/hstendln: fill 'p<id>n...' to bufsize, send, finish with z."""
        payload = data.hex().upper() if binary else data.decode("latin-1")
        head = "p%dn" % fnum
        buf = head
        for c in payload:
            buf += c
            if len(buf) >= bufsize:
                assert self.request(buf) == "b"
                buf = head
        if buf == head:
            assert self.request("p%dz" % fnum) == "b"
        else:
            assert self.request("p%dz" % fnum + buf[len(head):]) == "b"
