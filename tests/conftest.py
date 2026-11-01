import os
import socket
import threading
import tty

import pytest

from hostcm.files import Sandbox
from hostcm.link import Link
from hostcm.protocol import LineConfig
from hostcm.server import Options, Session
from hostcm.transport import Closed, SerialTransport, SocketTransport
from sim import SimMicro


class Running:
    def __init__(self, link, thread, micro, session):
        self.link, self.thread, self.micro, self.session = link, thread, micro, session

    def stop(self):
        self.link.stop.set()
        self.thread.join(3)


def _start(transport, session, tx_parity="none"):
    link = Link(transport, session, tx_parity=tx_parity)

    def run():
        try:
            link.run()
            session.close_all()
        except (Closed, OSError):
            session.abandon()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return link, th


@pytest.fixture
def root(tmp_path):
    d = tmp_path / "root"
    d.mkdir()
    return d


@pytest.fixture
def make_pty_server(root):
    """Server on the pty slave via pyserial; simulated micro on the master fd."""
    running = []

    def make(line=None, opts=None, tx_parity="none", **simkw):
        master, slave = os.openpty()
        tty.setraw(master)
        path = os.ttyname(slave)
        transport = SerialTransport(path, 2400)
        session = Session(Sandbox(root), line or LineConfig(), opts or Options())
        link, th = _start(transport, session, tx_parity)
        micro = SimMicro.over_fd(master, line=line or LineConfig(), **simkw)
        r = Running(link, th, micro, session)
        r.fds = (master, slave)
        r.transport = transport
        running.append(r)
        return r

    yield make
    for r in running:
        r.stop()
        r.transport.close()
        for fd in r.fds:
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def micro(make_pty_server):
    return make_pty_server().micro


@pytest.fixture
def make_tcp_server(root):
    """Server as a TCP client of a fake terminal server on localhost."""
    running = []

    def make(telnet=True, line=None):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        transport = SocketTransport.connect("127.0.0.1", port, telnet=telnet)
        peer, _ = srv.accept()
        srv.close()
        session = Session(Sandbox(root), line or LineConfig(), Options())
        link, th = _start(transport, session)
        r = Running(link, th, SimMicro.over_socket(peer, line=line or LineConfig()), session)
        r.peer = peer
        r.transport = transport
        running.append(r)
        return r

    yield make
    for r in running:
        r.stop()
        r.peer.close()
        r.transport.close()
