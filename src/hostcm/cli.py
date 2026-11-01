import argparse
import logging
import signal
import sys
import time

from . import __version__
from .files import Sandbox
from .link import Link
from .protocol import LineConfig, parse_hex_bytes
from .server import Options, Session
from .transport import Closed, SerialTransport, SocketTransport, listen

log = logging.getLogger("hostcm")


def hostport(text):
    host, _, port = text.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    return host.strip("[]"), int(port)


def framing(text):
    t = text.upper()
    if len(t) != 3 or t[0] not in "5678" or t[1] not in "NEOMS" or t[2] not in "12":
        raise argparse.ArgumentTypeError("expected e.g. 8N1, 7E1")
    return int(t[0]), t[1], int(t[2])


def build_parser():
    p = argparse.ArgumentParser(prog="hostcm", description="Waterloo SuperPET HOSTCM file server")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--serial", metavar="DEVICE", help="serial device, e.g. /dev/ttyUSB0")
    src.add_argument("--tcp", metavar="HOST:PORT", type=hostport,
                     help="connect to a terminal server port (telnet handled)")
    src.add_argument("--listen", metavar="HOST:PORT", type=hostport, help="accept TCP connections")
    p.add_argument("--root", default="./superpet-files", help="host directory served as 'host.' (default %(default)s)")
    p.add_argument("--baud", type=int, default=2400, help="serial baud (default %(default)s, the Waterloo SETUP default)")
    p.add_argument("--framing", type=framing, default=(8, "N", 1), metavar="8N1",
                   help="serial data/parity/stop (default 8N1)")
    p.add_argument("--tx-parity", choices=["none", "even", "odd", "mark", "space"], default="none",
                   help="set bit 7 of transmitted bytes as a parity bit (7-bit+parity over an 8N1 link)")
    p.add_argument("--telnet", dest="telnet", action="store_true", default=None,
                   help="force telnet IAC handling (default on for --tcp, off for --listen)")
    p.add_argument("--raw", dest="telnet", action="store_false", help="disable telnet handling")
    p.add_argument("--prompt", default="11", help="SETUP Prompt, hex, up to 4 bytes (default 11)")
    p.add_argument("--lineend", default="0D", help="SETUP Lineend, hex (default 0D)")
    p.add_argument("--response", default="13", help="SETUP Response, hex (default 13)")
    p.add_argument("--buffer", type=int, default=80, help="micro buffer size assumed until a 'v' request (default 80)")
    p.add_argument("--reply-overhead", type=int, default=4,
                   help="buffer bytes reserved for reply code/checksum/lineend (default 4)")
    p.add_argument("--seek-base", type=int, default=0, help="record number of the first record (default 0)")
    p.add_argument("--binary-record", type=int, default=0,
                   help="bytes per record when reading binary files (default: fill the micro buffer)")
    p.add_argument("--dir-format", choices=["name", "long"], default="name",
                   help="'long' adds file sizes to directory entries (default name)")
    p.add_argument("--case", choices=["preserve", "lower", "upper"], default="preserve",
                   help="case of newly created host file names (lookups are case-insensitive)")
    p.add_argument("--exit-on-quit", action="store_true", help="exit when the micro sends 'q'")
    p.add_argument("--once", action="store_true", help="do not reconnect / accept again after a disconnect")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv frame trace in hex")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def make_session(a):
    line = LineConfig(prompt=parse_hex_bytes(a.prompt), lineend=parse_hex_bytes(a.lineend),
                      response=parse_hex_bytes(a.response))
    opts = Options(micro_buffer=a.buffer, reply_overhead=a.reply_overhead, seek_base=a.seek_base,
                   binary_record=a.binary_record, dir_format=a.dir_format, exit_on_quit=a.exit_on_quit)
    return Session(Sandbox(a.root, case=a.case), line, opts)


def serve(transport, a):
    session = make_session(a)
    link = Link(transport, session, tx_parity=a.tx_parity)
    try:
        link.run()
        session.close_all()
    except (Closed, OSError) as e:
        log.warning("connection closed%s", "" if isinstance(e, Closed) else ": %s" % e)
        session.abandon()
    except KeyboardInterrupt:
        session.abandon()
        raise
    finally:
        transport.close()


def _terminate(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    a = build_parser().parse_args(argv)
    level = logging.WARNING if a.verbose == 0 else logging.INFO if a.verbose == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(message)s", stream=sys.stderr)
    try:
        signal.signal(signal.SIGTERM, _terminate)
    except ValueError:              # not the main thread (tests)
        pass
    log.info("serving %s", Sandbox(a.root).root)
    try:
        if a.serial:
            bits, parity, stop = a.framing
            t = SerialTransport(a.serial, a.baud, bits, parity, stop)
            log.info("serial %s %d %d%s%d", a.serial, a.baud, bits, parity, stop)
            serve(t, a)
        elif a.tcp:
            telnet = True if a.telnet is None else a.telnet
            while True:
                try:
                    t = SocketTransport.connect(*a.tcp, telnet=telnet)
                except OSError as e:
                    log.error("connect %s:%d: %s", *a.tcp, e)
                    if a.once:
                        return 1
                    time.sleep(5)
                    continue
                log.info("connected to %s", t.name)
                serve(t, a)
                if a.once or a.exit_on_quit:
                    break
                time.sleep(2)
        else:
            telnet = False if a.telnet is None else a.telnet
            srv = listen(*a.listen)
            log.info("listening on %s:%d", *a.listen)
            if a.listen[0] not in ("127.0.0.1", "::1", "localhost"):
                log.warning("no authentication: anyone who can reach %s:%d can read, write and "
                            "delete files in %s", a.listen[0], a.listen[1], Sandbox(a.root).root)
            while True:
                sock, peer = srv.accept()
                log.info("connection from %s", peer[0])
                serve(SocketTransport(sock, telnet, str(peer)), a)
                if a.once or a.exit_on_quit:
                    break
    except KeyboardInterrupt:
        pass
    except OSError as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
