# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Hash-job queue and dedup registry.

`HashQueue` decouples "a file needs to be (re)hashed" from
"something is currently draining runnable work".
`Builder.job_loop` drains it with priority over the SQL-poll job path,
since a hash job's runnability never depends on the workflow database.
`gather_hashes()` is the direct-drain counterpart used outside a build phase
(startup and watch phases), when `job_loop` is not running to pump the queue.

A hash job computes a hash and resolves its future.
A job carries no reason of its own, because it only reports what is on disk:
every result is applied as a `HashUpdateCause.OBSERVED` update.
Applying the result to the workflow is the responsibility of whoever awaits it,
and an awaiter must apply before it reads file state.
This lets an awaiter that has just awaited a whole batch of futures
write them all in one transaction.

When several awaiters share one job, `HashQueue.claim_apply()` picks the single one that applies.
Only a build phase needs that arbitration: `gather_hashes()` runs when no other awaiter exists,
so its callers apply the batch it returns without claiming anything.
"""

import asyncio
import functools
from collections.abc import Collection, Iterator
from typing import TYPE_CHECKING

import attrs

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

    job_i: int = attrs.field()
    """Unique negative id of this job, from `HashQueue`'s own counter.

    Disjoint from `Scheduler.job_counter`'s ids:
    hash jobs also run between build phases, when `Scheduler.job_counter` has been reset,
    so reusing that counter could collide with a live entry in `Executor.running`.
    Only used for tracking/logging, never for RPC resolution.
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

    applied: bool = attrs.field(init=False, default=False)
    """Set once a writer has claimed this job's result, through `HashQueue.claim_apply()`."""

    worker: ThreadWorker | None = attrs.field(init=False, default=None)
    """The in-flight hashing thread, if any, for `Executor.interrupt()`."""


@attrs.define
class HashQueue:
    """Pending hash jobs plus a path-keyed dedup registry.

    Every hash job means the same thing, namely observe the file on disk,
    so deduplicating by path is unambiguous:
    concurrent submitters for the same path all want the same answer.
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
    (success, exception, or cancellation), via `_job_done`,
    which fires in all three cases and therefore cannot leak.
    """

    _job_counter: int = attrs.field(init=False, default=0)
    """Negative counter for `HashJob.job_i`."""

    def submit(self, path: str, old_hash: FileHash) -> HashJob:
        """Enqueue a hash job for `path`, or return its already in-flight job.

        Parameters
        ----------
        path
            The director-relative path of the file to hash.
        old_hash
            The previously known hash, used as the baseline for change detection.
            Ignored if a job for `path` is already in flight.

        Returns
        -------
        job
            The new or already in-flight `HashJob` for `path`.
        """
        job = self.in_flight.get(path)
        if job is not None:
            return job
        self._job_counter -= 1
        job = HashJob(path, old_hash, self._job_counter)
        self.in_flight[path] = job
        job.future.add_done_callback(functools.partial(self._job_done, path))
        self.queue.put_nowait(job)
        self.wake.set()
        return job

    def _job_done(self, path: str, future: asyncio.Future[FileHash]) -> None:
        """Retire the job for `path` when its future resolves, however it resolved."""
        self.in_flight.pop(path, None)
        if not future.cancelled():
            # Retrieving the exception (if any) only marks it as retrieved:
            # it stays on the future for submitters that do await it
            # (`Builder.run_promoted_hash_jobs`).
            # `Executor.run_hash_job` has already reported it and drained the scheduler,
            # while most submitters are fire-and-forget (`DirectorHandler._submit_to_check`),
            # so without this, asyncio would log "Future exception was never retrieved"
            # for an error that was in fact fully handled.
            future.exception()

    def claim(self, job: HashJob) -> bool:
        """Atomically flip `job.started` from `False` to `True`.

        Lets a promoted, direct runner and the regular queue consumer (`pop_nowait()`)
        race safely: the loser just skips the job.
        Single-threaded asyncio makes a plain check-and-set atomic enough; no lock is needed.

        Returns
        -------
        won
            Whether the caller is the one that gets to run `job`.
        """
        if job.started:
            return False
        job.started = True
        return True

    def claim_apply(self, job: HashJob) -> bool:
        """Atomically flip `job.applied` from `False` to `True`.

        Several awaiters may share one job, while one observation may reach the graph only once.
        A second application does not repeat the first,
        because the state the first one settled on is no longer the state it started from:
        for a static file it is dropped as unchanged,
        while for an output it marks the creator pending all over again.
        Neither is harmful today, so this is a guard rather than a fix,
        which is also why nothing reports a lost claim.

        Call this inside the same database transaction that writes the result.
        A caller that loses the claim then knows the winner's write has already been committed,
        because it could only take the lock after the winner released it.
        Claiming outside that transaction would let a loser read file state
        that the winner has not written yet.

        Returns
        -------
        won
            Whether the caller is the one that gets to apply `job`'s result.
        """
        if job.applied:
            return False
        job.applied = True
        return True

    def _drain_claimed(self) -> Iterator[HashJob]:
        """Pop queued jobs one by one, yielding the ones this call manages to claim."""
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

        Jobs already claimed by a promoted, direct runner are discarded silently,
        since they are being run outside the queue.

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
        Already-started jobs are left alone:
        their cancellation is handled by `Executor.interrupt()`,
        through the `HashJob.worker` they registered.
        """
        for job in self._drain_claimed():
            job.future.cancel()


async def gather_hashes(
    hash_queue: HashQueue,
    executor: "Executor",
    reporter: ReporterClient,
    path_hashes: Collection[tuple[str, FileHash]],
    njob: int,
) -> dict[str, FileHash]:
    """Submit hash jobs for `path_hashes` and run them with bounded concurrency.

    Used by the startup scan and the watcher to drain a batch of hash jobs directly,
    independent of `Builder.job_loop`, which is not running during those phases.
    (Build, startup and watch phases are mutually exclusive.)

    Parameters
    ----------
    hash_queue
        Where jobs are submitted, for dedup with any other in-flight submitter.
    executor
        Runs each claimed job in a thread.
    reporter
        Where `update_progress` is sent, coalesced to at most once per `PROGRESS_REFRESH_DELAY`.
        (The per-job `job_started`/`job_stopped` bracket is `Executor.run_hash_job`'s own.)
    path_hashes
        `(path, old_hash)` pairs to (re)hash; see `HashJob.old_hash`.
    njob
        Maximum number of jobs this call runs concurrently.
        Jobs already claimed by another submitter (e.g. a duplicate path within `path_hashes`)
        are not run here, and therefore do not count against this budget.
        Only their shared future is awaited.

    Returns
    -------
    new_path_hashes
        The new hash of every path in `path_hashes`, keyed by path, in input order.
        A path whose hash could not be computed (e.g. a directory used as a file, or a `stat` error)
        is **absent** from the result:
        `Executor.run_hash_job` has already reported the error and drained the scheduler,
        and neither caller (startup nor watcher) can do anything with that path,
        so raising here would only take down the director over one bad file.
    """
    sem = asyncio.Semaphore(njob)
    ntotal = len(path_hashes)
    nsuccess = 0
    counts_flush_handle: asyncio.TimerHandle | None = None
    counts_flush_tasks: set[asyncio.Task] = set()

    def request_counts_flush() -> None:
        """Schedule an `update_progress` report, coalescing with any already pending.

        Mirrors `ReporterClient._request_jobs_flush`'s coalescing timer:
        unlike `job_started`/`job_stopped`, `update_progress` has no built-in throttling.
        """
        nonlocal counts_flush_handle
        if counts_flush_handle is None:
            loop = asyncio.get_running_loop()
            counts_flush_handle = loop.call_later(PROGRESS_REFRESH_DELAY, on_counts_timer)

    def on_counts_timer() -> None:
        nonlocal counts_flush_handle
        counts_flush_handle = None
        task = asyncio.get_running_loop().create_task(reporter.update_progress(nsuccess, ntotal))
        counts_flush_tasks.add(task)
        task.add_done_callback(counts_flush_tasks.discard)

    async def run_one(job: HashJob) -> FileHash | None:
        nonlocal nsuccess
        if hash_queue.claim(job):
            async with sem:
                await executor.run_hash_job(job)
        try:
            new_hash = await asyncio.shield(job.future)
        except Exception:  # noqa: BLE001
            # Already reported by `Executor.run_hash_job`, which also drained the scheduler.
            # Dropping the path from the result is what keeps one unhashable file
            # from aborting the whole startup scan or watch cycle.
            # A cancelled job raises `CancelledError`,
            # which is not an `Exception` and still propagates, as it must.
            return None
        nsuccess += 1
        request_counts_flush()
        return new_hash

    jobs = [hash_queue.submit(path, old_hash) for path, old_hash in path_hashes]
    new_hashes = await asyncio.gather(*(run_one(job) for job in jobs))

    if counts_flush_handle is not None:
        counts_flush_handle.cancel()
        counts_flush_handle = None
    if len(counts_flush_tasks) > 0:
        await asyncio.gather(*counts_flush_tasks)
    await reporter.update_progress(nsuccess, ntotal)

    return {
        job.path: new_hash
        for job, new_hash in zip(jobs, new_hashes, strict=True)
        if new_hash is not None
    }
