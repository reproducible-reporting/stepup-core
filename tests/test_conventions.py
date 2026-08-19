# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for coding conventions that ruff cannot express.

See the `__all__` section in `CLAUDE.md` for the convention these tests enforce.
"""

from stepup.core.pytest import ConventionTests


class TestConventions(ConventionTests):
    package = "stepup.core"
