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
"""Unit tests for stepup.core.scheduler."""

import asyncio

import pytest

from stepup.core.job import RunJob, TryReplayJob
from stepup.core.scheduler import Pool, Scheduler


def test_pool_basics():
    pool = Pool(2)
    assert pool.used == 0
    assert pool.size == 2
    assert pool.available
    pool.acquire()
    assert pool.used == 1
    assert pool.size == 2
    assert pool.available
    pool.acquire()
    assert pool.used == 2
    assert pool.size == 2
    assert not pool.available
    with pytest.raises(ValueError):
        pool.acquire()
    pool.release()
    assert pool.used == 1
    assert pool.size == 2
    assert pool.available
    pool.release()
    assert pool.used == 0
    assert pool.size == 2
    assert pool.available
    with pytest.raises(ValueError):
        pool.release()


def queue_job(scheduler: Scheduler, step_key: str, pool: str | None, try_replay: bool):
    job = TryReplayJob(step_key, pool) if try_replay else RunJob(step_key, pool)
    scheduler.inqueue.put_nowait(job)
    scheduler.changed.set()


def test_scheduler_basics_without_pools():
    scheduler = Scheduler(asyncio.Queue(), asyncio.Event())
    assert scheduler.num_workers == 1
    scheduler.changed.clear()
    scheduler.num_workers = 2
    assert scheduler.changed.is_set()
    assert scheduler.num_workers == 2
    assert not scheduler.onhold
    assert scheduler.queues_empty
    assert scheduler.has_pool(None)
    assert not scheduler.has_pool("name")
    assert scheduler._main_pool.used == 0
    queue_job(scheduler, "3", None, False)
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert not scheduler.queues_empty
    assert scheduler.pop_runnable_job() == (RunJob("3", None), None)
    assert scheduler.queues_empty
    assert scheduler._main_pool.used == 1
    scheduler.release_pool(None)
    assert scheduler._main_pool.used == 0


def test_scheduler_basics_with_pools():
    scheduler = Scheduler(asyncio.Queue(), asyncio.Event())
    scheduler.set_pool("pool", 2)
    assert scheduler.has_pool(None)
    assert scheduler.has_pool("pool")
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].used == 0
    queue_job(scheduler, "3", None, False)
    queue_job(scheduler, "4", "pool", False)
    assert not scheduler.queues_empty
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].queue.qsize() == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler.pop_runnable_job() == (RunJob("4", "pool"), "pool")
    assert not scheduler.queues_empty
    assert scheduler._pools["pool"].used == 1
    assert scheduler._main_pool.used == 1
    assert scheduler._main_pool.queue.qsize() == 1
    assert scheduler._pools["pool"].queue.qsize() == 0
    scheduler.release_pool("pool")
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler.pop_runnable_job() == (RunJob("3", None), None)
    assert scheduler.queues_empty
    assert scheduler._main_pool.used == 1
    assert scheduler._main_pool.queue.qsize() == 0
    with pytest.raises(ValueError):
        scheduler.release_pool("pool")
    scheduler.release_pool(None)
    assert scheduler._main_pool.used == 0

    queue_job(scheduler, "4", "name", False)
    with pytest.raises(KeyError):
        scheduler.pop_runnable_job()


def test_scheduler_basics_with_pools_replay():
    scheduler = Scheduler(asyncio.Queue(), asyncio.Event())
    scheduler.set_pool("pool", 1)
    assert scheduler.has_pool(None)
    assert scheduler.has_pool("pool")
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].used == 0
    queue_job(scheduler, "2", "pool", True)
    queue_job(scheduler, "7", None, False)
    assert not scheduler.queues_empty
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].queue.qsize() == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler.pop_runnable_job() == (TryReplayJob("2", "pool"), None)
    assert not scheduler.queues_empty
    assert scheduler._pools["pool"].used == 0
    assert scheduler._main_pool.used == 1
    assert scheduler._main_pool.queue.qsize() == 1
    assert scheduler._pools["pool"].queue.qsize() == 0
    scheduler.release_pool(None)
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler.pop_runnable_job() == (RunJob("7", None), None)
    assert scheduler.queues_empty
    assert scheduler._main_pool.used == 1
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler._pools["pool"].queue.qsize() == 0
    scheduler.release_pool(None)
    with pytest.raises(ValueError):
        scheduler.release_pool("pool")
    assert scheduler._main_pool.used == 0


def test_scheduler_onhold_without_pools():
    scheduler = Scheduler(asyncio.Queue(), asyncio.Event())
    assert not scheduler.onhold
    queue_job(scheduler, "3", None, False)
    assert not scheduler.onhold
    assert not scheduler.queues_empty
    scheduler.drain()
    assert scheduler.queues_empty
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert scheduler.pop_runnable_job() == (None, None)
    scheduler.resume()
    assert scheduler.pop_runnable_job() == (RunJob("3", None), None)
    assert scheduler._main_pool.used == 1
    scheduler.release_pool(None)
    assert scheduler._main_pool.used == 0


def test_scheduler_onhold_with_pools():
    scheduler = Scheduler(asyncio.Queue(), asyncio.Event())
    scheduler.num_workers = 2
    scheduler.set_pool("pool", 1)
    scheduler.set_pool("boo", 2)
    assert not scheduler.onhold
    assert scheduler.queues_empty
    scheduler.drain()
    queue_job(scheduler, "3", None, False)
    queue_job(scheduler, "4", "pool", False)
    queue_job(scheduler, "5", "pool", False)
    queue_job(scheduler, "6", "boo", False)
    assert scheduler.queues_empty
    assert scheduler.inqueue.qsize() == 4
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].queue.qsize() == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler._pools["boo"].queue.qsize() == 0
    assert scheduler._pools["boo"].used == 0

    scheduler.resume()
    assert not scheduler.queues_empty
    assert scheduler.inqueue.qsize() == 4
    assert scheduler._main_pool.queue.qsize() == 0
    assert scheduler._main_pool.used == 0
    assert scheduler._pools["pool"].queue.qsize() == 0
    assert scheduler._pools["pool"].used == 0
    assert scheduler._pools["boo"].queue.qsize() == 0
    assert scheduler._pools["boo"].used == 0

    assert scheduler.pop_runnable_job() == (RunJob("4", "pool"), "pool")
    assert scheduler.inqueue.qsize() == 0
    assert scheduler._main_pool.queue.qsize() == 1
    assert scheduler._main_pool.used == 1
    assert scheduler._pools["pool"].queue.qsize() == 1
    assert scheduler._pools["pool"].used == 1
    assert scheduler._pools["boo"].queue.qsize() == 1
    assert scheduler._pools["boo"].used == 0

    assert not scheduler._pools["pool"].available
    assert scheduler.pop_runnable_job() == (RunJob("6", "boo"), "boo")
    assert scheduler._pools["boo"].used == 1
    assert scheduler._pools["boo"].queue.qsize() == 0
    scheduler.release_pool("pool")
    assert scheduler._pools["pool"].available
    assert scheduler._pools["pool"].used == 0
    scheduler.release_pool("boo")
    assert scheduler._pools["boo"].available
    assert scheduler._pools["boo"].used == 0

    assert scheduler.pop_runnable_job() == (RunJob("5", "pool"), "pool")
    assert not scheduler._pools["pool"].available
    assert scheduler._pools["pool"].used == 1
    assert scheduler._pools["pool"].queue.qsize() == 0
    scheduler.release_pool("pool")
    assert scheduler._pools["pool"].available
    assert scheduler._pools["pool"].used == 0

    assert scheduler.pop_runnable_job() == (RunJob("3", None), None)
    assert scheduler._main_pool.used == 1
    assert scheduler._main_pool.queue.qsize() == 0
    scheduler.release_pool(None)
    assert scheduler._main_pool.used == 0
