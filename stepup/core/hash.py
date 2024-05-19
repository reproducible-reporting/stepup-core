# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright (C) 2024 Toon Verstraelen
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
    "HashWords",
    "compute_file_digest",
    "FileHash",
    "StepHash",
    "ExtendedStepHash",
    "compare_step_hashes",
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
            TypeError(f"Not supported by HashWords: {type(word)}")

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


@attrs.define
class FileHash:
    _digest: bytes = attrs.field()
    # for caching the hash
    _mode: int = attrs.field()
    _mtime: float = attrs.field()
    _size: int = attrs.field()
    _inode: int = attrs.field()

    @classmethod
    def unknown(cls):
        return FileHash(b"u", 0, 0.0, 0, 0)

    @classmethod
    def structure(cls, state) -> Self:
        return cls(*state)

    def unstructure(self):
        return [self._digest, self._mode, self._mtime, self._size, self._inode]

    def update(self, path: str) -> bool | None:
        """Update the hash given the for the given file on disk.

        Parameters
        ----------
        path
            Path to a file. Directories are not allowed.

        Returns
        -------
        changed
            None if the file does not exist.
            True when the file is not up-to-date.
            False otherwise
        """
        path = Path(path)
        if not path.exists():
            return None
        if path.is_dir():
            if not path.endswith(os.sep):
                raise ValueError(f"Path is a directory but does not end with separator: {path}")
            if self._digest == b"d":
                return False
            self._digest = b"d"
            self._mode = 0
            self._mtime = 0.0
            self._size = 0
            self._inode = 0
            return True
        if path.endswith(os.sep):
            raise ValueError(f"Path ends with separator but is no directory: {path}")
        st = path.stat()
        if (
            self._mode == st.st_mode
            and self._mtime == st.st_mtime
            and self._size == st.st_size
            and self._inode == st.st_ino
        ):
            return False
        changed = (self._mode != st.st_mode) | (self._size != st.st_size)
        self._mode = st.st_mode
        self._mtime = st.st_mtime
        self._size = st.st_size
        self._inode = st.st_ino
        digest = compute_file_digest(path)
        changed |= digest != self._digest
        self._digest = digest
        return changed

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def size(self) -> int:
        return self._size

    @property
    def digest(self) -> bytes:
        return self._digest


def _compute_step_digest(
    step_key: str,
    inp_hashes: list[tuple[str, FileHash]],
    env_var_values: list[tuple[str, str | None]],
    out_hashes: list[tuple[str, FileHash]],
):
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
    hw.update("__out_paths__")
    inp_digest = hw.digest()
    for path, file_hash in sorted(out_hashes):
        hw.update(path)
        hw.update(file_hash.mode.to_bytes(8))
        hw.update(file_hash.size.to_bytes(8))
        hw.update(file_hash.digest)
    return hw.digest(), inp_digest, dict(inp_hashes), dict(env_var_values), dict(out_hashes)


@attrs.define
class StepHash:
    """A hash used to detect if a step can be skipped or not."""

    _digest: bytes = attrs.field()
    _inp_digest: bytes = attrs.field()

    @classmethod
    def structure(cls, state, strings: list[str]):
        if len(state) == 2:
            return StepHash(*state)
        elif len(state) == 5:
            return ExtendedStepHash(
                state[0],
                state[1],
                {strings[path]: FileHash.structure(data) for path, data in state[2]},
                state[3],
                {strings[path]: FileHash.structure(data) for path, data in state[4]},
            )
        else:
            TypeError(f"Cannot structure as StepHash: {state}")

    def unstructure(self, lookup: dict[str, int]) -> list:
        return [self.digest, self.inp_digest]

    @property
    def digest(self) -> bytes:
        return self._digest

    @property
    def inp_digest(self) -> bytes:
        return self._inp_digest

    def reduce(self) -> Self:
        return self


@attrs.define
class ExtendedStepHash(StepHash):
    """An extended hash used to detect if a step can be skipped or not.

    All inputs for the digest are also stored, to be able to explain
    why a step cannot be skipped.
    """

    inp_hashes: dict[str, FileHash] = attrs.field(factory=dict)
    env_var_values: dict[str, str | None] = attrs.field(factory=dict)
    out_hashes: dict[str, FileHash] = attrs.field(factory=dict)

    def unstructure(self, lookup: dict[str, int]) -> list:
        return [
            self.digest,
            self.inp_digest,
            [(lookup[path], fh.unstructure()) for path, fh in self.inp_hashes.items()],
            self.env_var_values,
            [(lookup[path], fh.unstructure()) for path, fh in self.out_hashes.items()],
        ]

    def reduce(self) -> StepHash:
        return StepHash(self._digest, self._inp_digest)


def create_step_hash(
    step_key: str,
    extended: bool,
    inp_hashes: list[tuple[str, FileHash]],
    env_var_values: list[tuple[str, str | None]],
    out_hashes: list[tuple[str, FileHash]],
):
    args = _compute_step_digest(step_key, inp_hashes, env_var_values, out_hashes)
    if extended:
        return ExtendedStepHash(*args)
    else:
        return StepHash(*args[:2])


def compare_step_hashes(old_hash: StepHash, new_hash: StepHash) -> tuple[str, str]:
    chl = []
    sml = []
    _compare_step_digests(chl, sml, old_hash, new_hash)
    if isinstance(old_hash, ExtendedStepHash) and isinstance(new_hash, ExtendedStepHash):
        _compare_extended_hashes(chl, sml, old_hash, new_hash)
    changed = "\n".join(f"{descr:20s} {content}" for descr, content in chl)
    same = "\n".join(f"{descr:20s} {content}" for descr, content in sml)
    return changed, same


def _compare_step_digests(
    chl: list[tuple[str, str]], sml: list[tuple[str, str]], old_hash: StepHash, new_hash: StepHash
):
    parts = []
    changed = False

    if old_hash.__class__ is new_hash.__class__:
        parts.append(_fmt_class(old_hash))
    else:
        parts.append(_fmt_class(old_hash) + " ➜ " + _fmt_class(new_hash))

    if old_hash.digest == new_hash.digest:
        parts.append("digest " + _fmt_digest(old_hash.digest))
    else:
        changed = True
        parts.append(
            "digest " + _fmt_digest(old_hash.digest) + " ➜ " + _fmt_digest(new_hash.digest)
        )

    if old_hash.inp_digest == new_hash.inp_digest:
        parts.append("inp_digset " + _fmt_digest(old_hash.inp_digest))
    else:
        changed = True
        parts.append(
            "inp_digset "
            + _fmt_digest(old_hash.inp_digest)
            + " ➜ "
            + _fmt_digest(new_hash.inp_digest)
        )

    if changed:
        chl.append(("Modified step hash", ", ".join(parts)))
    else:
        sml.append(("Same step hash", ", ".join(parts)))


def _fmt_class(step_hash: StepHash) -> str:
    return "extended" if isinstance(step_hash, ExtendedStepHash) else "compact"


def _compare_extended_hashes(
    chl: list[tuple[str, str]],
    sml: list[tuple[str, str]],
    old_hash: ExtendedStepHash,
    new_hash: ExtendedStepHash,
):
    _explain_hash_changes("inp", chl, sml, old_hash.inp_hashes, new_hash.inp_hashes)
    _explain_env_changes(chl, sml, old_hash.env_var_values, new_hash.env_var_values)
    _explain_hash_changes("out", chl, sml, old_hash.out_hashes, new_hash.out_hashes)


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
                changed, line = _report_hash_diff(label, path, old_hashes[path], new_hashes[path])
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


def _report_hash_diff(
    label: str, path: str, old_hash: FileHash, new_hash: FileHash
) -> tuple[bool, tuple[str, str]]:
    changes = []

    if old_hash.digest != new_hash.digest:
        changes.append(f"digest {_fmt_digest(old_hash.digest)} ➜ {_fmt_digest(new_hash.digest)}")
    if old_hash.size != new_hash.size:
        changes.append(f"size {old_hash.size} ➜ {new_hash.size}")
    if old_hash.mode != new_hash.mode:
        changes.append(f"mode {stat.filemode(old_hash.mode)} ➜ {stat.filemode(new_hash.mode)}")
    if len(changes) > 0:
        return True, (f"Modified {label} hash", f"{path} ({', '.join(changes)})")
    else:
        return False, (f"Same {label} hash", path)


def _fmt_digest(digest: bytes) -> str:
    if len(digest) == 1:
        if digest == b"u":
            return "UNKNOWN"
        if digest == b"d":
            return "DIRECTORY"
        return "?"
    return digest.hex()[:8]


def _explain_env_changes(
    chl: list[tuple[str, str]],
    sml: list[tuple[str, str]],
    old_env: dict[str, str],
    new_env: dict[str, str],
):
    for name in sorted(set(old_env) | set(new_env)):
        if name in old_env:
            old_var = _fmt_env_value(old_env[name])
            if name in new_env:
                new_var = _fmt_env_value(new_env[name])
                if old_env[name] == new_env[name]:
                    sml.append(("Same env var", f"{name} {old_var}"))
                else:
                    chl.append(("Modified env var", f"{name} {old_var} ➜ {new_var}"))
            else:
                chl.append(("Deleted env var", f"{name} {old_var}"))
        elif name in new_env:
            new_var = _fmt_env_value(new_env[name])
            chl.append(("Added env var", f"{name} {new_var}"))
        else:
            raise AssertionError("This should never happen.")


def _fmt_env_value(value: str | None) -> str:
    return "(unset)" if value is None else f"='{value}'"
