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
from collections.abc import Iterator, Mapping
from typing import Self

import attrs
from path import Path

from .cattrs import json_converter
from .exceptions import ConsistencyError, HashCancelledError, HashFailedError

__all__ = (
    "HASH_CHUNK_SIZE",
    "FileHash",
    "HashComputeResult",
    "InpHashComputeResult",
    "InpInfo",
    "OutInfo",
    "StepHash",
    "compare_step_hashes",
    "compute_both_hashes",
    "compute_file_digest",
    "compute_inp_hashes",
    "compute_out_hashes",
    "fmt_env_value",
    "fmt_file_hash_diff",
    "fmt_full_digest",
    "fmt_short_digest",
)


# 256 KiB is the `_bufsize` default of `hashlib.file_digest`,
# unchanged since CPython 3.11 introduced it (still so on the 3.15 development branch).
HASH_CHUNK_SIZE = 1 << 18


#
# Digest primitives
#


@attrs.define
class HashWords:
    """An incremental SHA-256 hash of a sequence of words.

    Every word is preceded by a marker for its type,
    so that an empty word differs from a missing one
    and a `str` never collides with the `bytes` holding the same characters.

    The markers alone do not make the encoding injective:
    a word whose own bytes contain a marker sequence can imitate a word boundary.
    The words hashed within StepUp cannot collide,
    because every variable-length word is a label, a path or an environment variable
    name or value, none of which may contain a null byte.
    A caller that feeds arbitrary binary words to this class cannot rely on that.
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
        The file or symbolic link of which the hash must be computed.
    follow_symlinks
        What to hash when `path` is a symbolic link.
        If True (default), the contents of the file it points to.
        If False, the link target as stored in the link itself, encoded as UTF-8.
        A link therefore has two unrelated digests, one per setting,
        which are never comparable.
        This argument has no effect on a path that is not a symbolic link.
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
        When `path` is a directory,
        including a followed symbolic link that points to one.
    OSError
        When the file cannot be opened,
        e.g. a followed symbolic link whose target does not exist.
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


#
# Formatting of hash ingredients
#


def fmt_short_digest(digest: bytes | None) -> str:
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


def fmt_full_digest(digest: bytes) -> str:
    """Format a 32-byte digest as eight space-separated 8-character hex words."""
    hexdigest = digest.hex()
    return " ".join(hexdigest[i : i + 8] for i in range(0, 64, 8))


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


#
# File hashes
#


@attrs.define(frozen=True)
class FileHash:
    """A hash of a file's content and file properties.

    For existing (regular) files, the `digest` attribute is a SHA-256 hash of the file content,
    while the mode and the size of the file are also stored as "part of the hash".

    If the file does not exist, the hash is unknown:
    the digest is set to `b"u"` and all other properties to zero.

    The fields feed two comparisons that deliberately disagree.
    The `refreshed` method asks whether the content may have changed
    and answers from the `stat` result alone,
    so it also consults the modification time and the inode number.
    It errs on the eager side, because a needless digest computation only costs time,
    while a missed change would silently build against stale content.
    Equality asks whether the workflow must react to the file
    and therefore ignores the modification time and the inode number (`eq=False`),
    because these are not a function of the source files and the plan code.
    A fresh checkout or a copy of a project reproduces the same content
    under a new inode and a new timestamp,
    which must not mark every consuming step pending.

    As a result, `refreshed` may return a new instance that compares equal to the one it
    replaces and differs from it only in the properties that are excluded from equality.
    """

    # File properties whose changes are relevant to the workflow.

    digest: bytes = attrs.field(converter=bytes, repr=fmt_short_digest)
    """The SHA-256 hash of the file's content, or `b"u"` when the hash is unknown."""

    mode: int = attrs.field(converter=int, repr=stat.filemode)
    """The file mode, in the encoding of `os.stat_result.st_mode`."""

    size: int = attrs.field(converter=int, repr=False)
    """The file size in bytes."""

    # Properties that are only used to decide whether the digest must be recomputed.
    # They are excluded from equality, for the reason given in the class docstring.

    mtime: float = attrs.field(converter=float, repr=False, eq=False)
    """The last modification time, in seconds since the epoch."""

    inode: int = attrs.field(converter=int, repr=False, eq=False)
    """The inode number of the file on the file system."""

    @classmethod
    def unknown(cls):
        """Create the hash of a file that does not exist."""
        return cls(b"u", 0, 0, 0.0, 0)

    def refreshed(self, path: str, cancel_event: threading.Event | None = None) -> Self:
        """Return the current hash of the given file on disk.

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
            For a proper comparison between hashes, use the `==` operator, not the `is` operator,
            keeping in mind that it ignores the properties listed in the class docstring.

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
            self.mode == st.st_mode
            and self.mtime == st.st_mtime
            and self.size == st.st_size
            and self.inode == st.st_ino
        ):
            return self
        # Directories are rejected by compute_file_digest.
        digest = compute_file_digest(path, cancel_event=cancel_event)
        return self.__class__(digest, st.st_mode, st.st_size, st.st_mtime, st.st_ino)

    def stat_differs(self, other: Self) -> bool:
        """Whether the `stat` properties left out of `==` differ from those of `other`.

        See the class docstring for why they are excluded from equality.
        Two unknown hashes never differ here, because `unknown` zeroes both properties,
        which is what lets a caller use this to decide whether a stored hash is worth rewriting
        without having to exclude the unknown hash first.
        """
        return self.mtime != other.mtime or self.inode != other.inode

    @property
    def is_unknown(self):
        """Whether the digest is the placeholder for a file that does not exist."""
        return self.digest == b"u"

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


def fmt_file_hash_diff(old_hash: FileHash, new_hash: FileHash) -> str | None:
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
        changes.append(
            f"digest {fmt_short_digest(old_hash.digest)} ➜ {fmt_short_digest(new_hash.digest)}"
        )
    if old_hash.size != new_hash.size:
        changes.append(f"size {old_hash.size} ➜ {new_hash.size}")
    if old_hash.mode != new_hash.mode:
        changes.append(f"mode {stat.filemode(old_hash.mode)} ➜ {stat.filemode(new_hash.mode)}")
    return f"({', '.join(changes)})" if len(changes) > 0 else None


#
# Step hashes
#


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


# Frozen because a step hash is a value object: `with_out_hashes` returns a new instance.
# `unsafe_hash=False` keeps instances unhashable,
# because a compact hash would otherwise be hashable while an explained one is not.
@attrs.define(frozen=True, unsafe_hash=False)
class StepHash:
    """A hash used to detect whether a step can be skipped.

    The input digest covers everything a step depends on:
    its label, whether it is a shell command,
    its input files and the environment variables and overrides it uses.
    The output digest covers the files the step has created.

    A step hash is either compact or explained.
    An explained hash also keeps the ingredients of both digests (`InpInfo` and `OutInfo`),
    so that a difference between two hashes can be described in detail.
    """

    inp_digest: bytes = attrs.field()
    """The digest of the step's inputs."""

    inp_info: InpInfo | None = attrs.field(default=None)
    """The ingredients of `inp_digest`, or `None` for a compact hash."""

    out_digest: bytes | None = attrs.field(default=None)
    """The digest of the step's outputs, or `None` when they have not been hashed yet."""

    out_info: OutInfo | None = attrs.field(default=None)
    """The ingredients of `out_digest`, or `None` for a compact or output-less hash."""

    @classmethod
    def from_inp(
        cls,
        step_label: str,
        inp_hashes: Mapping[str, FileHash],
        env_values: Mapping[str, str | None],
        *,
        explained: bool,
        shell: bool = False,
        env_overrides: Mapping[str, str] | None = None,
    ):
        """Create a new step hash with input information only.

        Parameters
        ----------
        step_label
            The label of the step, which distinguishes it from every other step.
        inp_hashes
            The hashes of the step's input files, keyed by path.
        env_values
            The values of the environment variables the step depends on,
            keyed by name, with `None` for a variable that is not defined.
        explained
            Whether to keep the ingredients of the digest in an `InpInfo`.
        shell
            Whether the step runs its command through a shell.
        env_overrides
            The environment variables the step sets for its own command, keyed by name.

        Returns
        -------
        step_hash
            A step hash without output information.
            The environment variables are sorted here, so the digest does not depend on
            the order in which the caller happened to collect them.
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
        inp_info = (
            InpInfo(dict(inp_hashes), dict(env_values), dict(env_overrides)) if explained else None
        )
        return cls(hw.digest(), inp_info)

    def with_out_hashes(self, out_hashes: Mapping[str, FileHash]) -> Self:
        """Return a copy of this step hash with the output information added or replaced.

        Parameters
        ----------
        out_hashes
            The hashes of the step's output files, keyed by path.

        Returns
        -------
        step_hash
            A step hash with the same input information and a new output digest.
            It is explained if and only if `self` is.
        """
        hw = HashWords()
        _update_file_hashes(hw, out_hashes)
        out_info = OutInfo(dict(out_hashes)) if self.inp_info is not None else None
        return self.__class__(self.inp_digest, self.inp_info, hw.digest(), out_info)

    def to_json(self) -> str:
        """Serialize to the JSON representation stored in `step.hash`."""
        return json.dumps(json_converter.unstructure(self))

    @classmethod
    def from_json(cls, value: str | None) -> Self | None:
        """Deserialize from the JSON representation stored in `step.hash`."""
        if value is None:
            return None
        return json_converter.structure(json.loads(value), cls)


#
# Comparison of step hashes
#


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
        Input and output ingredients are only detailed when both hashes are explained.
        Files and environment variables are listed in sorted order.
    """
    changed_lines = []
    same_lines = []
    for is_changed, descr, content in _iter_comparisons(old_hash, new_hash):
        lines = changed_lines if is_changed else same_lines
        lines.append(f"{descr:20s} {content}")
    return "\n".join(changed_lines), "\n".join(same_lines)


def _iter_comparisons(old_hash: StepHash, new_hash: StepHash) -> Iterator[tuple[bool, str, str]]:
    """Iterate over the ingredient comparisons of two step hashes.

    Yields
    ------
    is_changed, descr, content
        Whether the ingredient differs between the two hashes,
        a short description of the ingredient and the comparison itself.
    """
    yield _compare_step_digests(old_hash, new_hash)
    old_inp, new_inp = old_hash.inp_info, new_hash.inp_info
    if old_inp is not None and new_inp is not None:
        yield from _compare_file_hashes("inp", old_inp.inp_hashes, new_inp.inp_hashes)
        yield from _compare_env_values("env var", old_inp.env_values, new_inp.env_values)
        yield from _compare_env_values("env override", old_inp.env_overrides, new_inp.env_overrides)
    old_out, new_out = old_hash.out_info, new_hash.out_info
    if old_out is not None and new_out is not None:
        yield from _compare_file_hashes("out", old_out.out_hashes, new_out.out_hashes)


def _compare_step_digests(old_hash: StepHash, new_hash: StepHash) -> tuple[bool, str, str]:
    """Compare the detail level and both digests of two step hashes."""
    old_level = _fmt_detail_level(old_hash)
    new_level = _fmt_detail_level(new_hash)
    parts = [old_level if old_level == new_level else f"{old_level} ➜ {new_level}"]
    is_changed = False
    for label, old_digest, new_digest in [
        ("inp_digest", old_hash.inp_digest, new_hash.inp_digest),
        ("out_digest", old_hash.out_digest, new_hash.out_digest),
    ]:
        if old_digest == new_digest:
            parts.append(f"{label} {fmt_short_digest(old_digest)}")
        else:
            is_changed = True
            parts.append(f"{label} {fmt_short_digest(old_digest)} ➜ {fmt_short_digest(new_digest)}")
    descr = "Modified step hash" if is_changed else "Same step hash"
    return is_changed, descr, ", ".join(parts)


def _fmt_detail_level(step_hash: StepHash) -> str:
    """Name the detail level of a step hash, as used in the comparison reports."""
    return "compact" if step_hash.inp_info is None else "explained"


def _compare_file_hashes(
    label: str, old_hashes: Mapping[str, FileHash], new_hashes: Mapping[str, FileHash]
) -> Iterator[tuple[bool, str, str]]:
    """Compare two mappings of file hashes, in sorted path order."""
    for path in sorted(set(old_hashes) | set(new_hashes)):
        if path not in old_hashes:
            yield True, f"Added {label} hash", path
        elif path not in new_hashes:
            yield True, f"Deleted {label} hash", path
        else:
            diff = fmt_file_hash_diff(old_hashes[path], new_hashes[path])
            if diff is None:
                yield False, f"Same {label} hash", path
            else:
                yield True, f"Modified {label} hash", f"{path} {diff}"


def _compare_env_values(
    label: str, old_env: Mapping[str, str | None], new_env: Mapping[str, str | None]
) -> Iterator[tuple[bool, str, str]]:
    """Compare two mappings of environment variable values, in sorted name order."""
    for name in sorted(set(old_env) | set(new_env)):
        if name not in old_env:
            yield True, f"Added {label}", f"{name} {fmt_env_value(new_env[name])}"
        elif name not in new_env:
            yield True, f"Deleted {label}", f"{name} {fmt_env_value(old_env[name])}"
        elif old_env[name] == new_env[name]:
            yield False, f"Same {label}", f"{name} {fmt_env_value(old_env[name])}"
        else:
            old_var = fmt_env_value(old_env[name])
            new_var = fmt_env_value(new_env[name])
            yield True, f"Modified {label}", f"{name} {old_var} ➜ {new_var}"


#
# Pure functions for threaded hash computation
#


@attrs.define
class HashComputeResult:
    """The result of a hash computation."""

    messages: list[str] = attrs.field()
    """What the caller must report about the files, one line per file.

    The wording depends on the function that produced the result:
    `compute_inp_hashes` writes a full sentence per unexpectedly changed or vanished input,
    while `compute_out_hashes` writes the bare path of each output that is missing.
    """

    all_hashes: dict[str, FileHash] = attrs.field()
    """The new hashes of all files that were hashed, keyed by path."""


@attrs.define
class InpHashComputeResult(HashComputeResult):
    """The result of an input hash computation, which also singles out the changed inputs."""

    new_hashes: dict[str, FileHash] = attrs.field()
    """The new hashes of the inputs that changed while they should not have, keyed by path."""


def compute_inp_hashes(
    inp_hashes: Mapping[str, FileHash], cancel_event: threading.Event
) -> InpHashComputeResult:
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
    ConsistencyError
        When an input was already missing before the step was scheduled.
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
        new_file_hash = old_file_hash.refreshed(path, cancel_event)
        all_inp_hashes[path] = new_file_hash
        if new_file_hash != old_file_hash:
            new_inp_hashes[path] = new_file_hash
            # If an input hash has changed,
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

    return InpHashComputeResult(messages, all_inp_hashes, new_inp_hashes)


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
    all_out_hashes = {}
    for path in sorted(out_hashes):
        new_file_hash = out_hashes[path].refreshed(path, cancel_event)
        all_out_hashes[path] = new_file_hash
        # Missing files are always reported, even if they are missing again (unchanged hash).
        if new_file_hash.is_unknown:
            messages.append(path)

    return HashComputeResult(messages, all_out_hashes)


def compute_both_hashes(
    inp_hashes: Mapping[str, FileHash],
    out_hashes: Mapping[str, FileHash],
    cancel_event: threading.Event,
) -> tuple[InpHashComputeResult, HashComputeResult]:
    """Call `compute_inp_hashes` and `compute_out_hashes`, in that order.

    A `ThreadWorker` runs a single callable,
    so a step that needs both results must ask for them in one call.
    The parameters, the results and the exceptions are those of the two functions it calls.
    """
    return (
        compute_inp_hashes(inp_hashes, cancel_event),
        compute_out_hashes(out_hashes, cancel_event),
    )
