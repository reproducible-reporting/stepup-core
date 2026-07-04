# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for stepup.core.tui."""

import pytest

from stepup.core.tui import merge_resources


@pytest.mark.parametrize(
    ("base", "override", "expected"),
    [
        # Basic merge: override adds a new key
        ("cpu:4", "gpu:1", "cpu:4,gpu:1"),
        # Override replaces an existing key
        ("cpu:4,gpu:1", "cpu:8", "cpu:8,gpu:1"),
        # Empty base: result is just the override
        ("", "cpu:4", "cpu:4"),
        # Empty override: result is just the base
        ("cpu:4", "", "cpu:4"),
        # Both empty: result is empty string
        ("", "", ""),
        # Override with multiple keys, some new and some replacing
        ("cpu:4,gpu:1,memgb:16", "gpu:2,memgb:32", "cpu:4,gpu:2,memgb:32"),
        # Value defaults to 1 when omitted in override
        ("cpu:4", "gpu", "cpu:4,gpu:1"),
        # Value defaults to 1 when omitted in base
        ("gpu", "cpu:4", "gpu:1,cpu:4"),
        # Override with zero value is valid
        ("cpu:4,gpu:1", "gpu:0", "cpu:4,gpu:0"),
        # Whitespace is stripped
        ("cpu : 4", " gpu : 1 ", "cpu:4,gpu:1"),
        # None base: result is just the override
        (None, "gpu:1", "gpu:1"),
        # None override: result is just the base
        ("cpu:4", None, "cpu:4"),
        # Both None: result is empty string
        (None, None, ""),
    ],
)
def test_merge_resources(base: str | None, override: str | None, expected: str) -> None:
    assert merge_resources(base, override) == expected
