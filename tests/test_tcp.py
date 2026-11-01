import socket
import struct
import threading
import time

from hostcm.cli import main
from hostcm.protocol import LineConfig, checksum
from sim import SimMicro


def test_telnet_negotiation_refused(make_tcp_server, root):
    r = make_tcp_server()
    r.peer.sendall(b"\xff\xfd\x01\xff\xfb\x03\xff\xfa\x18\x01\xff\xf0")   # DO ECHO, WILL SGA, SB TTYPE
    assert r.micro.read_until(b"\xff\xfe\x03") == b"\xff\xfc\x01\xff\xfe\x03"
    (root / "f").write_text("hello\n")
    # a request with telnet commands spliced in, and a telnet CR NUL line ending
    r.micro.send_raw(b"or\xff\xf1t f" + checksum("ort f").encode() + b"\r\x00")
    assert r.micro.recv_frame() == ("b1", True)
    assert r.micro.request("g1") == "bzhello"


def test_telnet_escapes_ff_on_send(make_tcp_server):
    line = LineConfig(prompt=b"\xff")
    r = make_tcp_server(line=line)
    r.micro.send_raw(b"v80" + checksum("v80").encode() + b"\r")
    assert r.micro.read_until(b"\r\xff\xff") == b"\x13b" + checksum("b").encode() + b"\r\xff\xff"


def test_raw_tcp_does_not_escape(make_tcp_server):
    line = LineConfig(prompt=b"\xff")
    r = make_tcp_server(telnet=False, line=line)
    r.micro.send_raw(b"v80" + checksum("v80").encode() + b"\r")
    assert r.micro.read_until(b"\r\xff") == b"\x13b" + checksum("b").encode() + b"\r\xff"
    r.micro.expect_silence(0.3)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _connect(port):
    for _ in range(50):
        try:
            return socket.create_connection(("127.0.0.1", port))
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("server did not start")


def _listen_server(tmp_path, *extra):
    port = _free_port()
    root = tmp_path / "files"
    result = {}
    th = threading.Thread(target=lambda: result.setdefault("rc", main(
        ["--listen", "127.0.0.1:%d" % port, "--root", str(root)] + list(extra))), daemon=True)
    th.start()
    return _connect(port), port, root, th, result


def test_cli_listen_mode(tmp_path):
    sock, port, root, th, result = _listen_server(tmp_path, "--once", "--exit-on-quit")
    m = SimMicro.over_socket(sock)
    assert m.open("w", "t", "hello.bas") == "b1"
    m.write_record(1, '10 print "hello"')
    m.send("q")
    th.join(5)
    sock.close()
    assert result["rc"] == 0
    assert (root / "hello.bas").read_text() == '10 print "hello"\n'


def test_reset_and_drop_keep_listener_and_old_file(tmp_path):
    sock, port, root, th, _ = _listen_server(tmp_path)
    root.joinpath("prog").write_text("original\n")
    m = SimMicro.over_socket(sock)
    assert m.open("w", "t", "prog") == "b1"
    m.write_record(1, "half a save")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()                                  # RST mid-save
    m2 = SimMicro.over_socket(_connect(port))
    assert m2.request("v80") == "b"
    assert th.is_alive()
    assert root.joinpath("prog").read_text() == "original\n"
