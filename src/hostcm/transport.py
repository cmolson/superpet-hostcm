"""Byte transports: serial port, TCP client (optionally telnet), TCP listener."""

import logging
import select
import socket

log = logging.getLogger("hostcm.transport")

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240


class Closed(Exception):
    pass


class TelnetCodec:
    """Strips telnet commands from the inbound stream and refuses every option.

    DO x -> WONT x, WILL x -> DONT x; DONT/WONT are not answered (already off),
    which keeps negotiation loop-free. IAC IAC is a literal 0xFF.
    """

    def __init__(self):
        self.state = "data"
        self.opt_cmd = None

    def decode(self, data: bytes):
        out, replies = bytearray(), bytearray()
        for b in data:
            st = self.state
            if st == "data":
                if b == IAC:
                    self.state = "iac"
                else:
                    out.append(b)
            elif st == "iac":
                if b == IAC:
                    out.append(IAC)
                    self.state = "data"
                elif b in (DO, DONT, WILL, WONT):
                    self.opt_cmd = b
                    self.state = "opt"
                elif b == SB:
                    self.state = "sb"
                else:
                    self.state = "data"
            elif st == "opt":
                if self.opt_cmd == DO:
                    replies += bytes((IAC, WONT, b))
                elif self.opt_cmd == WILL:
                    replies += bytes((IAC, DONT, b))
                log.debug("telnet %s %d", {DO: "DO", DONT: "DONT", WILL: "WILL", WONT: "WONT"}[self.opt_cmd], b)
                self.state = "data"
            elif st == "sb":
                if b == IAC:
                    self.state = "sb_iac"
            elif st == "sb_iac":
                self.state = "data" if b == SE else "sb"
        return bytes(out), bytes(replies)

    @staticmethod
    def encode(data: bytes) -> bytes:
        return data.replace(b"\xff", b"\xff\xff")


class SerialTransport:
    def __init__(self, device, baud=2400, bytesize=8, parity="N", stopbits=1):
        import serial
        self.port = serial.Serial(
            device, baudrate=baud, bytesize=bytesize, parity=parity, stopbits=stopbits,
            xonxoff=False, rtscts=False, dsrdtr=False, timeout=0.2)
        self.name = device

    def read(self, timeout):
        if self.port.timeout != timeout:
            self.port.timeout = timeout
        return self.port.read(max(1, self.port.in_waiting))

    def write(self, data):
        self.port.write(data)
        self.port.flush()

    def close(self):
        self.port.close()


class SocketTransport:
    def __init__(self, sock: socket.socket, telnet: bool, name=""):
        self.sock = sock
        self.telnet = TelnetCodec() if telnet else None
        self.name = name
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    @classmethod
    def connect(cls, host, port, telnet=True, timeout=10):
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(None)
        return cls(s, telnet, "%s:%d" % (host, port))

    def read(self, timeout):
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return b""
        data = self.sock.recv(4096)
        if not data:
            raise Closed()
        if self.telnet:
            data, replies = self.telnet.decode(data)
            if replies:
                self.sock.sendall(replies)
        return data

    def write(self, data):
        if self.telnet:
            data = self.telnet.encode(data)
        self.sock.sendall(data)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def listen(host, port):
    srv = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    return srv
