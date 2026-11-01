"""HOSTCM wire format (Waterloo HOSTCM Specification, T. Wilkinson, 1982).

micro -> host:  message CS LE          (q and N carry no CS)
host -> micro:  RS message CS LE PR    (the spec's "ignored" parts are empty)
"""

from dataclasses import dataclass

CHECKSUM_CHARS = "ABCDEFGHIJKLMNOP"
NAK = "N"
QUIT = "q"


def checksum(message: str) -> str:
    return CHECKSUM_CHARS[sum(ord(c) for c in message) % 16]


def parse_hex_bytes(text: str) -> bytes:
    """Accept '11', '0x11', '11 0d' etc. Used for SETUP-style values."""
    parts = text.replace(",", " ").split()
    if len(parts) == 1 and len(parts[0]) > 2 and not parts[0].lower().startswith("0x"):
        p = parts[0]
        parts = [p[i:i + 2] for i in range(0, len(p), 2)]
    return bytes(int(p, 16) for p in parts)


@dataclass
class LineConfig:
    """The three SETUP characters. Waterloo defaults: PR=11 (XON), LE=0D, RS=13 (XOFF)."""
    prompt: bytes = b"\x11"
    lineend: bytes = b"\r"
    response: bytes = b"\x13"

    def __post_init__(self):
        if len(self.lineend) != 1 or len(self.response) != 1:
            raise ValueError("lineend and response must be single bytes")
        if not 1 <= len(self.prompt) <= 4:
            raise ValueError("prompt must be 1..4 bytes")

    def frame_reply(self, message: str, with_checksum: bool = True) -> bytes:
        body = message + (checksum(message) if with_checksum else "")
        return self.response + body.encode("latin-1") + self.lineend + self.prompt

    def frame_request(self, message: str) -> bytes:
        """What the micro sends; used by the simulator and tests."""
        with_cs = message not in (NAK, QUIT)
        body = message + (checksum(message) if with_cs else "")
        return body.encode("latin-1") + self.lineend

    def reserved(self) -> set:
        return set(self.prompt) | set(self.lineend) | set(self.response)


def split_checksum(line: str):
    """Return (message, ok). Bare N and q are accepted without a checksum."""
    if line in (NAK, QUIT):
        return line, True
    if len(line) < 2:
        return line, False
    msg, cs = line[:-1], line[-1]
    return msg, checksum(msg) == cs


def explode(data: bytes) -> str:
    return data.hex().upper()


def implode(text: str) -> bytes:
    if len(text) % 2:
        raise ValueError("odd hex length")
    return bytes.fromhex(text)
