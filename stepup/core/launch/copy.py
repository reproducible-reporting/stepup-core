# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Copy a file in the director for the command `cp -p SRC DST`, without starting a process."""

import contextlib
import errno
import functools
import io
import logging
import os
import stat
import sys
import threading
import time

import attrs

from ..exceptions import WorkAbandonedError
from ..graph.outcome import ChildOutcome, ResourceUsage
from ..path import Path
from ..utils import open_regular_file
from .worker import Run, ThreadWorker

if sys.platform != "win32":
    # Windows has no `fcntl`, nor the cloning of files that needs it.
    import fcntl

__all__ = (
    "is_builtin_copy",
    "run_builtin_copy",
)

logger = logging.getLogger(__name__)


#
# Built-in copy
#


COPY_CHUNK_SIZE = 1 << 18
"""The number of bytes copied between two checks of the cancel event."""


def is_builtin_copy(parts: list[str]) -> bool:
    """Whether the words of a command are exactly `cp -p SRC DST`, without further options."""
    return (
        len(parts) == 4
        and parts[:2] == ["cp", "-p"]
        and not any(part.startswith("-") for part in parts[2:])
    )


def _copy_file(src: Path, dst: Path, cancel_event: threading.Event) -> bool:
    """Copy a file together with its permission bits, timestamps and owner, as `cp -p` does.

    Like `cp`, this follows symbolic links, copies into `dst` when that is a directory,
    overwrites an existing destination in place,
    and silently keeps the owner of the copy when it may not be changed.
    Like GNU `cp` 9.0 and later, the copy shares its data with the source where the file system can,
    and it keeps the holes of a sparse source.
    Unlike `cp`, it refuses a source or a destination that is not a regular file,
    such as a FIFO or a device, instead of reading or writing its stream.
    Unlike `shutil.copystat`, it leaves extended attributes alone,
    which include the security context of SELinux.

    Parameters
    ----------
    src
        The file to copy.
    dst
        The destination file, or an existing directory in which the copy gets the name of `src`.
    cancel_event
        The event that is checked between chunks of `COPY_CHUNK_SIZE` bytes.

    Returns
    -------
    completed
        `False` when `cancel_event` was set before the whole file was copied,
        which leaves an incomplete destination behind.

    Raises
    ------
    NotARegularFileError
        When `src` or `dst` is not a regular file, which is refused without waiting for it.
    OSError
        When the file cannot be copied.
    """
    if src.is_dir():
        raise IsADirectoryError(errno.EISDIR, "The source is a directory", str(src))
    if dst.is_dir():
        dst = dst / src.name
    with open(src, "rb", buffering=0, opener=open_regular_file) as fsrc:
        src_stat = os.fstat(fsrc.fileno())
        # Opening the destination truncates it, which would destroy the source.
        if dst.exists() and dst.samefile(src):
            raise OSError(
                errno.EINVAL, "The source and the destination are the same file", str(src)
            )
        # A new destination is private until it gets the permission bits of the source.
        # The truncation by `"wb"` happens before the opener checks the kind of file,
        # which is harmless, because it has no effect on a FIFO or a device.
        opener = functools.partial(open_regular_file, mode=stat.S_IRUSR | stat.S_IWUSR)
        # The destination has no buffer, which `os.copy_file_range` would bypass.
        with open(dst, "wb", buffering=0, opener=opener) as fdst:
            if not _copy_data(fsrc, fdst, cancel_event):
                return False
    os.utime(dst, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    if hasattr(os, "chown"):
        # Changing the owner takes privileges, changing the group only membership of it.
        try:
            os.chown(dst, src_stat.st_uid, src_stat.st_gid)
        except OSError:
            with contextlib.suppress(OSError):
                os.chown(dst, -1, src_stat.st_gid)
    # The permission bits come last, because changing the owner may clear some of them.
    os.chmod(dst, stat.S_IMODE(src_stat.st_mode))
    return True


def _copy_data(fsrc: io.FileIO, fdst: io.FileIO, cancel_event: threading.Event) -> bool:
    """Copy the content of a file into an empty one, sharing the data and keeping holes if possible.

    The whole file is cloned when the file system can, as `cp --reflink=auto` tries first.
    Otherwise, the data are copied in chunks by `_RangeCopier`,
    and only the data segments of a source that may be sparse, so that its holes stay holes.

    Parameters
    ----------
    fsrc
        The source, opened for reading without a buffer.
    fdst
        The empty destination, opened for writing without a buffer.
    cancel_event
        The event that is checked before the copy starts and between chunks.

    Returns
    -------
    completed
        `False` when `cancel_event` was set before all data were copied.
    """
    if cancel_event.is_set():
        return False
    if _clone_file(fsrc, fdst):
        return True
    copier = _RangeCopier(fsrc, fdst)
    if not _may_be_sparse(os.fstat(fsrc.fileno())):
        return copier.copy(0, None, cancel_event)
    pos = 0
    while (segment := _next_data_segment(fsrc, pos)) is not None:
        start, end = segment
        if not copier.copy(start, end, cancel_event):
            return False
        if end is None:
            break
        pos = end
    # A hole at the end of the source has no data that would extend the destination.
    os.ftruncate(fdst.fileno(), os.fstat(fsrc.fileno()).st_size)
    return True


def _clone_file(fsrc: io.FileIO, fdst: io.FileIO) -> bool:
    """Let an empty file share all data of another one with the `FICLONE` ioctl of Linux.

    This works on Btrfs, XFS and bcachefs, and on NFS 4.2 when the server can clone.
    It takes time per extent, not per byte, so it needs no cancel event.
    Python 3.11 lacks `fcntl.FICLONE`, and then no file is cloned.

    Parameters
    ----------
    fsrc
        The source, opened for reading.
    fdst
        The empty destination, opened for writing.

    Returns
    -------
    cloned
        `False` when the platform or the file system cannot clone the file,
        in which case the destination is still empty.
    """
    if sys.platform == "win32" or not hasattr(fcntl, "FICLONE"):
        return False
    try:
        fcntl.ioctl(fdst.fileno(), fcntl.FICLONE, fsrc.fileno())
    except OSError:
        # A clone that failed half way may have left data behind.
        os.ftruncate(fdst.fileno(), 0)
        return False
    return True


def _may_be_sparse(st: os.stat_result) -> bool:
    """Whether a file allocates fewer blocks than its size needs, which GNU `cp` tests as well.

    It is `False` where `os.SEEK_DATA` does not exist, which is needed to find the holes.
    A file that reports a size of 0, such as one in `/proc`, is not sparse by this test.
    """
    return hasattr(os, "SEEK_DATA") and st.st_blocks < st.st_size // 512


def _next_data_segment(fsrc: io.FileIO, pos: int) -> tuple[int, int | None] | None:
    """Find the first range of data in a file at or after a position.

    Parameters
    ----------
    fsrc
        The file to search, whose position is changed.
    pos
        The position where the search starts.

    Returns
    -------
    segment
        The start and the end of the data, where the end is `None` when it is not known,
        or `None` when no data are left.
    """
    fd = fsrc.fileno()
    try:
        start = os.lseek(fd, pos, os.SEEK_DATA)
    except OSError as exc:
        if exc.errno == errno.ENXIO:
            return None
        # The file system cannot tell where the data are, so all the rest counts as data.
        return pos, None
    try:
        return start, os.lseek(fd, start, os.SEEK_HOLE)
    except OSError:
        return start, None


@attrs.define
class _RangeCopier:
    """Copy ranges of bytes of a file to the same positions in another one, in chunks.

    The chunks are copied with `os.copy_file_range` where it exists,
    which keeps the data in the kernel and lets the file system share them.
    On a network file system, such as NFS 4.2 or SMB3, the server then copies the data.
    When the first call of `os.copy_file_range` fails or copies nothing,
    the chunks are read and written instead.
    A full disk, or a failure of a later call, is an error.
    """

    fsrc: io.FileIO = attrs.field()
    """The source, opened for reading without a buffer."""

    fdst: io.FileIO = attrs.field()
    """The destination, opened for writing without a buffer."""

    # Internal state

    _in_kernel: bool = attrs.field(init=False, factory=lambda: hasattr(os, "copy_file_range"))
    """Whether the next chunk is copied with `os.copy_file_range`."""

    _first: bool = attrs.field(init=False, default=True)
    """Whether no chunk was copied with `os.copy_file_range` yet."""

    _buf: memoryview | None = attrs.field(init=False, default=None)
    """The buffer for reading and writing a chunk, allocated when it is first needed."""

    def copy(self, start: int, end: int | None, cancel_event: threading.Event) -> bool:
        """Copy the bytes from `start` up to `end`, and check the cancel event after every chunk.

        Parameters
        ----------
        start
            The position of the first byte to copy.
        end
            The position after the last byte to copy,
            or `None` to copy up to the end of the source.
            The copy stops earlier when the source ends earlier, e.g. because it shrank.
        cancel_event
            The event that stops the copy when it is set.

        Returns
        -------
        completed
            `False` when `cancel_event` was set before the whole range was copied.

        Raises
        ------
        OSError
            When a chunk cannot be copied.
        """
        pos = start
        while end is None or pos < end:
            size = COPY_CHUNK_SIZE if end is None else min(COPY_CHUNK_SIZE, end - pos)
            ncopied = self._copy_chunk(pos, size)
            # As in `compute_file_digest`, only 0 bytes mean the end of the file.
            if ncopied == 0:
                break
            pos += ncopied
            if cancel_event.is_set():
                return False
        return True

    def _copy_chunk(self, pos: int, size: int) -> int:
        """Copy at most `size` bytes from position `pos`, and return the number of bytes copied."""
        if self._in_kernel:
            first, self._first = self._first, False
            try:
                ncopied = os.copy_file_range(self.fsrc.fileno(), self.fdst.fileno(), size, pos, pos)
            except OSError as exc:
                # As in `shutil`, only the first call tells whether the kernel can copy the data,
                # and a full disk is an error, whichever way the data are copied.
                if not first or exc.errno == errno.ENOSPC:
                    raise
            else:
                # A first call that copies nothing may have failed without saying so,
                # e.g. for a file that reports a size of 0.
                if ncopied > 0 or not first:
                    return ncopied
            self._in_kernel = False
        return self._read_write(pos, size)

    def _read_write(self, pos: int, size: int) -> int:
        """Copy at most `size` bytes from position `pos` with a buffer, and return their number."""
        if self._buf is None:
            self._buf = memoryview(bytearray(COPY_CHUNK_SIZE))
        # Seeking works on every platform, while Windows lacks `os.pread` and `os.pwrite`.
        self.fsrc.seek(pos)
        nread = self.fsrc.readinto(self._buf[:size])
        self.fdst.seek(pos)
        nwritten = 0
        # A write may take only part of the data.
        while nwritten < nread:
            nwritten += self.fdst.write(self._buf[nwritten:nread])
        return nread


def _builtin_copy(src: Path, dst: Path, cancel_event: threading.Event) -> ChildOutcome:
    """Copy a file as `_copy_file` does, and describe the result as that of a command.

    The CPU time of the copy is part of that of the director, and is not reported here.
    """
    wtime_start = time.perf_counter()
    try:
        completed = _copy_file(src, dst, cancel_event)
    except (OSError, ValueError) as exc:
        # A path that no file system function accepts raises `ValueError`.
        returncode, stderr = 1, f"Failed to copy {src} to {dst}: {exc}\n"
    else:
        returncode, stderr = (0, "") if completed else (1, f"Interrupted the copy to {dst}.\n")
    usage = ResourceUsage(wtime=time.perf_counter() - wtime_start)
    return ChildOutcome(returncode, "", stderr, usage)


async def run_builtin_copy(src: str, dst: str, cwd: Path, run: Run) -> ChildOutcome:
    """Copy a file in a thread of the director, relative to the working directory of the step."""
    worker = ThreadWorker(
        work=functools.partial(_builtin_copy, cwd / src, cwd / dst), job_i=run.job_i
    )
    run.worker = worker
    try:
        return await worker.run_in_thread()
    except WorkAbandonedError:
        stderr = f"Abandoned the copy to {cwd / dst}, which did not stop when asked.\n"
        return ChildOutcome(1, "", stderr)
    finally:
        run.worker = None
