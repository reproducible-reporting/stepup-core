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
"""Unit tests for stepup.core.job."""

import pytest

from stepup.core.job import RunJob, ValidateAmendedJob


@pytest.mark.parametrize("job_cls", [RunJob, ValidateAmendedJob])
def test_job_ordering(job_cls):
    job1 = job_cls(None, None, (1, 2), [], [], None)
    job2 = job_cls(None, None, (1, 3), [], [], None)
    job3 = job_cls(None, None, (2, 1), [], [], None)

    assert job1 < job2 < job3


def test_job_ordering_mixed_classes():
    job1 = RunJob(None, None, (1, 2), [], [], None)
    job2 = ValidateAmendedJob(None, None, (1, 3), [], [], None)
    job3 = RunJob(None, None, (2, 1), [], [], None)

    assert job1 < job2 < job3
