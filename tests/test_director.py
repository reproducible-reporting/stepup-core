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
"""Unit tests for stepup.core.director."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from stepup.core.director import DirectorHandler, interpret_jobs
from stepup.core.step import Step
from stepup.core.utils import DBLock
from stepup.core.workflow import Workflow


def test_interpret_jobs_integer():
    assert interpret_jobs(Decimal("4")) == 4


def test_interpret_jobs_fraction_affinity():
    with patch("os.sched_getaffinity", return_value=set(range(8)), create=True):
        result = interpret_jobs(Decimal("1.5"))
    assert result == 12


def test_interpret_jobs_fraction_cpu_count():
    with (
        patch("stepup.core.director.os") as mock_os,
    ):
        del mock_os.sched_getaffinity
        mock_os.cpu_count.return_value = 8
        result = interpret_jobs(Decimal("1.5"))
    assert result == 12


@pytest.mark.asyncio
async def test_record_subprocess_rpc(wfp: Workflow):
    """The record_subprocess RPC handler inserts a row for the targeted step."""
    handler = DirectorHandler(
        scheduler=None,
        workflow=wfp,
        dblock=DBLock(wfp.con),
        reporter=None,
        builder=None,
        watcher=None,
        stop_event=None,
    )
    step = wfp.find(Step, "./plan.py")
    await handler.record_subprocess(step.i, "typst compile a.typ", "sub", {"X": "1"}, 0, False)
    await handler.record_subprocess(step.i, "echo hi", ".", None, 3, True)
    assert list(step.iter_subprocesses()) == [
        (0, "typst compile a.typ", "sub", {"X": "1"}, 0, False),
        (1, "echo hi", ".", None, 3, True),
    ]
