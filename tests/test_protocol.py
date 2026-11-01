import pytest

from hostcm.cli import build_parser
from hostcm.link import add_parity, describe
from hostcm.protocol import LineConfig, checksum, explode, implode, parse_hex_bytes, split_checksum
from hostcm.transport import TelnetCodec


def test_checksum_formula():
    assert checksum("b") == "C"
    assert checksum("") == "A"
    assert checksum("ort testfile script") == "K"


def test_frames_default_setup():
    line = LineConfig()
    assert line.frame_reply("b1") == b"\x13b1" + checksum("b1").encode() + b"\r\x11"
    assert line.frame_reply("N", with_checksum=False) == b"\x13N\r\x11"
    assert line.frame_request("g1") == b"g1" + checksum("g1").encode() + b"\r"
    assert line.frame_request("q") == b"q\r"
    assert line.frame_request("N") == b"N\r"


def test_split_checksum():
    assert split_checksum("g1" + checksum("g1")) == ("g1", True)
    assert split_checksum("g1X") == ("g1", False)
    assert split_checksum("q") == ("q", True)
    assert split_checksum("N") == ("N", True)
    assert split_checksum("") == ("", False)


def test_explode():
    assert explode(bytes([0, 0x0D, 0xFF])) == "000DFF"
    assert implode("000dff") == bytes([0, 0x0D, 0xFF])
    with pytest.raises(ValueError):
        implode("ABC")


def test_parse_hex():
    assert parse_hex_bytes("11") == b"\x11"
    assert parse_hex_bytes("0x0d") == b"\r"
    assert parse_hex_bytes("0D0A") == b"\r\n"
    assert parse_hex_bytes("0d 0a") == b"\r\n"


def test_lineconfig_validation():
    with pytest.raises(ValueError):
        LineConfig(prompt=b"12345")
    with pytest.raises(ValueError):
        LineConfig(lineend=b"\r\n")


def test_telnet_codec():
    c = TelnetCodec()
    data, replies = c.decode(b"ab\xff\xfd\x01c\xff\xfb\x03\xff\xff\xff\xfe\x05d\xff\xfa\x18\x00xx\xff\xf0e\xff\xf1")
    assert data == b"abc\xffde"
    assert replies == b"\xff\xfc\x01\xff\xfe\x03"   # WONT ECHO, DONT SGA; DONT 5 unanswered
    # split across reads
    c = TelnetCodec()
    d1, r1 = c.decode(b"x\xff")
    d2, r2 = c.decode(b"\xfd")
    d3, r3 = c.decode(b"\x18y")
    assert d1 + d2 + d3 == b"xy" and r3 == b"\xff\xfc\x18"
    assert TelnetCodec.encode(b"a\xffb") == b"a\xff\xffb"


def test_parity():
    assert add_parity(b"\x01\x03", "even") == b"\x81\x03"
    assert add_parity(b"\x01\x03", "odd") == b"\x01\x83"
    assert add_parity(b"\x01", "mark") == b"\x81"
    assert add_parity(b"\x81", "space") == b"\x01"
    assert add_parity(b"\x81", "none") == b"\x81"


def test_describe():
    assert describe(b"\x13b1\r\x11") == "13 62 31 0D 11  |<XOFF>b1<CR><XON>|"


def test_cli_parse(tmp_path):
    a = build_parser().parse_args(["--tcp", "ts.example:6001", "--root", str(tmp_path), "--framing", "7e1"])
    assert a.tcp == ("ts.example", 6001) and a.framing == (7, "E", 1) and a.telnet is None
    a = build_parser().parse_args(["--serial", "/dev/ttyUSB0", "--baud", "9600", "-vv", "--raw"])
    assert a.baud == 9600 and a.verbose == 2 and a.telnet is False
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--tcp", "nohostport"])
