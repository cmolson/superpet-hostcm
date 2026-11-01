"""Sandboxed mapping of SuperPET file names onto a host directory."""

import fnmatch
import os
import re
import tempfile
from pathlib import Path

_TYPE_PREFIX = re.compile(r"^\s*(\([^)]*\)\s*)+")
_BAD_CHARS = set('\\*?"<>|') | {chr(c) for c in range(32)} | {"\x7f"}


def _umask():
    m = os.umask(0)
    os.umask(m)
    return m


class BadName(Exception):
    pass


class Sandbox:
    def __init__(self, root, case="preserve"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if case not in ("preserve", "lower", "upper"):
            raise ValueError(case)
        self.case = case

    def fold(self, s):
        return s.lower() if self.case == "lower" else s.upper() if self.case == "upper" else s

    @staticmethod
    def clean(name: str) -> str:
        """Strip the '(type)' prefix the Waterloo software may add."""
        return _TYPE_PREFIX.sub("", name).strip()

    def _components(self, name: str, allow_wild=False):
        name = self.clean(name)
        if not name:
            raise BadName("empty")
        parts = name.split("/")
        for p in parts:
            p = p.strip()
            if not p or p in (".", "..") or p.startswith("."):
                raise BadName(name)
            bad = _BAD_CHARS - ({"*", "?"} if allow_wild else set())
            if any(c in bad for c in p):
                raise BadName(name)
        return [p.strip() for p in parts]

    def _inside(self, path: Path) -> Path:
        real = Path(os.path.realpath(path))
        if real != self.root and self.root not in real.parents:
            raise BadName(str(path))
        return real

    def _match_ci(self, directory: Path, part: str):
        exact = directory / part
        if exact.exists():
            return exact
        if directory.is_dir():
            low = part.lower()
            for entry in sorted(os.listdir(directory)):
                if entry.lower() == low and not entry.startswith("."):
                    return directory / entry
        return None

    def real(self, path: Path) -> Path:
        return Path(os.path.realpath(path))

    def resolve(self, name: str, create=False, follow=True) -> Path:
        """Existing files are found case-insensitively; new ones get --case folding.
        With follow=False a final symlink is returned itself (for scratch and rename)."""
        parts = self._components(name)
        cur = self.root
        for i, part in enumerate(parts):
            found = self._match_ci(cur, part)
            last = i == len(parts) - 1
            if found is None:
                if not (create and last):
                    raise FileNotFoundError(name)
                found = cur / self.fold(part)
            elif not last and not found.is_dir():
                raise FileNotFoundError(name)
            cur = found
        real = self._inside(cur)
        if follow or not cur.is_symlink():
            return real
        return self._inside(cur.parent) / cur.name

    def listdir(self, spec: str):
        """Names matching a directory request: '', a sub-directory, a pattern, or 'sub/pattern'."""
        spec = self.clean(spec)
        base, pattern = self.root, "*"
        if spec:
            parts = self._components(spec, allow_wild=True)
            if not any(c in "*?" for c in parts[-1]):
                p = None
                try:
                    p = self.resolve(spec)
                except FileNotFoundError:
                    pass
                if p is not None and p.is_dir():
                    base = p
                    parts = []
            if parts:
                pattern = parts[-1]
                base = self.resolve("/".join(parts[:-1])) if len(parts) > 1 else self.root
        out = []
        for entry in sorted(os.listdir(base), key=str.lower):
            if entry.startswith("."):
                continue
            path = base / entry
            if fnmatch.fnmatchcase(entry.lower(), pattern.lower()) and path.exists():
                try:
                    self._inside(path)
                except BadName:
                    continue
                size = path.stat().st_size if path.is_file() else None
                out.append((entry, size))
        return out

    def write_atomic(self, path: Path, data: bytes):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hostcm-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o666 & ~_umask())
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
