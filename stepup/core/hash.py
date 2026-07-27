# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""File and step hashing.

Hash computation is the only blocking work the director does. `ThreadWorker` runs it in a
dedicated thread, one per hash computation, so the director's event loop stays responsive;
`compute_inp_hashes` / `compute_out_hashes` / `compute_both_hashes` are the pure functions run
inside such a thread.
"""

import hashlib
import json
import os
import stat
import threading
from typing import Self

import attrs
from path import Path

from .cattrs import json_converter
from .exceptions import HashCancelledError

__all__ = (
    "HASH_CHUNK_SIZE",
    "FileHash",
    "HashWords",
    "InpInfo",
    "OutInfo",
    "StepHash",
    "compare_step_hashes",
    "compute_both_hashes",
    "compute_file_digest",
    "compute_inp_hashes",
    "compute_out_hashes",
    "fmt_digest",
    "fmt_env_value",
    "fmt_file_hash_diff",
)


HASH_CHUNK_SIZE = 1 << 18  # 256 KiB — matches hashlib.file_digest's own internal buffer size.


@attrs.define
class HashWords:
    _hash = attrs.field(init=False, factory=hashlib.sha256)

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


def compute_file_digest(
    path: str, follow_symlinks: bool = True, cancel_event: threading.Event | None = None
) -> bytes:
    """Compute the SHA-256 digest of a file or a symbolic link.

    Parameters
    ----------
    path
        The file of which the hash must be computed.
    follow_symlinks
        If True (default) and the path is a symbolic link,
        try to hash the contents of the destination file.
        If False, the destination path itself is hashed.
    cancel_event
        When given, the event is checked between chunks of `HASH_CHUNK_SIZE` bytes,
        so an in-progress hash of a large file can be aborted promptly.

    Returns
    -------
    digest
        A 32 bytes SHA-256 hash.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before the whole file was hashed.
    """
    # Cheap part:
    path = Path(path)
    if path.islink() and not follow_symlinks:
        return hashlib.sha256(path.readlink().encode("utf-8")).digest()
    if path.is_dir():
        raise OSError("File digests of directories are not supported.")
    # Expensive part:
    # Not using hashlib.file_digest, same algorithm reimplemented here with a cancellation check.
    digest = hashlib.sha256()
    buf = bytearray(HASH_CHUNK_SIZE)
    view = memoryview(buf)
    # With buffering=0, readinto performs at most one syscall,
    # so nread may be smaller than the buffer for pipes or network filesystems;
    # only nread == 0 means EOF.
    with open(path, "rb", buffering=0) as fh:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise HashCancelledError(path)
            nread = fh.readinto(buf)
            if nread == 0:
                break
            digest.update(view[:nread])
    return digest.digest()


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
        return "?"
    return digest.hex()[:8]


def fmt_env_value(value: str | None) -> str:
    return "(unset)" if value is None else f"='{value}'"


@attrs.define(frozen=True)
class FileHash:
    """A hash of a file's content and file properties.

    For existing (regular) files, the digest attribute is a SHA-256 hash of the file content,
    and the mode of the file is also stored as "part of the hash".
    When either contents, size or mode changes, the file is considered changed.

    If the file does not exist, the digest is set to b"u".

    In addition to the digest and mode, some more properties are stored
    to decide if the recomputation of a file hash is necessary:

    - the last modification time
    - the file size
    - the inode number

    If all three (and also the mode) remained the same, the digest is not recomputed.
    """

    # File properties whose changes are relevant
    _digest: bytes = attrs.field(converter=bytes, repr=fmt_digest)
    """The SHA-256 hash of the file's content."""
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
        return cls(b"u", 0, 0.0, 0, 0)

    def regen(self, path: str, cancel_event: threading.Event | None = None) -> Self:
        """Regenerate and return a new instance for the given file on disk.

        Parameters
        ----------
        path
            Path to a file or directory.
            If the file or directory does not exist, the hash is set to "unknown",
            i.e. the digest is set to b"u" and the mode to 0.
        cancel_event
            When given, passed on to `compute_file_digest`
            so a digest computation in progress can be aborted promptly.

        Returns
        -------
        evolved
            The new hash. If the file has not changed, no new hash is created and self is returned.
            For a proper comparison between hashes, use the `==` operator, not the `is` operator.
            Two hashes are considered the same if their content, size and mode are the same,
            but timestamps and inodes may differ.

        Raises
        ------
        HashCancelledError
            When `cancel_event` was set before the whole file was hashed.
        """
        # Check for cancellation early.
        if cancel_event is not None and cancel_event.is_set():
            raise HashCancelledError(path)
        # Check if the file exists and is a regular file.
        path = Path(path)
        if not path.exists():
            return self if self.is_unknown else self.unknown()
        # Check if the hash computation can be skipped.
        st = path.stat()
        mode = st.st_mode
        if path.is_dir():
            raise ValueError(f"File digests of directories are not supported: {path}")
        if path.endswith(os.sep):
            raise ValueError(f"File digests of directories are not supported: {path}")
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
        digest = compute_file_digest(path, cancel_event=cancel_event)
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

    def to_json(self) -> str | None:
        """Serialize to the JSON representation stored in `file.hash`, or `None` if unknown."""
        if self.is_unknown:
            return None
        return json.dumps(json_converter.unstructure(self))

    @classmethod
    def from_json(cls, value: str | None) -> Self:
        """Deserialize from the JSON representation stored in `file.hash`."""
        if value is None:
            return cls.unknown()
        return json_converter.structure(json.loads(value), cls)


@attrs.define
class InpInfo:
    """Details of ingredients used to compute the inp_digest of a StepHash."""

    inp_hashes: dict[str, FileHash] = attrs.field(factory=dict)
    env_values: dict[str, str | None] = attrs.field(factory=dict)
    env_overrides: dict[str, str] = attrs.field(factory=dict)


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
        env_values: dict[str, str | None],
        subshell: bool = False,
        env_overrides: dict[str, str] | None = None,
    ):
        """Create a new step hash with input information only."""
        env_overrides = {} if env_overrides is None else env_overrides
        hw = HashWords()
        hw.update(step_key)
        hw.update("__subshell__")
        hw.update(bytes([int(subshell)]))
        hw.update("__inp_paths__")
        for path, file_hash in sorted(inp_hashes):
            hw.update(path)
            hw.update(file_hash.mode.to_bytes(8))
            hw.update(file_hash.size.to_bytes(8))
            hw.update(file_hash.digest)
        hw.update("__env_vars__")
        for env_var, value in sorted(env_values.items()):
            hw.update(env_var)
            hw.update(value)
        # Only mix in env_overrides when present, so steps without overrides keep their digest.
        if env_overrides:
            hw.update("__env_overrides__")
            for name, value in sorted(env_overrides.items()):
                hw.update(name)
                hw.update(value)
        inp_digest = hw.digest()
        inp_info = (
            InpInfo(dict(inp_hashes), dict(env_values), dict(env_overrides)) if extended else None
        )
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
    def out_digest(self) -> bytes | None:
        return self._out_digest

    @property
    def out_info(self) -> OutInfo | None:
        return self._out_info

    def to_json(self) -> str:
        """Serialize to the JSON representation stored in `step.hash`."""
        return json.dumps(json_converter.unstructure(self))

    @classmethod
    def from_json(cls, value: str | None) -> Self | None:
        """Deserialize from the JSON representation stored in `step.hash`."""
        if value is None:
            return None
        return json_converter.structure(json.loads(value), cls)


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
    _explain_env_dict_changes(chl, sml, old_info.env_values, new_info.env_values, "env var")
    _explain_env_dict_changes(
        chl, sml, old_info.env_overrides, new_info.env_overrides, "env override"
    )


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
                changed, line = _report_file_hash_diff(
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


def _report_file_hash_diff(
    label: str, path: str, old_hash: "FileHash", new_hash: "FileHash"
) -> tuple[bool, tuple[str, str]]:
    change = fmt_file_hash_diff(old_hash, new_hash)
    if change is None:
        return False, (f"Same {label} hash", path)
    return True, (f"Modified {label} hash", f"{path} {change}")


def _explain_env_dict_changes(
    chl: list[tuple[str, str]],
    sml: list[tuple[str, str]],
    old_env: dict[str, str | None],
    new_env: dict[str, str | None],
    label: str,
):
    for name in sorted(set(old_env) | set(new_env)):
        if name in old_env:
            old_var = fmt_env_value(old_env[name])
            if name in new_env:
                new_var = fmt_env_value(new_env[name])
                if old_env[name] == new_env[name]:
                    sml.append((f"Same {label}", f"{name} {old_var}"))
                else:
                    chl.append((f"Modified {label}", f"{name} {old_var} ➜ {new_var}"))
            else:
                chl.append((f"Deleted {label}", f"{name} {old_var}"))
        elif name in new_env:
            new_var = fmt_env_value(new_env[name])
            chl.append((f"Added {label}", f"{name} {new_var}"))
        else:
            raise AssertionError("This should never happen.")


#
# Pure functions for threaded hash computation
#


@attrs.define
class HashComputeResult:
    """The result of a hash computation.

    This is used as a return value of the `compute_...` functions below.
    """

    messages: list[str] = attrs.field()
    """Messages about unexpected input changes or vanished inputs."""

    new_hashes: list[tuple[str, FileHash]] = attrs.field()
    """A list of tuples `(path, new_file_hash)` for inputs/outputs whose hash changed."""

    all_hashes: list[tuple[str, FileHash]] = attrs.field()
    """A list of tuples `(path, new_file_hash)` for all inputs/outputs, regardless of changes."""


def compute_inp_hashes(
    inp_hashes: list[tuple[str, FileHash]], cancel_event: threading.Event
) -> HashComputeResult:
    """Compute the new hashes of the inputs.

    Parameters
    ----------
    inp_hashes
        A list of tuples `(path, old_file_hash)` for each input file.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    HashComputeResult
        The result of the hash computation.
        The messages attribute contains a list of unexpected input changes or vanished inputs.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before the whole file was hashed.
    """
    messages = []
    new_inp_hashes = []
    all_inp_hashes = []
    for path, old_file_hash in sorted(inp_hashes):
        new_file_hash = old_file_hash.regen(path, cancel_event)
        all_inp_hashes.append((path, new_file_hash))
        if new_file_hash != old_file_hash:
            new_inp_hashes.append((path, new_file_hash))
            if new_file_hash.is_unknown:
                messages.append(f"Input vanished unexpectedly: {path} ")
            else:
                messages.append(
                    f"Input changed unexpectedly: {path} "
                    + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )

    return HashComputeResult(messages, new_inp_hashes, all_inp_hashes)


def compute_out_hashes(
    out_hashes: list[tuple[str, FileHash]], cancel_event: threading.Event
) -> HashComputeResult:
    """Compute the new hashes of the outputs.

    Parameters
    ----------
    out_hashes
        A list of tuples `(path, old_file_hash)` for each output file.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    HashComputeResult
        The result of the hash computation.
        The messages attribute contains a list of missing output paths.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before the whole file was hashed.
    """
    out_missing = []
    new_out_hashes = []
    all_out_hashes = []
    for path, old_file_hash in sorted(out_hashes):
        new_file_hash = old_file_hash.regen(path, cancel_event)
        all_out_hashes.append((path, new_file_hash))
        if new_file_hash != old_file_hash:
            new_out_hashes.append((path, new_file_hash))
        if new_file_hash.is_unknown:
            out_missing.append(path)

    return HashComputeResult(out_missing, new_out_hashes, all_out_hashes)


def compute_both_hashes(
    inp_hashes: list[tuple[str, FileHash]],
    out_hashes: list[tuple[str, FileHash]],
    cancel_event: threading.Event,
) -> tuple[HashComputeResult, HashComputeResult]:
    """Compute input and output hashes.

    Parameters
    ----------
    inp_hashes
        A list of tuples `(path, file_hash)` for each input file.
    out_hashes
        A list of tuples `(path, file_hash)` for each output file.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    inp_results, out_results
        The results of `compute_inp_hashes` and `compute_out_hashes`, respectively.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before the whole file was hashed.
    """
    return (
        compute_inp_hashes(inp_hashes, cancel_event),
        compute_out_hashes(out_hashes, cancel_event),
    )
