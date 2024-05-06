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
"""The `Scheduler` plans the execution of jobs by the worker processes."""

import asyncio
import itertools

import attrs

from .job import Job

__all__ = ("Pool", "Scheduler")


@attrs.define
class Pool:
    """A single pool of which the scheduler can contain several"""

    used: int = attrs.field(converter=int, validator=attrs.validators.ge(0), init=False, default=0)
    size: int = attrs.field(converter=int, validator=attrs.validators.gt(0), default=1)
    queue: asyncio.Queue[Job] = attrs.field(init=False, factory=asyncio.Queue)

    @property
    def available(self) -> bool:
        return self.size > self.used

    def acquire(self):
        if self.used >= self.size:
            raise ValueError("Cannot acquire a slot of a fully occupied pool")
        self.used += 1

    def release(self):
        if self.used == 0:
            raise ValueError("Cannot release a slot of a fully occupied pool")
        self.used -= 1


@attrs.define
class Scheduler:
    # Input queue: Job instances are taken from here
    inqueue: asyncio.Queue[Job] = attrs.field()
    # The changed event is set to True whenever it may be worth calling pop_runnable_step
    changed: asyncio.Event = attrs.field()
    # Main pool keeps overall count of all active steps.
    _main_pool: Pool = attrs.field(factory=Pool, validator=attrs.validators.instance_of(Pool))
    # The _pools dict also keeps counts per pool name.
    _pools: dict[str, Pool] = attrs.field(factory=dict)
    # When True, all steps end up in the wait queue instead of the run queue
    _onhold: bool = attrs.field(converter=bool, default=False)

    def __attrs_post_init__(self):
        self.changed.set()

    @property
    def num_workers(self) -> int:
        return self._main_pool.size

    @property
    def onhold(self) -> bool:
        return self._onhold

    @num_workers.setter
    def num_workers(self, num_workers):
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
        result &= self.inqueue.qsize() == 0
        return result

    def has_pool(self, pool_name: str | None) -> bool:
        return True if pool_name is None else pool_name in self._pools

    def set_pool(self, pool_name: str, size: int):
        if size <= 0:
            raise ValueError("A pool size must be strictly positive.")
        pool = self._pools.get(pool_name)
        if pool is None:
            self._pools[pool_name] = Pool(size)
        else:
            pool.size = size
        self.changed.set()

    def pop_runnable_job(self) -> tuple[Job | None, str | None]:
        """Return a runnable step and its pool (or None)."""
        if not self._onhold:
            while self.inqueue.qsize() > 0:
                job = self.inqueue.get_nowait()
                if job.pool is None:
                    self._main_pool.queue.put_nowait(job)
                else:
                    self._pools[job.pool].queue.put_nowait(job)
        for pool_name, pool in self._pools.items():
            if self._main_pool.available and pool.available and pool.queue.qsize() > 0:
                job = pool.queue.get_nowait()
                self.acquire_pool(pool_name)
                return job, pool_name
        if self._main_pool.available and self._main_pool.queue.qsize() > 0:
            job = self._main_pool.queue.get_nowait()
            self.acquire_pool(None)
            return job, None
        return None, None

    def acquire_pool(self, pool_name: str | None):
        if pool_name is not None:
            self._pools[pool_name].acquire()
        self._main_pool.acquire()
        self.changed.set()

    def release_pool(self, pool_name: str | None):
        if pool_name is not None:
            self._pools[pool_name].release()
        self._main_pool.release()
        self.changed.set()

    def drain(self):
        self._onhold = True
        all_pools = list(self._pools.values())
        all_pools.append(self._main_pool)
        for pool in all_pools:
            while pool.queue.qsize() > 0:
                self.inqueue.put_nowait(pool.queue.get_nowait())

    def resume(self):
        self._onhold = False
        self.changed.set()
