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

from stepup.core.director import interpret_jobs


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
