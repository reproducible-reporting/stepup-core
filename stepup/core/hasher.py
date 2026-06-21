# StepUp Core provides the basic framework for the StepUp build tool.
# Copyright 2024-2026 Toon Verstraelen
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
"""Step-hash computation, offloaded to a separate process.

File hashing is the only CPU- and IO-bound work the director needs to perform while running steps.
To keep the director's single event loop responsive, it is delegated to a forkserver child
(fork mode) or a `_stepup_hasher` subprocess (no-fork mode). Both call `compute_step_hash`,
which is a pure function: it takes a `HashTask` describing the files and environment variables
to hash and returns a `HashResult`.

The caller applies the result to its own step state.
"""

import pickle
import sys

import attrs

from .hash import FileHash, StepHash, fmt_file_hash_diff

__all__ = ("HashResult", "HashTask", "compute_step_hash", "hash_fork_entry", "hasher_tool")


@attrs.define
class HashTask:
    """A request to (re)compute (part of) a step hash."""

    mode: str = attrs.field()
    """Which part of the hash to compute: `"inp"`, `"out"` or `"full"`."""

    step_key: str = attrs.field(default="")
    """The step description, used as the leading ingredient of the input digest."""

    extended: bool = attrs.field(default=False)
    """Whether to keep the detailed ingredients (for `--explain-rerun`)."""

    subshell: bool = attrs.field(default=False)
    """Whether the step command is executed via a subshell."""

    check_hash: bool = attrs.field(default=True)
    """If `True`, unexpected changes in input files are reported as errors."""

    inp_hashes: list[tuple[str, FileHash]] = attrs.field(factory=list)
    """The input paths and their previously known file hashes."""

    env_var_values: list[tuple[str, str | None]] = attrs.field(factory=list)
    """The (already resolved) environment variable names and values used by the step."""

    out_hashes: list[tuple[str, FileHash]] = attrs.field(factory=list)
    """The output paths and their previously known file hashes."""

    step_hash: StepHash | None = attrs.field(default=None)
    """An existing step hash to evolve with output information (for `"out"` mode)."""


@attrs.define
class HashResult:
    """The outcome of a `HashTask`, applied by the caller to its step state."""

    step_hash: StepHash | None = attrs.field(default=None)
    """The (re)computed step hash, or `None` if inputs changed/vanished unexpectedly."""

    new_inp_hashes: list[tuple[str, FileHash]] = attrs.field(factory=list)
    """The *changed* input paths and their new file hashes."""

    new_out_hashes: list[tuple[str, FileHash]] = attrs.field(factory=list)
    """The *changed* output paths and their new file hashes."""

    inp_messages: list[str] = attrs.field(factory=list)
    """Validation messages for unexpectedly changed or vanished inputs."""

    out_missing: list[str] = attrs.field(factory=list)
    """Expected output paths that were not created."""

    inp_digest: bytes = attrs.field(default=b"")
    """The input digest, exposed to the step as `STEPUP_STEP_INP_DIGEST`."""

    success: bool = attrs.field(default=True)
    """`False` if inputs changed unexpectedly or outputs are missing."""


def _compute_inp(task: HashTask, result: HashResult) -> StepHash | None:
    """Compute the input part of the step hash, updating `result` in place."""
    messages = []
    all_inp_hashes = []
    for path, old_file_hash in task.inp_hashes:
        new_file_hash = old_file_hash.regen(path)
        all_inp_hashes.append((path, new_file_hash))
        if task.check_hash and new_file_hash != old_file_hash:
            if new_file_hash.is_unknown:
                messages.append(f"Input vanished unexpectedly: {path} ")
            else:
                messages.append(
                    f"Input changed unexpectedly: {path} "
                    + fmt_file_hash_diff(old_file_hash, new_file_hash)
                )
            result.new_inp_hashes.append((path, new_file_hash))

    # If there are unexpected issues with inputs, bail out.
    if len(messages) > 0:
        result.inp_messages = messages
        result.success = False
        return None

    step_hash = StepHash.from_inp(
        task.step_key,
        task.extended,
        all_inp_hashes,
        task.env_var_values,
        task.subshell,
    )
    result.inp_digest = step_hash.inp_digest
    return step_hash


def _compute_out(task: HashTask, result: HashResult, step_hash: StepHash | None) -> StepHash | None:
    """Compute the output part of the step hash, updating `result` in place."""
    all_out_hashes = []
    for path, old_file_hash in sorted(task.out_hashes):
        new_file_hash = old_file_hash.regen(path)
        all_out_hashes.append((path, new_file_hash))
        if new_file_hash != old_file_hash:
            result.new_out_hashes.append((path, new_file_hash))
        if new_file_hash.is_unknown:
            result.out_missing.append(path)
            result.success = False
    if step_hash is not None:
        step_hash = step_hash.evolve_out(all_out_hashes)
    return step_hash


def compute_step_hash(task: HashTask) -> HashResult:
    """Compute (part of) a step hash for the given task.

    Parameters
    ----------
    task
        The hashing request, see `HashTask`.

    Returns
    -------
    result
        The computed hash and associated metadata, see `HashResult`.
    """
    result = HashResult()
    if task.mode == "inp":
        result.step_hash = _compute_inp(task, result)
    elif task.mode == "out":
        result.step_hash = _compute_out(task, result, task.step_hash)
    elif task.mode == "full":
        step_hash = _compute_inp(task, result)
        result.step_hash = _compute_out(task, result, step_hash)
    else:
        raise ValueError(f"Unknown hash task mode: {task.mode!r}")
    return result


def hash_fork_entry(task: HashTask, result_conn) -> None:
    """Entry point for forkserver-launched hashing (fork mode).

    Runs in a forked child process, computes the hash and sends the `HashResult`
    back to the parent through `result_conn`.
    """
    try:
        result = compute_step_hash(task)
    except BaseException as exc:  # noqa: BLE001
        result_conn.send(exc)
    else:
        result_conn.send(result)


def hasher_tool() -> None:
    """Entry point for the `_stepup_hasher` tool (no-fork mode).

    Reads a pickled `HashTask` from stdin and writes a pickled `HashResult` to stdout.
    """
    task = pickle.load(sys.stdin.buffer)
    result = compute_step_hash(task)
    sys.stdout.buffer.write(pickle.dumps(result))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    hasher_tool()
