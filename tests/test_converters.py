# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Tests for `stepup.core.converters`."""

import argparse
from decimal import Decimal

import pytest

from stepup.core.converters import boolean, positive_decimal, positive_int
from stepup.core.exceptions import StepUpError


@pytest.mark.parametrize("value", ["yes", "TRUE", "t", "Y", "1", True])
def test_boolean_true(value):
    assert boolean(value) is True


@pytest.mark.parametrize("value", ["no", "False", "f", "N", "0", False])
def test_boolean_false(value):
    assert boolean(value) is False


def test_boolean_invalid():
    with pytest.raises(StepUpError, match=r"Cannot interpret 'maybe' as a boolean value."):
        boolean("maybe")


def test_boolean_type():
    with pytest.raises(TypeError, match="Got int"):
        boolean(1)


@pytest.mark.parametrize(("value", "expected"), [("4", 4), (4, 4)])
def test_positive_int(value, expected):
    assert positive_int(value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [("0", "must be strictly positive: '0'"), ("abc", "not an integer: 'abc'")],
)
def test_positive_int_invalid(value, message):
    with pytest.raises(StepUpError, match=message):
        positive_int(value)


@pytest.mark.parametrize(
    ("value", "expected"), [("3.0", Decimal("3.0")), (3.0, Decimal("3.0")), ("2", Decimal("2"))]
)
def test_positive_decimal(value, expected):
    result = positive_decimal(value)
    assert result == expected
    assert result.as_tuple() == expected.as_tuple()


@pytest.mark.parametrize(
    ("value", "message"),
    [("-1.5", "must be strictly positive: '-1.5'"), ("abc", "not a number: 'abc'")],
)
def test_positive_decimal_invalid(value, message):
    with pytest.raises(StepUpError, match=message):
        positive_decimal(value)


def test_argparse_message(capsys):
    parser = argparse.ArgumentParser(prog="prog")
    parser.add_argument("--flag", type=boolean)
    with pytest.raises(SystemExit):
        parser.parse_args(["--flag", "maybe"])
    assert "argument --flag: invalid boolean value: 'maybe'" in capsys.readouterr().err
