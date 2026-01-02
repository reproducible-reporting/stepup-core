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
"""The `Scheduler` plans the execution of jobs by the worker processes."""

import asyncio
import itertools

import attrs

from .job import Job

__all__ = ("Pool", "Scheduler")


@attrs.define
class Pool:
    """A single pool of running and queued jobs, of which the scheduler can contain several."""

    used: int = attrs.field(converter=int, validator=attrs.validators.ge(0), init=False, default=0)
    """Number of slots currently in use."""

    size: int = attrs.field(converter=int, validator=attrs.validators.gt(0), default=1)
    """Maximum number of slots available."""

    queue: asyncio.PriorityQueue[Job] = attrs.field(init=False, factory=asyncio.PriorityQueue)
    """Queue of jobs that are waiting to be sent to the runner."""

    @property
    def available(self) -> bool:
        """Return True if there is at least one slot available in this pool."""
        return self.size > self.used

    def acquire(self):
        """Acquire a slot in this pool."""
        if self.used >= self.size:
            raise ValueError("Cannot acquire a slot of a fully occupied pool")
        self.used += 1

    def release(self):
        """Release a slot in this pool."""
        if self.used == 0:
            raise ValueError("Cannot release a slot of a fully occupied pool")
        self.used -= 1


@attrs.define
class Scheduler:
    """The Scheduler plans the execution of jobs by the worker processes.

    The scheduler maintains a queue of jobs in several pools.
    If a job is not assigned to a specific pool, it goes into the main pool,
    which has a number of slots equal to the number of worker processes.
    Jobs in other pools can only run when both the main pool and the specific pool have free slots.
    Pools can thus be used to limit concurrency for specific types of jobs.

    The jobs enter the scheduler through the `job_queue` (and the `config_queue`),
    where they are placed by the Workflow instance when it decides that a job is runnable.
    """

    job_queue: asyncio.Queue[Job] = attrs.field()
    """Job queue: Job instances are popped from here and sent to the runner."""

    config_queue: asyncio.Queue[tuple[str, int]] = attrs.field()
    """Pool definitions are popped from here and applied to the scheduler."""

    changed: asyncio.Event = attrs.field()
    """The changed event is set whenever it may be worth calling `pop_runnable_step()`"""

    _main_pool: Pool = attrs.field(factory=Pool, validator=attrs.validators.instance_of(Pool))
    """Keeps overall count of all running steps."""

    _pools: dict[str, Pool] = attrs.field(factory=dict)
    """Keeps counts of running steps per pool name."""

    _onhold: bool = attrs.field(converter=bool, default=False)
    """When True, now new jobs are taken from the job_queue."""

    def __attrs_post_init__(self):
        self.changed.set()

    @property
    def num_workers(self) -> int:
        """Return the number of worker slots in the main pool."""
        return self._main_pool.size

    @property
    def onhold(self) -> bool:
        """Return whether the scheduler is on hold.

        If True, no new jobs are sent to the workers."""
        return self._onhold

    @num_workers.setter
    def num_workers(self, num_workers):
        """The number of worker slots in the main pool."""
        if num_workers <= 0:
            raise ValueError("The maximum number of workers must be strictly positive.")
        self._main_pool.size = num_workers
        self.changed.set()

    @property
    def queues_empty(self) -> bool:
        """Are all queues with runnable steps are empty?"""
        result = all(
            pool.queue.qsize() == 0
            for pool in itertools.chain([self._main_pool], self._pools.values())
        )
        if self._onhold:
            return result
        result &= self.job_queue.qsize() == 0
        return result

    def has_pool(self, pool_name: str | None) -> bool:
        """Return True if the scheduler has a pool with the given name."""
        return True if pool_name is None else pool_name in self._pools

    def set_pool(self, pool_name: str, size: int):
        """Define a new pool or set the size of the pool with the given name."""
        if size <= 0:
            raise ValueError("A pool size must be strictly positive.")
        pool = self._pools.get(pool_name)
        if pool is None:
            self._pools[pool_name] = Pool(size)
        else:
            pool.size = size
        self.changed.set()

    def pop_runnable_job(self) -> Job | None:
        """Return a runnable step (or None if there aren't any)."""
        while not self.config_queue.empty():
            pool_name, pool_size = self.config_queue.get_nowait()
            self.set_pool(pool_name, pool_size)
        if not self._onhold:
            while self.job_queue.qsize() > 0:
                job = self.job_queue.get_nowait()
                if job.pool is None:
                    self._main_pool.queue.put_nowait(job)
                else:
                    self._pools[job.pool].queue.put_nowait(job)
        for pool_name, pool in self._pools.items():
            if self._main_pool.available and pool.available and pool.queue.qsize() > 0:
                job = pool.queue.get_nowait()
                self.acquire_pool(pool_name)
                return job
        if self._main_pool.available and self._main_pool.queue.qsize() > 0:
            job = self._main_pool.queue.get_nowait()
            self.acquire_pool(None)
            return job
        return None

    def acquire_pool(self, pool_name: str | None):
        """Acquire a slot in the given pool (and in the main pool)."""
        if pool_name is not None:
            self._pools[pool_name].acquire()
        self._main_pool.acquire()
        self.changed.set()

    def release_pool(self, pool_name: str | None):
        """Release a slot in the given pool (and in the main pool)."""
        if pool_name is not None:
            self._pools[pool_name].release()
        self._main_pool.release()
        self.changed.set()

    def drain(self):
        """Put all queued jobs back into the main job queue and put the scheduler on hold."""
        self._onhold = True
        all_pools = list(self._pools.values())
        all_pools.append(self._main_pool)
        for pool in all_pools:
            while pool.queue.qsize() > 0:
                self.job_queue.put_nowait(pool.queue.get_nowait())

    def resume(self):
        """Take the scheduler off hold, allowing it to send jobs to the workers again."""
        self._onhold = False
        self.changed.set()
