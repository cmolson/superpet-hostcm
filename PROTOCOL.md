# HOSTCM protocol notes

How `superpet-hostcm` implements the protocol. See the README for sources.

Requests from the micro have the form `message CS LE`. `q` and `N` carry no checksum.
Replies from the host have the form `RS message CS LE PR`. The checksum is
`"ABCDEFGHIJKLMNOP"[sum(ASCII codes) % 16]`. A bad checksum gets a bare `N`, in either
direction, and the other side retransmits.

The ROM sends the lineend and then waits for RS. It reads the reply up to LE, then
discards characters until it has seen PR. That is why RS is sent first and PR last.

| request | meaning | replies |
|---|---|---|
| `v80` | micro buffer size (sent before every open) | `b`, `xInvalid buffer size` |
| `o<mode><t\|b> <name>` | open. Modes: r w u a l(oad) s(tore). The ROM sends `<name>` as `(fmt[:lrecl])name`. | `b<fid>` (1 to 9), `x...` |
| `g<fid>` / `g<fid>l` | next record / resend the last chunk (the spec says `1`, the ROM sends `l`; both are accepted) | `bz<data>` complete, `bn<data>` partial, `e` EOF |
| `p<fid>n<data>` / `p<fid>z<data>` | write a partial record / write the final part of a record | `b` |
| `r<fid> <rec>` (spec) / `r<rec>` (ROM) | seek. The ROM form applies to the most recently referenced file. | `b`, `xInvalid record number` |
| `c<fid>`, `a` | close one file / close all files | `b` |
| `d<spec>`, `f`, `k` | directory open, read, close | `b`, `b<record>`, `e`, `xNo files found` |
| `y<name>` | scratch | `b` |
| `w<old>`, then `b<new>` | rename | `b`, `xExpecting file name` |
| `?`, `m<dev>`, `u<dev>` | attributes, mount, dismount (not used by the SuperPET) | `b<h>,<m>`, `b` |
| `q` | quit. All files are flushed and closed. | no reply |
| `N` | resend the last reply | the last reply, byte for byte |

Data handling:

- Text files are stored as host text with `\n` line endings, one line per record. When a
  file is read, CR is stripped. Any character that would collide with PR, LE or RS, any
  other control character, and any byte above 0x7E is sent as `?`. Tab passes through.
- Binary files (type `b`, and modes `l`/`s`) travel hex-exploded, two characters per
  byte. They are stored as raw bytes. Record boundaries written by the micro are not
  kept. On read, the file is cut into records that fill the micro buffer (`--binary-record`
  overrides the size). Each chunk is an even number of hex digits, because the ROM cannot
  rejoin a hex pair split across replies.
- Records longer than the micro buffer are split into `bn` replies followed by a final `bz`.
  A reply, counting its code, data and checksum, never exceeds `v` minus 1.
- No PETSCII translation is done. In 6809 mode the Waterloo software uses ASCII, and so
  does the protocol.

## File name mapping

The sandbox is `--root`. Each name the micro sends is mapped like this:

1. The leading `(fmt[:lrecl])` prefix that the ROM adds is removed, along with any
   `(type)` prefix written by the user, and surrounding blanks are trimmed.
2. `/` separates sub-directories. A path component may not be empty, `.`, `..`, or start
   with `.`. Control characters, `\ * ? " < > |` are rejected with `xInvalid file name`.
   Spaces are allowed, for CMS-style names such as `testfile script`.
3. The resolved real path, after following symlinks, must lie inside the root. Anything
   that escapes is rejected.
4. Existing files and directories are matched case-insensitively. New files keep the
   case the micro sent, unless `--case lower|upper` is given.
5. In `d<spec>`, `<spec>` is empty (list everything), a sub-directory, a pattern using
   `*` and `?`, or `sub/pattern`. Dotfiles are hidden. In name format, directory records
   end in `/`.

Files are written atomically on `c`, `a` or `q`. If the connection drops or the server stops
first, files that were open for writing are left unchanged. Append mode adds to the existing
bytes, and update mode rewrites only the records the micro changed; other lines are kept
byte for byte.

