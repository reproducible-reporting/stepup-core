# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Hash-job queue and dedup registry.

`HashQueue` decouples "a file needs to be (re)hashed" from
"something is currently draining runnable work".
`Builder.job_loop` drains it with priority over the SQL-poll job path,
since a hash job's runnability never depends on the workflow database.
`gather_hashes()` is the direct-drain counterpart used outside a build phase
(startup, watch phase), when `job_loop` is not running to pump the queue.
"""

import asyncio
from collections.abc import Collection, Iterator
from typing import TYPE_CHECKING

import attrs

from .enums import HashUpdateCause
from .hash import FileHash
from .reporter import PROGRESS_REFRESH_DELAY, ReporterClient
from .run import ThreadWorker

if TYPE_CHECKING:
    # Avoid a runtime import cycle: executor.py imports HashJob from this module.
    from .executor import Executor

__all__ = ("HashJob", "HashQueue", "gather_hashes")


@attrs.define(eq=False)
class HashJob:
    """A single-file hash computation, queued or in flight.

    Instances use identity-based equality and hashing, like `Run`,
    since two jobs for the same path are still distinct attempts
    (dedup is handled by `HashQueue.in_flight`, keyed by path, not by `HashJob` equality).
    """

    path: str = attrs.field()
    """The director-relative path of the file to hash, same form as node labels."""

    old_hash: FileHash = attrs.field()
    """The previously known hash, or `FileHash.unknown()` if there was none."""

    cause: HashUpdateCause = attrs.field()
    """Why the hash is being (re)computed, which decides how the result is applied.

    See `Workflow.update_file_hashes`.
    """

    job_i: int = attrs.field()
    """Unique id of this job, from `HashQueue`'s own counter.

    Disjoint from `Scheduler.job_counter`'s ids: hash jobs also run between build phases,
    when `Scheduler.job_counter` has been reset, so reusing that counter could collide with
    a live entry in `Executor.running`. Only used for tracking/logging, never for RPC
    resolution.
    """

    future: asyncio.Future[FileHash] = attrs.field(init=False)
    """Resolved with the new hash on completion, or cancelled/failed otherwise.

    Created eagerly on the running loop, so `HashQueue.submit()` can hand out a future
    before the job has even been queued.
    """

    @future.default
    def _default_future(self) -> asyncio.Future[FileHash]:
        return asyncio.get_running_loop().create_future()

    started: bool = attrs.field(init=False, default=False)
    """Set once a runner has claimed this job, through `HashQueue.claim()`."""

    worker: ThreadWorker | None = attrs.field(init=False, default=None)
    """The in-flight hashing thread, if any, for `Executor.interrupt()`."""


@attrs.define
class HashQueue:
    """Pending hash jobs plus a path-keyed dedup registry.

    Owned by nothing in particular: a single instance is created in the composition root
    (`director.py:serve()`) and shared by every component that needs to submit or drain
    hash jobs, keeping `Scheduler` and `Builder` themselves free of this bookkeeping.
    """

    wake: asyncio.Event = attrs.field()
    """Set whenever a job is submitted, so a parked `Builder.job_loop` wakes up.

    This is `Builder.wake_job_loop`, injected at construction time.
    """

    queue: asyncio.Queue[HashJob] = attrs.field(init=False, factory=asyncio.Queue)
    """Jobs not yet popped by a consumer, in submission order."""

    in_flight: dict[str, HashJob] = attrs.field(init=False, factory=dict)
    """path -> `HashJob`, covering both queued and started (but unresolved) jobs.

    An entry is removed as soon as its job's future resolves
    (success, exception, or cancellation), via `future.add_done_callback`,
    which fires in all three cases and therefore cannot leak.
    """

    _job_counter: int = attrs.field(init=False, default=0)
    """Counter for `HashJob.job_i`.

    Decremented so hash-job ids stay negative,
    disjoint from `Scheduler.job_counter`'s (positive) ids.
    """

    def submit(self, path: str, old_hash: FileHash, cause: HashUpdateCause) -> HashJob:
        """Enqueue a hash job for `path`, or return its already in-flight job.

        Parameters
        ----------
        path
            The director-relative path of the file to hash.
        old_hash
            The previously known hash, used as the baseline for change detection.
            Ignored if a job for `path` is already in flight.
        cause
            Why the hash is being (re)computed.
            Ignored if a job for `path` is already in flight:
            by construction, concurrent submitters for the same path
            want the same confirmation.

        Returns
        -------
        job
            The new or already in-flight `HashJob` for `path`.
        """
        job = self.in_flight.get(path)
        if job is not None:
            return job
        self._job_counter -= 1
        job = HashJob(path, old_hash, cause, self._job_counter)
        self.in_flight[path] = job
        job.future.add_done_callback(lambda _future, path=path: self.in_flight.pop(path, None))
        self.queue.put_nowait(job)
        self.wake.set()
        return job

    def claim(self, job: HashJob) -> bool:
        """Atomically flip `job.started` from `False` to `True`.

        Lets a promoted, direct runner and the regular queue consumer (`pop_nowait()`) race safely:
        the loser just skips the job.
        Single-threaded asyncio makes a plain check-and-set atomic enough, no lock needed.

        Returns
        -------
        won
            Whether the caller is the one that gets to run `job`.
        """
        if job.started:
            return False
        job.started = True
        return True

    def _drain_claimed(self) -> Iterator[HashJob]:
        """Pop all jobs that have already been claimed by a promoted, direct runner.

        This is a convenience for `Builder.job_loop`, which only wants to run unclaimed jobs.
        """
        while True:
            try:
                job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if not self.claim(job):
                continue
            yield job

    def pop_nowait(self) -> HashJob | None:
        """Pop the next unclaimed job from the queue.

        Jobs already claimed by a promoted, direct runner are discarded silently
        (they are being run outside the queue) instead of being returned again.

        Returns
        -------
        job
            The next unclaimed `HashJob`, or `None` if the queue holds
            no (more) unclaimed jobs.
        """
        return next(self._drain_claimed(), None)

    def shutdown(self) -> None:
        """Cancel the futures of all not-yet-started jobs, and drain the queue.

        Prevents awaiters of a queued-but-never-started job from hanging forever.
        Already-started jobs are left alone: their cancellation is handled by
        `Executor.interrupt()`, through the `HashJob.worker` they registered.
        """
        for job in self._drain_claimed():
            job.future.cancel()


async def gather_hashes(
    hash_queue: HashQueue,
    executor: "Executor",
    reporter: ReporterClient,
    path_hash_causes: Collection[tuple[str, FileHash, HashUpdateCause]],
    njob: int,
) -> dict[str, FileHash]:
    """Submit hash jobs for `path_hash_causes` and run them with bounded concurrency.

    Used by the startup scan and the watcher to drain a batch of hash jobs directly,
    independent of `Builder.job_loop`, which is not running during those phases
    (build, startup and watch phases are mutually exclusive, see `tmp/overview.md`).
    A single call may mix jobs with different causes
    (e.g. the startup scan's `CONFIRMED` and `EXTERNAL` batches),
    which lets them interleave under one shared `njob` budget
    instead of running one cause's batch to completion before the next starts.

    Parameters
    ----------
    hash_queue
        Where jobs are submitted, for dedup with any other in-flight submitter.
    executor
        Runs each claimed job in a thread.
    reporter
        Where live progress is sent: `start_job`/`stop_job` around each claimed job,
        and `update_counts`, coalesced to at most once per `PROGRESS_REFRESH_DELAY`.
    path_hash_causes
        `(path, old_hash, cause)` triples to (re)hash;
        see `HashJob.old_hash` and `HashJob.cause`.
    njob
        Maximum number of jobs this call runs concurrently. Jobs already claimed by
        another submitter (e.g. a duplicate path within `path_hash_causes`, or, once amend()
        promotion lands, a blocked RPC racing this drain) are not run here, and therefore
        do not count against this budget; only their shared future is awaited.

    Returns
    -------
    path_hashes
        The new hash of every path in `path_hash_causes`, keyed by path, in input order.
        (The input stays a sequence of triples, since a single call may carry several causes.)
        An exception
        raised by one job (e.g. a stat error) propagates from `gather()` without cancelling
        the other jobs already running in the background; that mirrors today's behavior,
        where an unhandled `regen()` error already crashes the caller (startup or watcher).
    """
    sem = asyncio.Semaphore(njob)
    ntotal = len(path_hash_causes)
    nsuccess = 0
    counts_flush_handle: asyncio.TimerHandle | None = None
    counts_flush_tasks: set[asyncio.Task] = set()

    def request_counts_flush() -> None:
        """Schedule an `update_counts` report, coalescing with any already pending.

        Mirrors `ReporterClient._request_jobs_flush`'s coalescing timer:
        unlike `start_job`/`stop_job`, `update_counts` has no built-in throttling.
        """
        nonlocal counts_flush_handle
        if counts_flush_handle is None:
            loop = asyncio.get_running_loop()
            counts_flush_handle = loop.call_later(PROGRESS_REFRESH_DELAY, on_counts_timer)

    def on_counts_timer() -> None:
        nonlocal counts_flush_handle
        counts_flush_handle = None
        task = asyncio.get_running_loop().create_task(reporter.update_counts(nsuccess, ntotal))
        counts_flush_tasks.add(task)
        task.add_done_callback(counts_flush_tasks.discard)

    async def run_one(job: HashJob) -> FileHash:
        nonlocal nsuccess
        if hash_queue.claim(job):
            async with sem:
                reporter.start_job("H", job.path, job.job_i)
                try:
                    await executor.run_hash_job(job)
                finally:
                    reporter.stop_job(job.job_i)
        new_hash = await asyncio.shield(job.future)
        nsuccess += 1
        request_counts_flush()
        return new_hash

    jobs = [hash_queue.submit(path, old_hash, cause) for path, old_hash, cause in path_hash_causes]
    new_hashes = await asyncio.gather(*(run_one(job) for job in jobs))

    if counts_flush_handle is not None:
        counts_flush_handle.cancel()
        counts_flush_handle = None
    if len(counts_flush_tasks) > 0:
        await asyncio.gather(*counts_flush_tasks)
    await reporter.update_counts(nsuccess, ntotal)

    return {job.path: new_hash for job, new_hash in zip(jobs, new_hashes, strict=True)}
