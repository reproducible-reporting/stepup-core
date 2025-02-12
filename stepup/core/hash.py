# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
#
# This file is part of StepUp Core.
#
# StepUp Core is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# StepUp Core is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
# --
"""File and step hashing."""

import hashlib
import os
import stat
from typing import Self

import attrs
from path import Path

__all__ = (
    "FileHash",
    "HashWords",
    "InpInfo",
    "OutInfo",
    "StepHash",
    "compare_step_hashes",
    "compute_file_digest",
    "fmt_digest",
    "fmt_env_value",
    "fmt_file_hash_diff",
    "report_file_hash_diff",
)


@attrs.define
class HashWords:
    _hash = attrs.field(init=False, factory=hashlib.blake2b)

    def update(self, word: str | bytes | None):
        if isinstance(word, bytes):
            self._hash.update(b"\0\0")
            self._hash.update(word)
        elif isinstance(word, str):
            self._hash.update(b"\0\1")
            self._hash.update(word.encode())
        elif word is None:
            self._hash.update(b"\0\2")
        else:
            raise TypeError(f"Not supported by HashWords: {type(word)}")

    def digest(self):
        return self._hash.digest()


def compute_file_digest(path: str, follow_symlinks=True) -> bytes:
    """Compute the blake2b digest of a file or a symbolic link.

    Parameters
    ----------
    path
        The file of which the hash must be computed.
    follow_symlinks
        If True (default) and the path is a symbolic link,
        try to hash the contents of the destination file.
        If False, the destination path itself is hashed.

    Returns
    -------
    digest
        A 64 bytes blake2b hash.
    """
    path = Path(path)
    if path.islink() and not follow_symlinks:
        return hashlib.blake2b(path.readlink().encode("utf-8")).digest()
    if path.is_dir():
        raise OSError("File digests of directories are not supported.")
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, hashlib.blake2b).digest()


def report_file_hash_diff(
    label: str, path: str, old_hash: "FileHash", new_hash: "FileHash"
) -> tuple[bool, tuple[str, str]]:
    change = fmt_file_hash_diff(old_hash, new_hash)
    if change is None:
        return False, (f"Same {label} hash", path)
    return True, (f"Modified {label} hash", f"{path} {change}")


def fmt_file_hash_diff(old_hash: "FileHash", new_hash: "FileHash") -> str | None:
    changes = []
    if old_hash.digest != new_hash.digest:
        changes.append(f"digest {fmt_digest(old_hash.digest)} ➜ {fmt_digest(new_hash.digest)}")
    if old_hash.size != new_hash.size:
        changes.append(f"size {old_hash.size} ➜ {new_hash.size}")
    if old_hash.mode != new_hash.mode:
        changes.append(f"mode {stat.filemode(old_hash.mode)} ➜ {stat.filemode(new_hash.mode)}")
    return f"({', '.join(changes)})" if len(changes) > 0 else None


def fmt_digest(digest: bytes | None) -> str:
    if digest is None:
        return "(unset)"
    if len(digest) == 1:
        if digest == b"u":
            return "UNKNOWN"
        if digest == b"d":
            return "DIRECTORY"
        return "?"
    return digest.hex()[:8]


def fmt_env_value(value: str | None) -> str:
    return "(unset)" if value is None else f"='{value}'"


@attrs.define(frozen=True)
class FileHash:
    """A hash of a file's content and file properties.

    For existing (regular) files, the digest attribute is a blake2b hash of the file content,
    and the mode of the file is also stored as "part of the hash".
    When either contents, size or mode changes, the file is considered changed.

    For directories, the digest is set to b"d".
    If the file does not exist, the digest is set to b"u".

    In addition to the digest and mode, some more properties are stored
    to decide if the recomputation of a file hash is necessary:
    - the last modification time
    - the file size
    - the inode number
    If all three (and the mode) remained the same, the digest is not recomputed.
    (For directories, these three properties are always 0.)
    """

    # File properties whose changes are relevant
    _digest: bytes = attrs.field(converter=bytes, repr=fmt_digest)
    """The blake2b hash of the file's content."""
    _mode: int = attrs.field(converter=int, repr=stat.filemode)
    """The file mode."""

    # Properties that are only used to detect changes.
    # If these have not changed, the digest is not recomputed.
    _mtime: float = attrs.field(converter=float, repr=False, eq=False)
    """The last modification time."""

    _size: int = attrs.field(converter=int, repr=False)
    """The file size in bytes."""

    _inode: int = attrs.field(converter=int, repr=False, eq=False)
    """The inode number of the file on the file system."""

    @classmethod
    def unknown(cls):
        return FileHash(b"u", 0, 0.0, 0, 0)

    def regen(self, path: str) -> Self:
        """Regenerate and return a new instance for the given file on disk.

        Parameters
        ----------
        path
            Path to a file or directory.
            If the file or directory does not exist, the hash is set to "unknown",
            i.e. the digest is set to b"u" and the mode to 0.

        Returns
        -------
        evolved
            The new hash. If the file has not changed, no new hash is created and self is returned.
            For a proper comparison between hashes, use the `==` operator, not the `is` operator.
            Two hashes are considered the same if their content, size and mode are the same,
            but timestamps and inodes may differ.
        """
        # Perform the cheap part of the hash computation
        path = Path(path)
        if not path.exists():
            digest = b"u"
            mode = 0
            mtime = 0.0
            size = 0
            inode = 0
            if self.is_unknown:
                return self
        else:
            st = path.stat()
            mode = st.st_mode
            if path.is_dir():
                if not path.endswith(os.sep):
                    raise ValueError(f"Path is a directory but does not end with separator: {path}")
                if len(self.digest) > 1:
                    raise ValueError(f"Path is a directory but old digest is from a file: {path}")
                digest = b"d"
                # Decide if we need to return self
                if self._mode == mode and self.is_dir:
                    return self
                mtime = 0.0
                size = 0
                inode = 0
            else:
                if path.endswith(os.sep):
                    raise ValueError(f"Path ends with separator but is no directory: {path}")
                if self.is_dir:
                    raise ValueError(f"Path is a file but old digest is from a directory: {path}")
                mtime = st.st_mtime
                size = st.st_size
                inode = st.st_ino
                # Decide if the digest computation can be skipped
                if (
                    self._mode == mode
                    and self._mtime == mtime
                    and self._size == size
                    and self._inode == inode
                ):
                    return self
                digest = compute_file_digest(path)
        return self.__class__(digest, mode, mtime, size, inode)

    @property
    def digest(self) -> bytes:
        return self._digest

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def mtime(self) -> float:
        return self._mtime

    @property
    def size(self) -> int:
        return self._size

    @property
    def inode(self) -> int:
        return self._inode

    @property
    def is_unknown(self):
        return self._digest == b"u"

    @property
    def is_dir(self):
        return self._digest == b"d"


@attrs.define
class InpInfo:
    """Details of ingredients used to compute the inp_digest of a StepHash."""

    inp_hashes: dict[str, FileHash] = attrs.field(factory=dict)
    env_var_values: dict[str, str | None] = attrs.field(factory=dict)


@attrs.define
class OutInfo:
    """Details of ingredients used to compute the out_digest of a StepHash."""

    out_hashes: dict[str, FileHash] = attrs.field(factory=dict)


@attrs.define
class StepHash:
    """A hash used to detect if a step can be skipped or not."""

    _inp_digest: bytes = attrs.field()
    _inp_info: InpInfo | None = attrs.field(default=None)
    _out_digest: bytes | None = attrs.field(default=None)
    _out_info: OutInfo | None = attrs.field(default=None)

    @classmethod
    def from_inp(
        cls,
        step_key: str,
        extended: bool,
        inp_hashes: list[tuple[str, FileHash]],
        env_var_values: list[tuple[str, str | None]],
    ):
        """Create a new step hash with input information only."""
        hw = HashWords()
        hw.update(step_key)
        hw.update("__inp_paths__")
        for path, file_hash in sorted(inp_hashes):
            hw.update(path)
            hw.update(file_hash.mode.to_bytes(8))
            hw.update(file_hash.size.to_bytes(8))
            hw.update(file_hash.digest)
        hw.update("__env_vars__")
        for env_var, value in sorted(env_var_values):
            hw.update(env_var)
            hw.update(value)
        inp_digest = hw.digest()
        inp_info = InpInfo(dict(inp_hashes), dict(env_var_values)) if extended else None
        return cls(inp_digest, inp_info)

    def evolve_out(self, out_hashes):
        """Create a copy of the StepHash with output information added/updated."""
        hw = HashWords()
        for path, file_hash in sorted(out_hashes):
            hw.update(path)
            hw.update(file_hash.mode.to_bytes(8))
            hw.update(file_hash.size.to_bytes(8))
            hw.update(file_hash.digest)
        out_digest = hw.digest()
        extended = self._inp_info is not None
        out_info = OutInfo(dict(out_hashes)) if extended else None
        return self.__class__(self._inp_digest, self._inp_info, out_digest, out_info)

    @property
    def inp_digest(self) -> bytes:
        return self._inp_digest

    @property
    def inp_info(self) -> InpInfo | None:
        return self._inp_info

    @property
    def out_digest(self) -> bytes:
        return self._out_digest

    @property
    def out_info(self) -> OutInfo | None:
        return self._out_info

    def reduced(self) -> Self:
        return StepHash(self._inp_digest, None, self._out_digest, None)


def compare_step_hashes(old_hash: StepHash, new_hash: StepHash) -> tuple[str, str]:
    chl = []
    sml = []
    _compare_step_digests(chl, sml, old_hash, new_hash)
    if not (old_hash.inp_info is None or new_hash.inp_info is None):
        _compare_inp_info(chl, sml, old_hash.inp_info, new_hash.inp_info)
    if not (old_hash.out_info is None or new_hash.out_info is None):
        _compare_out_info(chl, sml, old_hash.out_info, new_hash.out_info)
    changed = "\n".join(f"{descr:20s} {content}" for descr, content in chl)
    same = "\n".join(f"{descr:20s} {content}" for descr, content in sml)
    return changed, same


def _compare_step_digests(
    chl: list[tuple[str, str]], sml: list[tuple[str, str]], old_hash: StepHash, new_hash: StepHash
):
    parts = []
    changed = False

    if (old_hash.inp_info is None) == (new_hash.inp_info is None):
        parts.append(_fmt_info(old_hash))
    else:
        parts.append(_fmt_info(old_hash) + " ➜ " + _fmt_info(new_hash))

    if old_hash.inp_digest == new_hash.inp_digest:
        parts.append("inp_digest " + fmt_digest(old_hash.inp_digest))
    else:
        changed = True
        parts.append(
            "inp_digest "
            + fmt_digest(old_hash.inp_digest)
            + " ➜ "
            + fmt_digest(new_hash.inp_digest)
        )

    if old_hash.out_digest == new_hash.out_digest:
        parts.append("out_digest " + fmt_digest(old_hash.out_digest))
    else:
        changed = True
        parts.append(
            "out_digest "
            + fmt_digest(old_hash.out_digest)
            + " ➜ "
            + fmt_digest(new_hash.out_digest)
        )

    if changed:
        chl.append(("Modified step hash", ", ".join(parts)))
    else:
        sml.append(("Same step hash", ", ".join(parts)))


def _fmt_info(step_hash: StepHash) -> str:
    return "compact" if step_hash.inp_info is None else "explained"


def _compare_inp_info(
    chl: list[tuple[str, str]], sml: list[tuple[str, str]], old_info: InpInfo, new_info: InpInfo
):
    _explain_hash_changes("inp", chl, sml, old_info.inp_hashes, new_info.inp_hashes)
    _explain_env_changes(chl, sml, old_info.env_var_values, new_info.env_var_values)


def _compare_out_info(
    chl: list[tuple[str, str]], sml: list[tuple[str, str]], old_info: OutInfo, new_info: OutInfo
):
    _explain_hash_changes("out", chl, sml, old_info.out_hashes, new_info.out_hashes)


def _explain_hash_changes(
    label: str,
    chl: list[tuple[str, str]],
    sml: list[tuple[str, str]],
    old_hashes: dict[str, FileHash],
    new_hashes: dict[str, FileHash],
):
    for path in sorted(set(old_hashes) | set(new_hashes)):
        if path in old_hashes:
            if path in new_hashes:
                changed, line = report_file_hash_diff(
                    label, path, old_hashes[path], new_hashes[path]
                )
                if changed:
                    chl.append(line)
                else:
                    sml.append(line)
            else:
                chl.append((f"Deleted {label} hash", path))
        elif path in new_hashes:
            chl.append((f"Added {label} hash", path))
        else:
            raise AssertionError("This should never happen.")


def _explain_env_changes(
    chl: list[tuple[str, str]],
    sml: list[tuple[str, str]],
    old_env: dict[str, str],
    new_env: dict[str, str],
):
    for name in sorted(set(old_env) | set(new_env)):
        if name in old_env:
            old_var = fmt_env_value(old_env[name])
            if name in new_env:
                new_var = fmt_env_value(new_env[name])
                if old_env[name] == new_env[name]:
                    sml.append(("Same env var", f"{name} {old_var}"))
                else:
                    chl.append(("Modified env var", f"{name} {old_var} ➜ {new_var}"))
            else:
                chl.append(("Deleted env var", f"{name} {old_var}"))
        elif name in new_env:
            new_var = fmt_env_value(new_env[name])
            chl.append(("Added env var", f"{name} {new_var}"))
        else:
            raise AssertionError("This should never happen.")
