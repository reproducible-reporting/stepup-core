# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
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
