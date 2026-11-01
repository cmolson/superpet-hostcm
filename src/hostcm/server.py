"""HOSTCM request handling, independent of the transport."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .files import BadName, Sandbox
from .protocol import NAK, QUIT, LineConfig, explode, implode, split_checksum

log = logging.getLogger("hostcm")

READ_MODES = {"r", "l"}
WRITE_MODES = {"w", "s"}
MAX_FILES = 9
MAX_RECORD = 65536               # longest record or pending put, in characters


@dataclass
class Options:
    micro_buffer: int = 80
    host_buffer: int = 1024
    reply_overhead: int = 4
    seek_base: int = 0
    binary_record: int = 0          # 0 = as large as the micro buffer allows
    dir_format: str = "name"
    exit_on_quit: bool = False


@dataclass
class OpenFile:
    path: Path
    mode: str
    binary: bool
    records: List = field(default_factory=list)
    pos: int = 0
    pending: str = ""               # unsent tail of a partially sent record
    last_chunk: Optional[str] = None
    put_buf: str = ""
    dirty: bool = False
    base: bytes = b""              # existing contents an append-mode file adds to

    @property
    def readable(self):
        return self.mode in READ_MODES or self.mode == "u"

    @property
    def writable(self):
        return self.mode in WRITE_MODES or self.mode in ("u", "a")


class Session:
    def __init__(self, sandbox: Sandbox, line: LineConfig = None, opts: Options = None):
        self.fs = sandbox
        self.line = line or LineConfig()
        self.opts = opts or Options()
        self.micro_buffer = self.opts.micro_buffer
        self.files = {}
        self.dir_entries = None
        self.rename_from = None
        self.last_reply: Optional[bytes] = None
        self.current = None
        self.quit = False

    def handle_line(self, raw: bytes) -> Optional[bytes]:
        """One received line (lineend already removed) -> bytes to send, or None."""
        text = bytes(b & 0x7F for b in raw).decode("ascii")
        text = text.lstrip("\n\0" + "".join(chr(c) for c in self.line.reserved()))
        if not text:
            return None
        if text.strip().lower() == "hostcm":
            log.info("passthru 'hostcm' command seen")
            return b"\r\nhost communications started\r\n" + self.line.prompt
        msg, ok = split_checksum(text)
        if not ok:
            log.warning("bad checksum on %r, sending NAK", text)
            return self.line.frame_reply(NAK, with_checksum=False)
        if msg == NAK:
            if self.last_reply is None:
                log.warning("NAK with nothing to resend")
                return None
            log.info("NAK from micro, resending last reply")
            return self.last_reply
        if msg == QUIT:
            log.info("quit")
            self.close_all()
            self.dir_entries = None
            self.rename_from = None
            self.quit = True
            return None
        self.quit = False
        reply = self.dispatch(msg)
        self.last_reply = self.line.frame_reply(reply)
        return self.last_reply

    def dispatch(self, msg: str) -> str:
        if self.rename_from is not None:
            return self._rename_to(msg)
        handler = {
            "o": self._open, "g": self._get, "p": self._put, "r": self._seek,
            "c": self._close, "a": self._close_all, "?": self._attr, "v": self._size,
            "d": self._dir_open, "f": self._dir_get, "k": self._dir_close,
            "y": self._scratch, "w": self._rename, "m": self._mount, "u": self._mount,
        }.get(msg[0])
        if handler is None:
            return "xInvalid request"
        try:
            return handler(msg[1:])
        except BadName:
            return "xInvalid file name"
        except FileNotFoundError:
            return "xFile not found"
        except OSError as e:
            return "x" + (e.strerror or str(e))
        except ValueError:
            return "xInvalid request"

    def data_limit(self, binary=False):
        n = max(1, self.micro_buffer - self.opts.reply_overhead)
        return max(2, n - n % 2) if binary else n

    def _file(self, arg):
        if not arg or not arg[0].isdigit():
            return None, "xInvalid file number"
        f = self.files.get(int(arg[0]))
        if f is None:
            return None, "xFile not open"
        self.current = int(arg[0])
        return f, None

    def _sanitize(self, line: str) -> str:
        bad = self.line.reserved()
        return "".join("?" if (ord(c) in bad or ord(c) > 126 or (ord(c) < 32 and c != "\t")) else c
                       for c in line)

    def _load(self, of: OpenFile):
        data = of.path.read_bytes() if of.path.exists() else b""
        if of.binary:
            n = self.opts.binary_record or self.data_limit(True) // 2
            of.records = [data[i:i + n] for i in range(0, len(data), n)]
        else:
            lines = data.decode("latin-1").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            of.records = lines          # raw; cleaned for the wire in _get

    def _flush(self, of: OpenFile):
        if not of.dirty:
            return
        if of.put_buf:
            try:
                self._commit(of, of.put_buf)
            except ValueError:
                log.warning("dropping unfinished binary record for %s", of.path)
        if of.binary:
            data = b"".join(of.records)
        else:
            data = "".join(r + "\n" for r in of.records).encode("latin-1")
        base = of.base
        if base and not of.binary and not base.endswith(b"\n"):
            base += b"\n"
        self.fs.write_atomic(of.path, base + data)
        of.dirty = False

    def _commit(self, of: OpenFile, payload: str):
        rec = implode(payload) if of.binary else payload
        if of.pos < len(of.records):
            of.records[of.pos] = rec
        else:
            of.records.append(rec)
        of.pos += 1
        of.put_buf = ""
        of.dirty = True

    def close_all(self):
        for of in list(self.files.values()):
            self._flush(of)
        self.files.clear()

    def abandon(self):
        """Connection lost: files the micro never closed are not written."""
        for of in self.files.values():
            if of.dirty:
                log.warning("connection lost, %s not written", of.path)
        self.files.clear()
        self.dir_entries = None
        self.rename_from = None

    def _open(self, arg):
        if len(arg) < 3:
            return "xInvalid open mode"
        mode, ftype, blank, name = arg[0].lower(), arg[1], arg[2], arg[3:]
        if mode not in READ_MODES | WRITE_MODES | {"u", "a"}:
            return "xInvalid open mode"
        if ftype not in "bt":
            return "xInvalid open type"
        if blank != " " or not name.strip():
            return "xInvalid file name"
        slot = next((i for i in range(1, MAX_FILES + 1) if i not in self.files), None)
        if slot is None:
            return "xExceeded maximum number of open files"
        binary = ftype == "b" or mode in ("l", "s")
        creating = mode in WRITE_MODES or mode == "a"
        path = self.fs.resolve(name, create=creating)
        if path.is_dir():
            return "xInvalid file name"
        if mode in READ_MODES or mode == "u":
            if not path.exists():
                return "xFile not found"
        of = OpenFile(path=path, mode=mode, binary=binary)
        if mode in WRITE_MODES:
            of.dirty = True
        elif mode == "a":
            of.base = path.read_bytes() if path.exists() else b""
            of.dirty = True
        else:
            self._load(of)
        self.files[slot] = of
        self.current = slot
        log.info("open %d %s %s %s", slot, mode, "binary" if binary else "text", path)
        return "b%d" % slot

    def _get(self, arg):
        of, err = self._file(arg)
        if err:
            return err
        if not of.readable:
            return "xFile not open for input or update"
        # the spec says '1'; the SuperPET ROM (hstrqust) actually sends 'l'
        if arg[1:2] in ("1", "l") and of.last_chunk is not None:
            return of.last_chunk
        if not of.pending:
            if of.pos >= len(of.records):
                of.last_chunk = "e"
                return "e"
            rec = of.records[of.pos]
            of.pos += 1
            of.pending = explode(rec) if of.binary else self._sanitize(rec.rstrip("\r"))
            if not of.pending:
                of.last_chunk = "bz"
                return "bz"
        n = self.data_limit(of.binary)
        chunk, of.pending = of.pending[:n], of.pending[n:]
        of.last_chunk = ("bn" if of.pending else "bz") + chunk
        return of.last_chunk

    def _put(self, arg):
        of, err = self._file(arg)
        if err:
            return err
        if not of.writable:
            return "xFile not open for output, update or append"
        kind, data = arg[1:2], arg[2:]
        if kind not in ("n", "z"):
            return "xInvalid put mode"
        if len(of.put_buf) + len(data) > MAX_RECORD:
            of.put_buf = ""
            return "xRecord too long"
        of.put_buf += data
        if kind == "z":
            try:
                self._commit(of, of.put_buf)
            except ValueError:
                of.put_buf = ""
                return "xInvalid binary data"
        return "b"

    def _seek(self, arg):
        # spec: "r<fnum> <rnum>"; SuperPET ROM (hstseek): "r<rnum>" for the current file
        if len(arg) >= 3 and arg[0].isdigit() and arg[1] == " ":
            fid, rtext = arg[0], arg[2:]
        elif arg.strip().lstrip("-").isdigit():
            if self.current is None:
                return "xFile not open"
            fid, rtext = str(self.current), arg.strip()
        else:
            return "xInvalid record number"
        of, err = self._file(fid)
        if err:
            return err
        if not of.readable:
            return "xFile not open for input or update"
        try:
            rnum = int(rtext) - self.opts.seek_base
        except ValueError:
            return "xInvalid record number"
        if not 0 <= rnum <= len(of.records):
            return "xInvalid record number"
        of.pos, of.pending, of.last_chunk = rnum, "", None
        return "b"

    def _close(self, arg):
        if not arg or not arg[0].isdigit():
            return "xInvalid file number"
        of = self.files.get(int(arg[0]))
        if of is None:
            return "xFile not open"
        del self.files[int(arg[0])]
        self._flush(of)
        return "b"

    def _close_all(self, arg):
        self.close_all()
        return "b"

    def _attr(self, arg):
        return "b%d,%d" % (self.opts.host_buffer, self.micro_buffer)

    def _size(self, arg):
        try:
            n = int(arg.strip())
        except ValueError:
            return "xInvalid buffer size"
        if n <= self.opts.reply_overhead + 1 or n > 4096:
            return "xInvalid buffer size"
        self.micro_buffer = n
        return "b"

    def _dir_open(self, arg):
        if self.dir_entries is not None:
            return "xDirectory file already open"
        try:
            entries = self.fs.listdir(arg)
        except (FileNotFoundError, NotADirectoryError):
            entries = []
        if not entries:
            return "xNo files found"
        self.dir_entries = list(entries)
        return "b"

    def _dir_get(self, arg):
        if self.dir_entries is None:
            return "xDirectory file not open"
        if not self.dir_entries:
            return "e"
        name, size = self.dir_entries.pop(0)
        if self.opts.dir_format == "long":
            rec = "%-24s %s" % (name, "<dir>" if size is None else size)
        else:
            rec = name + ("/" if size is None else "")
        return "b" + self._sanitize(rec)[: self.data_limit() + 1]

    def _dir_close(self, arg):
        if self.dir_entries is None:
            return "xDirectory file not open"
        self.dir_entries = None
        return "b"

    def _is_open(self, path):
        real = self.fs.real(path)
        return any(of.path == real for of in self.files.values())

    def _scratch(self, arg):
        path = self.fs.resolve(arg, follow=False)
        if path.is_dir():
            return "xInvalid file name"
        if self._is_open(path):
            return "xFile is open"
        path.unlink()
        log.info("scratch %s", path)
        return "b"

    def _rename(self, arg):
        path = self.fs.resolve(arg, follow=False)
        if path.is_dir():
            return "xInvalid file name"
        if self._is_open(path):
            return "xFile is open"
        self.rename_from = path
        return "b"

    def _rename_to(self, msg):
        src, self.rename_from = self.rename_from, None
        if not msg.startswith("b"):
            return "xExpecting file name"
        try:
            dst = self.fs.resolve(msg[1:], create=True)
        except (BadName, FileNotFoundError):
            return "xInvalid file name"
        if dst == src:
            dst = src.parent / self.fs.fold(self.fs.clean(msg[1:]).split("/")[-1].strip())
        elif dst.exists():
            return "xFile already exists"
        src.rename(dst)
        log.info("rename %s -> %s", src, dst)
        return "b"

    def _mount(self, arg):
        return "b"
