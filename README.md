# superpet-hostcm

A HOSTCM file server for the Commodore SuperPET (SP9000). It lets a Linux or macOS
machine act as the `host` device for the Waterloo microsystems software in 6809 mode, so
the Waterloo editor and languages can list, load and save files on it over the
SuperPET's RS-232 port.

Tested with the SuperPET software running on an [EconoPET](https://github.com/dlehenbauer/econopet),
connected through a Livingston PortMaster PM-25 terminal server at 2400 baud.

## Install

Requires Python 3.8 or later. pyserial is installed automatically.

```sh
pipx install git+https://github.com/cmolson/superpet-hostcm
```

`pip install --user git+https://github.com/cmolson/superpet-hostcm` works too.

To work on the code:

```sh
git clone https://github.com/cmolson/superpet-hostcm.git
cd superpet-hostcm
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

## Quick start

1. On the SuperPET, boot the Waterloo software in 6809 mode. The SETUP defaults
   (2400 baud, even parity, Prompt `11`, Lineend `0D`, Response `13`) work as they are.

2. Find the serial port the SuperPET is connected to:

   ```sh
   ls /dev/ttyUSB* /dev/ttyACM* /dev/cu.usbserial* 2>/dev/null
   ```

   On Linux you may need to be in the `dialout` group to open it:
   `sudo usermod -aG dialout $USER`, then log out and back in.

3. Start the server on that port:

   ```sh
   hostcm --serial /dev/ttyUSB0 -v
   ```

   The SuperPET sees the files in `superpet-files` under the directory you start
   `hostcm` from (it is created if it doesn't exist). Use `--root` to serve a different
   folder. `dir host` lists that folder: sub-folders end in `/`, and names starting
   with `.` are hidden. Files in a sub-folder are named `host.sub/name`.

   If the SuperPET is on a terminal server instead, give its address and port, for
   example `hostcm --tcp 10.0.0.5:6001 -v`.

4. On the SuperPET, name files with the `host.` prefix:

   ```
   dir host
   get host.hello.txt
   put host.hello.txt
   ```

Set the host side of the line to 8N1 with flow control off. The SuperPET sends 7 data
bits plus parity, which arrives as 8N1 with the parity in bit 7, and the server ignores
that bit. If something in between checks parity, use `--framing 7E1`. Don't use XON/XOFF
flow control, because the SuperPET's Prompt and Response characters are XON and XOFF.

`--listen` has no authentication. Anyone who can reach the port can read, write and
delete files in the served directory, so bind it to `127.0.0.1` or a trusted network.

## Options

The serial and SETUP-related defaults match the Waterloo SETUP defaults.

| option | default | meaning |
|---|---|---|
| `--serial DEV` / `--tcp HOST:PORT` / `--listen ADDR:PORT` | | serial device, connect to a terminal-server port, or accept TCP connections |
| `--root DIR` | `./superpet-files` | directory served as `host` |
| `--baud` | 2400 | serial rate |
| `--framing` | 8N1 | serial frame, e.g. `7E1` |
| `--tx-parity` | none | put even/odd/mark/space parity in bit 7 of transmitted bytes |
| `--prompt` / `--lineend` / `--response` | 11 / 0D / 13 | SETUP Prompt, Lineend and Response, in hex |
| `--telnet` / `--raw` | telnet for `--tcp`, raw for `--listen` | telnet IAC handling |
| `--buffer` | 80 | micro buffer size assumed until the micro reports one |
| `--reply-overhead` | 4 | buffer bytes kept free for the reply code, checksum and lineend |
| `--seek-base` | 0 | number of the first record in a seek |
| `--binary-record` | fill the buffer | bytes per record when sending a binary file |
| `--dir-format` | name | `long` adds the file size to directory entries |
| `--case` | preserve | case of newly created files; lookups are case-insensitive |
| `--exit-on-quit`, `--once` | | stop after a quit request, or after one connection |
| `-v`, `-vv` | | `-v` logs operations, `-vv` traces every frame |
| `--version` | | print the version |

File names are confined to `--root`. Text files are stored as ordinary text files with
`\n` line endings. A file is written when the SuperPET closes it; if the connection drops
first, the file on disk is left as it was. Protocol details are in [PROTOCOL.md](PROTOCOL.md).

## Sources

Written from these documents:

- *HOSTCM Specifications Document*, T. Wilkinson, University of Waterloo, February 2, 1982,
  and the *HOSTCM Tutorial*. Scanned by Mike Naberezny from Michael Cook's copies:
  <https://mikenaberezny.com/hardware/superpet/hostcm-protocol/>
- SuperPET system ROM source, `opsys-all-clean-20101210-src.txt`, rebuilt by John Toebes,
  for the `hst*` routines (the client side of the protocol):
  <https://mikenaberezny.com/hardware/superpet/disassemblies/>
- Mike Naberezny, *SuperPET serial port*:
  <https://mikenaberezny.com/hardware/superpet/serial-port/>
- Mike Naberezny, *Commodore SuperPET* (September 1, 2008):
  <https://mikenaberezny.com/2008/09/01/commodore-superpet>
- The SuperPET archive on funet:
  <https://bio.nic.funet.fi/pub/cbm/pet/SuperPET/index.html>

Rob Ferguson announced a HOSTCM server for the SuperPET on classiccmp in 2014
(<https://classiccmp.org/mailman3/hyperkitty/list/test-drb@ccmp.vtda.org/thread/2T3LYSZGZKXG5A7ENONT4HT2NJZDLDYA>).

## License

MIT. See [LICENSE](LICENSE).
