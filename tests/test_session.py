"""End-to-end tests: simulated SuperPET <-> pty <-> pyserial <-> server."""

import os

import pytest

from hostcm.protocol import LineConfig, checksum
from hostcm.server import Options


def write_text(root, name, lines):
    (root / name).write_text("".join(l + "\n" for l in lines))


# ---- spec Appendix A ----------------------------------------------------

def test_example1_read_records_with_partial(micro, root):
    long = "".join(chr(33 + i % 90) for i in range(130))
    write_text(root, "testfile script", ["rec1", "rec2", long])
    assert micro.request("v80") == "b"
    assert micro.request("ort testfile script") == "b1"
    assert micro.request("g1") == "bzrec1"
    assert micro.request("g1") == "bzrec2"
    r1 = micro.request("g1")
    assert r1.startswith("bn") and len(r1) + 1 + 1 <= 80   # code + data + CS + LE fits
    r2 = micro.request("g1")
    assert r2.startswith("bz") and r1[2:] + r2[2:] == long
    assert micro.request("g1") == "e"
    assert micro.request("c1") == "b"


def test_example2_rename(micro, root):
    write_text(root, "testfile script", ["x"])
    assert micro.request("wtestfile script") == "b"
    assert micro.request("boldfile script") == "b"
    assert (root / "oldfile script").read_text() == "x\n"
    assert not (root / "testfile script").exists()


def test_exact_reply_bytes(make_pty_server, root):
    r = make_pty_server()
    write_text(root, "f", ["hi"])
    r.micro.send("ort f")
    assert r.micro.read_until(b"\x11") == b"\x13b1" + checksum("b1").encode() + b"\r\x11"


# ---- reading and writing ------------------------------------------------

def test_write_then_read_text(micro, root):
    assert micro.open("w", "t", "notes") == "b1"
    micro.write_record(1, "first line")
    micro.write_record(1, "x" * 200)
    micro.write_record(1, "")
    assert micro.request("c1") == "b"
    assert (root / "notes").read_text() == "first line\n" + "x" * 200 + "\n\n"
    assert micro.open("r", "t", "notes") == "b1"
    assert micro.read_records(1) == ["first line", "x" * 200, ""]
    assert micro.request("c1") == "b"


def test_append(micro, root):
    write_text(root, "log", ["a"])
    assert micro.open("a", "t", "log") == "b1"
    micro.write_record(1, "b")
    assert micro.request("c1") == "b"
    assert (root / "log").read_text() == "a\nb\n"


def test_append_creates(micro, root):
    assert micro.open("A", "t", "new") == "b1"
    micro.write_record(1, "z")
    assert micro.request("a") == "b"
    assert (root / "new").read_text() == "z\n"


def test_update_with_seek(micro, root):
    write_text(root, "rel", ["r0", "r1", "r2"])
    assert micro.open("u", "t", "rel") == "b1"
    assert micro.request("r1 2") == "b"
    assert micro.request("g1") == "bzr2"
    assert micro.request("r1 1") == "b"
    micro.write_record(1, "R1")
    assert micro.request("r1 3") == "b"          # one past the end: append position
    micro.write_record(1, "r3")
    assert micro.request("r1 5") == "xInvalid record number"
    assert micro.request("r1 x") == "xInvalid record number"
    assert micro.request("c1") == "b"
    assert (root / "rel").read_text() == "r0\nR1\nr2\nr3\n"


def test_seek_base_one(make_pty_server, root):
    m = make_pty_server(opts=Options(seek_base=1)).micro
    write_text(root, "rel", ["one", "two"])
    assert m.open("r", "t", "rel") == "b1"
    assert m.request("r1 2") == "b"
    assert m.request("g1") == "bztwo"
    assert m.request("r1 0") == "xInvalid record number"


def test_resend_previous_record(micro, root):
    write_text(root, "f", ["one", "two"])
    assert micro.open("r", "t", "f") == "b1"
    assert micro.request("g1") == "bzone"
    assert micro.request("g11") == "bzone"
    assert micro.request("g1") == "bztwo"
    assert micro.request("g11") == "bztwo"
    assert micro.request("g1") == "e"


def test_two_files_interleaved(micro, root):
    write_text(root, "a", ["a1", "a2"])
    write_text(root, "b", ["b1"])
    assert micro.open("r", "t", "a") == "b1"
    assert micro.open("r", "t", "b") == "b2"
    assert micro.request("g2") == "bzb1"
    assert micro.request("g1") == "bza1"
    assert micro.request("g2") == "e"
    assert micro.request("g1") == "bza2"


def test_small_buffer_partials(micro, root):
    write_text(root, "f", ["abcdefghijklmnopqrstuvwxyz"])
    assert micro.open("r", "t", "f", bufsize=10) == "b1"
    parts = []
    while True:
        r = micro.request("g1")
        assert len(r) + 2 <= 10
        parts.append(r)
        if r[1] == "z":
            break
    assert "".join(p[2:] for p in parts) == "abcdefghijklmnopqrstuvwxyz"
    assert all(p[1] == "n" for p in parts[:-1])


def test_binary_store_and_load(micro, root):
    data = bytes(range(256)) * 3
    assert micro.open("s", "b", "prog.mod") == "b1"
    hexdata = data.hex().upper()
    for i in range(0, len(hexdata), 64):
        micro.write_record(1, hexdata[i:i + 64])
    assert micro.request("c1") == "b"
    assert (root / "prog.mod").read_bytes() == data
    assert micro.open("l", "b", "prog.mod") == "b1"
    recs = micro.read_records(1)
    assert all(len(r) % 2 == 0 and len(r) + 4 <= 80 for r in recs)
    assert bytes.fromhex("".join(recs)) == data


def test_binary_bad_hex(micro):
    assert micro.open("w", "b", "bad") == "b1"
    assert micro.request("p1zABC") == "xInvalid binary data"
    assert micro.request("p1zZZ") == "xInvalid binary data"


def test_text_sanitized(micro, root):
    (root / "ctl").write_bytes(b"a\x11b\x13c\td\xe9\r\n")
    assert micro.open("r", "t", "ctl") == "b1"
    assert micro.request("g1") == "bza?b?c\td?"


def test_bit7_masked_on_receive(make_pty_server, root):
    r = make_pty_server()
    write_text(root, "f", ["x"])
    frame = LineConfig().frame_request("ort f")
    r.micro.send_raw(bytes(b | 0x80 for b in frame))    # e.g. 7M1 seen as 8N1
    msg, ok = r.micro.recv_frame()
    assert ok and msg == "b1"


def test_tx_parity_even(make_pty_server, root):
    r = make_pty_server(tx_parity="even")
    write_text(root, "f", ["x"])
    r.micro.send("ort f")
    raw = r.micro.read_until(b"\x11")                   # XON already has even parity
    for b in raw:
        assert bin(b).count("1") % 2 == 0


# ---- errors ---------------------------------------------------------------

@pytest.mark.parametrize("req,reply", [
    ("ozt f", "xInvalid open mode"),
    ("orq f", "xInvalid open type"),
    ("ortf", "xInvalid file name"),
    ("ort ", "xInvalid file name"),
    ("o", "xInvalid open mode"),
    ("ort nosuch", "xFile not found"),
    ("g5", "xFile not open"),
    ("gx", "xInvalid file number"),
    ("p3zdata", "xFile not open"),
    ("c7", "xFile not open"),
    ("c", "xInvalid file number"),
    ("r1 0", "xFile not open"),
    ("v", "xInvalid buffer size"),
    ("v2", "xInvalid buffer size"),
    ("vabc", "xInvalid buffer size"),
    ("f", "xDirectory file not open"),
    ("k", "xDirectory file not open"),
    ("dzz*", "xNo files found"),
    ("y nosuch", "xFile not found"),
    ("Z", "xInvalid request"),
])
def test_error_replies(micro, req, reply):
    assert micro.request(req) == reply


def test_wrong_direction(micro, root):
    write_text(root, "f", ["x"])
    assert micro.open("r", "t", "f") == "b1"
    assert micro.request("p1zx") == "xFile not open for output, update or append"
    assert micro.open("w", "t", "g") == "b2"
    assert micro.request("g2") == "xFile not open for input or update"
    assert micro.request("r2 0") == "xFile not open for input or update"


def test_max_open_files(micro, root):
    for i in range(1, 10):
        assert micro.open("w", "t", "f%d" % i) == "b%d" % i
    assert micro.request("owt f10") == "xExceeded maximum number of open files"
    assert micro.request("c4") == "b"
    assert micro.request("owt f10") == "b4"
    assert micro.request("a") == "b"
    assert sorted(os.listdir(root)) == sorted(["f%d" % i for i in range(1, 11)])


def test_rename_expecting_name(micro, root):
    write_text(root, "f", ["x"])
    assert micro.request("wf") == "b"
    assert micro.request("g1") == "xExpecting file name"
    assert (root / "f").exists()


def test_rename_errors(micro, root):
    write_text(root, "a", ["x"])
    write_text(root, "b", ["y"])
    assert micro.request("wnosuch") == "xFile not found"
    assert micro.request("wa") == "b"
    assert micro.request("bb") == "xFile already exists"
    assert micro.request("wa") == "b"
    assert micro.request("b../evil") == "xInvalid file name"
    assert micro.request("wa") == "b"
    assert micro.request("bA") == "b"                    # case-only rename
    assert sorted(os.listdir(root)) == ["A", "b"]


def test_scratch(micro, root):
    write_text(root, "gone", ["x"])
    assert micro.request("ygone") == "b"
    assert not (root / "gone").exists()


def test_scratch_open_file_refused(micro, root):
    write_text(root, "f", ["x"])
    assert micro.open("r", "t", "f") == "b1"
    assert micro.request("yf") == "xFile is open"


# ---- names and sandbox -----------------------------------------------------

@pytest.mark.parametrize("name", ["../etc/passwd", "/etc/passwd", "a/../../x", ".hidden",
                                  "bad\\name", "wild*", "q?", "sub//x"])
def test_traversal_rejected(micro, name):
    assert micro.request("owt " + name) == "xInvalid file name"


def test_symlink_escape_rejected(micro, root, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("secret\n")
    os.symlink(outside, root / "link")
    assert micro.request("ort link") == "xInvalid file name"


def test_type_prefix_and_case(micro, root):
    write_text(root, "MyProg.bas", ["10 print"])
    assert micro.request("ort (t)myprog.BAS") == "b1"
    assert micro.request("g1") == "bz10 print"


def test_subdirectory_files(micro, root):
    (root / "pascal").mkdir()
    assert micro.request("owt pascal/hello") == "b1"
    micro.write_record(1, "program hello;")
    assert micro.request("c1") == "b"
    assert (root / "pascal" / "hello").read_text() == "program hello;\n"
    assert micro.request("owt nodir/x") == "xFile not found"


def test_case_lower_on_create(make_pty_server, root):
    from hostcm.files import Sandbox
    r = make_pty_server()
    r.session.fs = Sandbox(root, case="lower")
    assert r.micro.request("owt NEWFILE") == "b1"
    assert r.micro.request("c1") == "b"
    assert os.listdir(root) == ["newfile"]


# ---- directory ---------------------------------------------------------------

def dir_list(micro, spec=""):
    r = micro.request("d" + spec)
    if r != "b":
        return r
    out = []
    while True:
        r = micro.request("f")
        if r == "e":
            break
        assert r.startswith("b")
        out.append(r[1:])
    assert micro.request("k") == "b"
    return out


def test_directory(micro, root):
    for n in ["b.mac", "A.MAC", "c.txt", ".hidden"]:
        (root / n).write_text("x")
    (root / "sub").mkdir()
    (root / "sub" / "inner").write_text("y")
    assert dir_list(micro) == ["A.MAC", "b.mac", "c.txt", "sub/"]
    assert dir_list(micro, "*.mac") == ["A.MAC", "b.mac"]
    assert dir_list(micro, "sub") == ["inner"]
    assert dir_list(micro, "sub/*") == ["inner"]
    assert dir_list(micro, "c.txt") == ["c.txt"]
    assert dir_list(micro, "nomatch*") == "xNo files found"


def test_directory_already_open(micro, root):
    (root / "x").write_text("x")
    assert micro.request("d") == "b"
    assert micro.request("d") == "xDirectory file already open"
    assert micro.request("k") == "b"


def test_directory_long(make_pty_server, root):
    m = make_pty_server(opts=Options(dir_format="long")).micro
    (root / "file").write_text("12345")
    assert dir_list(m) == ["file" + " " * 20 + " 5"]


# ---- error recovery ----------------------------------------------------------

def test_bad_checksum_gets_nak_then_retry(make_pty_server, root):
    r = make_pty_server()
    write_text(root, "f", ["x"])
    r.micro.send_raw(b"ort f" + b"Z" + b"\r")
    assert r.micro.read_until(b"\x11") == b"\x13N\r\x11"
    r.micro.send("ort f")
    assert r.micro.recv_frame() == ("b1", True)


def test_nak_from_micro_resends_identical(make_pty_server, root):
    r = make_pty_server()
    write_text(root, "f", ["one", "two"])
    r.micro.send("ort f")
    r.micro.recv_frame()
    r.micro.send("g1")
    first = r.micro.read_until(b"\x11")
    r.micro.send("N")
    again = r.micro.read_until(b"\x11")
    assert first == again
    assert r.micro.request("g1") == "bztwo"             # NAK did not advance the file


def test_nak_with_nothing_to_resend_is_ignored(make_pty_server):
    r = make_pty_server()
    r.micro.send("N")
    r.micro.expect_silence()


def test_noise_before_message(make_pty_server, root):
    r = make_pty_server()
    write_text(root, "f", ["x"])
    r.micro.send_raw(b"\n\x00\x11")
    r.micro.send("ort f")
    assert r.micro.recv_frame() == ("b1", True)


def test_quit_flushes_and_is_silent(make_pty_server, root):
    r = make_pty_server()
    assert r.micro.open("w", "t", "q.txt") == "b1"
    r.micro.write_record(1, "saved")
    r.micro.send("q")
    r.micro.expect_silence()
    assert (root / "q.txt").read_text() == "saved\n"
    assert r.micro.request("g1") == "xFile not open"


def test_partial_put_flushed_on_close(micro, root):
    assert micro.open("w", "t", "p") == "b1"
    assert micro.request("p1nhalf") == "b"
    assert micro.request("c1") == "b"
    assert (root / "p").read_text() == "half\n"


def test_attribute_mount_and_hostcm_greeting(make_pty_server):
    r = make_pty_server()
    assert r.micro.request("v120") == "b"
    assert r.micro.request("?") == "b1024,120"
    assert r.micro.request("mdisk") == "b"
    assert r.micro.request("udisk") == "b"
    r.micro.send_raw(b"hostcm\r")
    assert b"host communications started" in r.micro.read_until(b"\x11")


def test_rsts_style_setup(make_pty_server, root):
    line = LineConfig(prompt=b"\n", lineend=b"\r", response=b"\n")
    r = make_pty_server(line=line)
    write_text(root, "f", ["abc"])
    r.micro.send("ort f")
    raw = r.micro.read_until(b"\r\n")
    assert raw == b"\nb1" + checksum("b1").encode() + b"\r\n"
    assert r.micro.request("g1") == "bzabc"


# ---- behaviour of the actual SuperPET ROM client (opsys hst* routines) -------

def test_rom_open_name_has_format_prefix(micro, root):
    write_text(root, "prog.bas", ["10 rem"])
    assert micro.request("v80") == "b"
    assert micro.request("ort (t)prog.bas") == "b1"
    assert micro.request("owb (f:128)data.bin") == "b2"
    assert micro.request("owt (v:80)var.txt") == "b3"
    assert micro.request("a") == "b"
    assert sorted(os.listdir(root)) == ["data.bin", "prog.bas", "var.txt"]


def test_rom_resend_uses_letter_l(micro, root):
    write_text(root, "a", ["a1", "a2"])
    write_text(root, "b", ["b1"])
    assert micro.request("ort (t)a") == "b1"
    assert micro.request("ort (t)b") == "b2"
    assert micro.request("g1") == "bza1"
    assert micro.request("g2") == "bzb1"
    assert micro.request("g1l") == "bza1"            # pre-empted read on file 1 resumes
    assert micro.request("g1") == "bza2"


def test_rom_seek_without_file_number(micro, root):
    write_text(root, "a", ["a0", "a1", "a2"])
    write_text(root, "b", ["b0", "b1"])
    assert micro.request("r2") == "xFile not open"
    assert micro.request("out (f:2)a") == "b1"
    assert micro.request("out (f:2)b") == "b2"
    assert micro.request("r1") == "b"                # applies to the last file referenced (2)
    assert micro.request("g2") == "bzb1"
    assert micro.request("g1") == "bza0"
    assert micro.request("r2") == "b"                # now file 1
    assert micro.request("g1") == "bza2"
    assert micro.request("rx") == "xInvalid record number"


def test_rom_buffered_writes(micro, root):
    assert micro.request("v80") == "b"
    assert micro.request("owt (t)long.txt") == "b1"
    line = "".join(chr(48 + i % 40) for i in range(200))
    micro.rom_write_line(1, line.encode())
    micro.rom_write_line(1, b"x" * 76)              # fills the buffer exactly, then bare 'p1z'
    micro.rom_write_line(1, b"")
    assert micro.request("c1") == "b"
    assert (root / "long.txt").read_text() == line + "\n" + "x" * 76 + "\n\n"


def test_rom_binary_roundtrip(micro, root):
    data = bytes((i * 7) & 0xFF for i in range(1000))
    assert micro.request("v80") == "b"
    assert micro.request("osb (f)prog") == "b1"
    micro.rom_write_line(1, data, binary=True)
    assert micro.request("c1") == "b"
    assert (root / "prog").read_bytes() == data
    assert micro.request("v80") == "b"
    assert micro.request("olb (f)prog") == "b1"
    got = b""
    while True:
        r = micro.request("g1")
        if r == "e":
            break
        assert len(r) + 1 <= 80                     # reply incl. checksum fits the ROM's buffer
        got += bytes.fromhex(r[2:])
    assert got == data


# ---- robustness -----------------------------------------------------------

def test_unfinished_binary_put_does_not_crash(micro, root):
    assert micro.open("w", "b", "x") == "b1"
    assert micro.request("p1nABC") == "b"
    assert micro.request("c1") == "b"
    assert micro.request("v80") == "b"           # still serving


def test_append_keeps_existing_bytes(micro, root):
    raw = "café ☃\r\nline2\x0cff\n".encode("utf-8")
    (root / "f").write_bytes(raw)
    assert micro.open("a", "t", "f") == "b1"
    micro.write_record(1, "added")
    assert micro.request("c1") == "b"
    assert (root / "f").read_bytes() == raw + b"added\n"


def test_update_keeps_other_lines_byte_exact(micro, root):
    raw = "café\r\nold\n".encode("utf-8")
    (root / "f").write_bytes(raw)
    assert micro.open("u", "t", "f") == "b1"
    assert micro.request("r1 1") == "b"
    micro.write_record(1, "new")
    assert micro.request("c1") == "b"
    assert (root / "f").read_bytes() == "café\r\nnew\n".encode("utf-8")


def test_scratch_symlink_removes_link_not_target(micro, root):
    (root / "real").write_text("keep\n")
    os.symlink("real", root / "alias")
    assert micro.request("yalias") == "b"
    assert not (root / "alias").exists() and (root / "real").read_text() == "keep\n"


def test_rename_open_file_refused(micro, root):
    write_text(root, "f", ["x"])
    assert micro.open("r", "t", "f") == "b1"
    assert micro.request("wf") == "xFile is open"


def test_oversized_put_refused(micro, root):
    assert micro.open("w", "t", "big") == "b1"
    for _ in range(1000):
        r = micro.request("p1n" + "x" * 70)
        if r != "b":
            break
    assert r == "xRecord too long"
