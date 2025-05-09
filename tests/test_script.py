# StepUp Core provides the basic framework for the StepUp build tool.
# © 2024–2025 Toon Verstraelen
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
"""Unit tests for stepup.core.script."""

import pytest

from stepup.core.script import _get_path_list


def test_get_path_list():
    info = {"inp": ["aa", "bb"], "out": "cc", "blub": 0}
    assert _get_path_list("inp", info, "foo.py", "info") == ["aa", "bb"]
    assert _get_path_list("out", info, "foo.py", "info") == ["cc"]
    assert _get_path_list("vol", info, "foo.py", "info") == []
    with pytest.raises(TypeError):
        _get_path_list("blub", info, "foo.py", "info")
