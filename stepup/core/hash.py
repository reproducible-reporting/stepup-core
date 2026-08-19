# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""File and step hashing.

Because the hash computation is performed in threads to avoid blocking the director process,
the functions `compute_inp_hashes`, `compute_out_hashes` and `compute_both_hashes` must be pure.
"""

import hashlib
import json
import os
import stat
import threading
from collections.abc import Mapping
from typing import Self

import attrs
from path import Path

from .cattrs import json_converter
from .exceptions import ConsistencyError, HashCancelledError, HashFailedError

__all__ = (
    "HASH_CHUNK_SIZE",
    "FileHash",
    "HashComputeResult",
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


# 256 KiB is the `_bufsize` default of `hashlib.file_digest`,
# unchanged since CPython 3.11 introduced it (still so on the 3.15 development branch).
HASH_CHUNK_SIZE = 1 << 18


@attrs.define
class HashWords:
    """An incremental SHA-256 hash of a sequence of words.

    Every word is preceded by a marker for its type,
    so that consecutive words remain distinguishable
    and an empty word differs from a missing one.
    """

    _hash = attrs.field(init=False, factory=hashlib.sha256)

    def update(self, word: str | bytes | None):
        """Add a word to the hash.

        Parameters
        ----------
        word
            The word to add.
            A `str` is encoded as UTF-8.
            `None` stands for a missing word and contributes only its marker.

        Raises
        ------
        TypeError
            When `word` is not a `str`, `bytes` or `None`.
        """
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
        """Compute the hash of the words added so far.

        Returns
        -------
        digest
            A 32-byte SHA-256 hash.
        """
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
        A 32-byte SHA-256 hash.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before the whole file was hashed.
    HashFailedError
        When `path` is a directory.
    """
    # Cheap part:
    path = Path(path)
    if path.islink() and not follow_symlinks:
        return hashlib.sha256(path.readlink().encode("utf-8")).digest()
    if path.is_dir():
        raise HashFailedError(f"File digests of directories are not supported: {path}")
    # Expensive part:
    # hashlib.file_digest is not used here:
    # the same algorithm is reimplemented with a cancellation check.
    digest = hashlib.sha256()
    buf = bytearray(HASH_CHUNK_SIZE)
    view = memoryview(buf)
    # With buffering=0, readinto performs at most one syscall,
    # so nread may be smaller than the buffer for pipes or network file systems;
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
    """Summarize the differences between two hashes of the same file.

    Parameters
    ----------
    old_hash, new_hash
        The hashes to compare.

    Returns
    -------
    diff
        A parenthesized summary of the changed digest, size and mode,
        or `None` when none of these three differ.
    """
    changes = []
    if old_hash.digest != new_hash.digest:
        changes.append(f"digest {fmt_digest(old_hash.digest)} ➜ {fmt_digest(new_hash.digest)}")
    if old_hash.size != new_hash.size:
        changes.append(f"size {old_hash.size} ➜ {new_hash.size}")
    if old_hash.mode != new_hash.mode:
        changes.append(f"mode {stat.filemode(old_hash.mode)} ➜ {stat.filemode(new_hash.mode)}")
    return f"({', '.join(changes)})" if len(changes) > 0 else None


def fmt_digest(digest: bytes | None) -> str:
    """Format a digest for display.

    Parameters
    ----------
    digest
        A 32-byte SHA-256 hash,
        the `b"u"` placeholder of a file whose hash is unknown,
        or `None` when there is no digest at all.

    Returns
    -------
    formatted
        The first eight hexadecimal characters of the digest,
        `(unset)` for `None` and `UNKNOWN` for the placeholder `b"u"`.
    """
    if digest is None:
        return "(unset)"
    if digest == b"u":
        return "UNKNOWN"
    return digest.hex()[:8]


def fmt_env_value(value: str | None) -> str:
    """Format the value of an environment variable for display.

    Parameters
    ----------
    value
        The value of the variable, or `None` when the variable is not defined.

    Returns
    -------
    formatted
        The quoted value preceded by an equals sign, or `(unset)`.
    """
    return "(unset)" if value is None else f"='{value}'"


@attrs.define(frozen=True)
class FileHash:
    """A hash of a file's content and file properties.

    For existing (regular) files, the `digest` attribute is a SHA-256 hash of the file content,
    and the mode of the file is also stored as "part of the hash".
    When the contents, the size or the mode changes, the file is considered changed.

    If the file does not exist, the hash is unknown:
    the digest is set to `b"u"` and all other properties to zero.

    In addition to the digest and the mode, some more properties are stored
    to decide whether the digest must be recomputed:

    - the last modification time
    - the file size
    - the inode number

    If all three (and also the mode) remained the same, the digest is not recomputed.
    """

    # File properties whose changes are relevant.

    _digest: bytes = attrs.field(converter=bytes, repr=fmt_digest)
    """The SHA-256 hash of the file's content."""
    _mode: int = attrs.field(converter=int, repr=stat.filemode)
    """The file mode."""

    # Properties that are only used to detect changes.
    # If these have not changed, the digest is not recomputed.

    # Note that _mtime and _inode are not used for sorting,
    # to ensure deterministic order across builds when sorting by FileHash instance.

    _mtime: float = attrs.field(converter=float, repr=False, eq=False)
    """The last modification time."""

    _size: int = attrs.field(converter=int, repr=False)
    """The file size in bytes."""

    _inode: int = attrs.field(converter=int, repr=False, eq=False)
    """The inode number of the file on the file system."""

    @classmethod
    def unknown(cls):
        """Create the hash of a file that does not exist."""
        return cls(b"u", 0, 0.0, 0, 0)

    def regen(self, path: str, cancel_event: threading.Event | None = None) -> Self:
        """Regenerate and return a new instance for the given file on disk.

        Parameters
        ----------
        path
            Path to a file.
            If the file cannot be stat'ed, the hash is set to "unknown",
            i.e. the digest is set to `b"u"` and the mode to 0.
            This covers a missing file, a broken symbolic link
            and a trailing separator on a path that is not a directory.
        cancel_event
            When given, passed on to `compute_file_digest`
            so a digest computation in progress can be aborted promptly.

        Returns
        -------
        refreshed
            The new hash.
            If the file has not changed, no new hash is created and `self` is returned.
            For a proper comparison between hashes, use the `==` operator, not the `is` operator.
            Two hashes are considered the same if their content, size and mode are the same,
            but timestamps and inodes may differ.

        Raises
        ------
        HashCancelledError
            When `cancel_event` was set before the whole file was hashed.
        HashFailedError
            When `path` is an existing directory.
        """
        # Check for cancellation early.
        if cancel_event is not None and cancel_event.is_set():
            raise HashCancelledError(path)
        # A single stat call collects every property below and doubles as the existence test.
        path = Path(path)
        try:
            st = os.stat(path)
        except OSError:
            return self if self.is_unknown else self.unknown()
        # Decide whether the digest computation can be skipped.
        if (
            self._mode == st.st_mode
            and self._mtime == st.st_mtime
            and self._size == st.st_size
            and self._inode == st.st_ino
        ):
            return self
        # Directories are rejected by compute_file_digest.
        digest = compute_file_digest(path, cancel_event=cancel_event)
        return self.__class__(digest, st.st_mode, st.st_mtime, st.st_size, st.st_ino)

    @property
    def digest(self) -> bytes:
        """The SHA-256 hash of the file's content, or `b"u"` when the hash is unknown."""
        return self._digest

    @property
    def mode(self) -> int:
        """The file mode, in the encoding of `os.stat_result.st_mode`."""
        return self._mode

    @property
    def mtime(self) -> float:
        """The last modification time, in seconds since the epoch."""
        return self._mtime

    @property
    def size(self) -> int:
        """The file size in bytes."""
        return self._size

    @property
    def inode(self) -> int:
        """The inode number of the file on the file system."""
        return self._inode

    @property
    def is_unknown(self):
        """Whether the digest is the placeholder for a file that does not exist."""
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
    """Details of the ingredients used to compute the `inp_digest` of a `StepHash`."""

    inp_hashes: dict[str, FileHash] = attrs.field(factory=dict)
    env_values: dict[str, str | None] = attrs.field(factory=dict)
    env_overrides: dict[str, str] = attrs.field(factory=dict)


@attrs.define
class OutInfo:
    """Details of the ingredients used to compute the `out_digest` of a `StepHash`."""

    out_hashes: dict[str, FileHash] = attrs.field(factory=dict)


def _update_file_hashes(hw: HashWords, file_hashes: Mapping[str, FileHash]):
    """Add a mapping of file hashes to `hw`, sorted by path.

    Sorting here makes the digest independent of the order in which
    the caller happened to collect the paths.
    Both digests of a `StepHash` are built with this function,
    so their file contribution can never drift apart.
    """
    for path in sorted(file_hashes):
        file_hash = file_hashes[path]
        hw.update(path)
        hw.update(file_hash.mode.to_bytes(8))
        hw.update(file_hash.size.to_bytes(8))
        hw.update(file_hash.digest)


@attrs.define
class StepHash:
    """A hash used to detect whether a step can be skipped.

    The input digest covers everything a step depends on:
    its label, whether it is a shell command,
    its input files and the environment variables and overrides it uses.
    The output digest covers the files the step has created.

    A step hash is either compact or extended.
    An extended hash also keeps the ingredients of both digests (`InpInfo` and `OutInfo`),
    so that a difference between two hashes can be explained in detail.
    """

    _inp_digest: bytes = attrs.field()
    _inp_info: InpInfo | None = attrs.field(default=None)
    _out_digest: bytes | None = attrs.field(default=None)
    _out_info: OutInfo | None = attrs.field(default=None)

    @classmethod
    def from_inp(
        cls,
        step_label: str,
        extended: bool,
        inp_hashes: Mapping[str, FileHash],
        env_values: dict[str, str | None],
        shell: bool = False,
        env_overrides: dict[str, str] | None = None,
    ):
        """Create a new step hash with input information only.

        The environment variables are sorted here, so the digest does not depend on the order
        in which the caller happened to collect them.
        """
        env_overrides = {} if env_overrides is None else env_overrides
        hw = HashWords()
        hw.update(step_label)
        hw.update("__shell__")
        hw.update(bytes([int(shell)]))
        hw.update("__inp_paths__")
        _update_file_hashes(hw, inp_hashes)
        hw.update("__env_vars__")
        for env_var, value in sorted(env_values.items()):
            hw.update(env_var)
            hw.update(value)
        hw.update("__env_overrides__")
        for name, value in sorted(env_overrides.items()):
            hw.update(name)
            hw.update(value)
        inp_digest = hw.digest()
        inp_info = (
            InpInfo(dict(inp_hashes), dict(env_values), dict(env_overrides)) if extended else None
        )
        return cls(inp_digest, inp_info)

    def evolve_out(self, out_hashes: Mapping[str, FileHash]):
        """Create a copy of the StepHash with output information added/updated."""
        hw = HashWords()
        _update_file_hashes(hw, out_hashes)
        out_digest = hw.digest()
        extended = self._inp_info is not None
        out_info = OutInfo(dict(out_hashes)) if extended else None
        return self.__class__(self._inp_digest, self._inp_info, out_digest, out_info)

    @property
    def inp_digest(self) -> bytes:
        """The digest of the step's inputs."""
        return self._inp_digest

    @property
    def inp_info(self) -> InpInfo | None:
        """The ingredients of `inp_digest`, or `None` for a compact hash."""
        return self._inp_info

    @property
    def out_digest(self) -> bytes | None:
        """The digest of the step's outputs, or `None` when they have not been hashed yet."""
        return self._out_digest

    @property
    def out_info(self) -> OutInfo | None:
        """The ingredients of `out_digest`, or `None` for a compact or output-less hash."""
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
    """Explain the differences between two hashes of the same step.

    Parameters
    ----------
    old_hash, new_hash
        The hashes to compare.

    Returns
    -------
    changed, same
        Two multi-line reports, one for the ingredients that differ
        and one for those that are identical.
        Input and output ingredients are only detailed when both hashes are extended.
        Files and environment variables are listed in sorted order.
    """
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

    This is the return value of the `compute_inp_hashes` and `compute_out_hashes` functions.
    """

    messages: list[str] = attrs.field()
    """What the caller must report about the files, one line per file.

    The wording depends on the function that produced the result:
    `compute_inp_hashes` writes a full sentence per unexpectedly changed or vanished input,
    while `compute_out_hashes` writes the bare path of each output that is missing.
    """

    new_hashes: dict[str, FileHash] = attrs.field()
    """The new hashes of the inputs/outputs whose hash changed, keyed by path."""

    all_hashes: dict[str, FileHash] = attrs.field()
    """The new hashes of all inputs/outputs, keyed by path, regardless of changes."""


def compute_inp_hashes(
    inp_hashes: Mapping[str, FileHash], cancel_event: threading.Event
) -> HashComputeResult:
    """Compute the new hashes of the inputs.

    Parameters
    ----------
    inp_hashes
        The old hashes of the input files, keyed by path.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    result
        The result of the hash computation.
        The `messages` attribute contains a list of unexpected input changes or vanished inputs.
        The paths are processed in sorted order, so the messages are reported deterministically
        and both hash dictionaries are ordered by path.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before all files were hashed.
    HashFailedError
        When an input turned out to be a directory.
    """
    messages = []
    new_inp_hashes = {}
    all_inp_hashes = {}
    for path in sorted(inp_hashes):
        old_file_hash = inp_hashes[path]
        new_file_hash = old_file_hash.regen(path, cancel_event)
        all_inp_hashes[path] = new_file_hash
        if new_file_hash != old_file_hash:
            # Collect changed hashes, so callers can process them efficiently.
            new_inp_hashes[path] = new_file_hash
            # If am input hash has changed,
            # corresponding input files have changed or disappeared unexpectedly,
            # which must be reported.
            if new_file_hash.is_unknown:
                messages.append(f"Input vanished unexpectedly: {path}")
            else:
                messages.append(
                    f"Input changed unexpectedly: {path} "
                    + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )
        elif old_file_hash.is_unknown:
            raise ConsistencyError("A step was scheduled with a missing input file.")

    return HashComputeResult(messages, new_inp_hashes, all_inp_hashes)


def compute_out_hashes(
    out_hashes: Mapping[str, FileHash], cancel_event: threading.Event
) -> HashComputeResult:
    """Compute the new hashes of the outputs.

    Parameters
    ----------
    out_hashes
        The old hashes of the output files, keyed by path.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    result
        The result of the hash computation.
        The `messages` attribute contains a list of missing output paths.
        The paths are processed in sorted order, as in `compute_inp_hashes`.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before all files were hashed.
    HashFailedError
        When an output turned out to be a directory.
    """
    messages = []
    new_out_hashes = {}
    all_out_hashes = {}
    for path in sorted(out_hashes):
        old_file_hash = out_hashes[path]
        new_file_hash = old_file_hash.regen(path, cancel_event)
        all_out_hashes[path] = new_file_hash
        # Collect changed hashes, so callers can process them efficiently.
        if new_file_hash != old_file_hash:
            new_out_hashes[path] = new_file_hash
        # Missing files are always reported, even if they are missing again (unchanged hash).
        if new_file_hash.is_unknown:
            messages.append(path)

    return HashComputeResult(messages, new_out_hashes, all_out_hashes)


def compute_both_hashes(
    inp_hashes: Mapping[str, FileHash],
    out_hashes: Mapping[str, FileHash],
    cancel_event: threading.Event,
) -> tuple[HashComputeResult, HashComputeResult]:
    """Compute input and output hashes.

    Parameters
    ----------
    inp_hashes
        The old hashes of the input files, keyed by path.
    out_hashes
        The old hashes of the output files, keyed by path.
    cancel_event
        Set this event to cancel the hash computation.

    Returns
    -------
    inp_results, out_results
        The results of `compute_inp_hashes` and `compute_out_hashes`, respectively.

    Raises
    ------
    HashCancelledError
        When `cancel_event` was set before all files were hashed.
    HashFailedError
        When an input or output turned out to be a directory.
    """
    return (
        compute_inp_hashes(inp_hashes, cancel_event),
        compute_out_hashes(out_hashes, cancel_event),
    )
