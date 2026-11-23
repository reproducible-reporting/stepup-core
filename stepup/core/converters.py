# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Converters from the value of a setting or an environment variable to a Python object.

Each converter takes the value as a string,
which is how it comes from the command line or an environment variable,
or as the matching TOML type, which is how it may come from a configuration file.
It returns the converted value
and raises a `StepUpError` (a subclass of `ValueError`) when the value cannot be used,
or a `TypeError` when the value has a type the converter does not accept.

A converter works in all places where StepUp expects a function of this kind:

- as the `type` of an `argparse` argument,
  in which case the name of the converter appears in the error message,
  e.g. `invalid boolean value: 'maybe'`,
- as the `validator` of an [`EnvVarInfo`][stepup.core.env_vars.EnvVarInfo],
- and in a step that reads an environment variable,
  e.g. `boolean(os.getenv("STEPUP_FANCY_VERBOSE", "0"))`,
  in which case a `StepUpError` is reported as a short message without a traceback.
"""

import decimal
from decimal import Decimal

from .exceptions import StepUpError

__all__ = ("boolean", "positive_decimal", "positive_int")


def boolean(value: str | bool) -> bool:
    """Convert a string to a boolean value, passing a boolean through unchanged.

    The strings `yes`, `true`, `t`, `y` and `1` mean `True`,
    and `no`, `false`, `f`, `n` and `0` mean `False`, regardless of their case.

    Parameters
    ----------
    value
        The value to convert to a boolean.

    Returns
    -------
    flag
        The boolean represented by the value.

    Raises
    ------
    StepUpError
        If the string cannot be interpreted as a boolean value.
    TypeError
        If the value is neither a string nor a boolean.

    Examples
    --------
    >>> boolean('yes')
    True
    >>> boolean('No')
    False
    >>> boolean(True)
    True
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in ("yes", "true", "t", "y", "1"):
            return True
        if value.lower() in ("no", "false", "f", "n", "0"):
            return False
        raise StepUpError(f"Cannot interpret '{value}' as a boolean value.")
    raise TypeError(f"Expected a boolean value or string. Got {type(value).__name__}")


def positive_int(value: str | int) -> int:
    """Convert a value to an integer, requiring it to be strictly positive.

    Parameters
    ----------
    value
        The value to convert, e.g. `"4"` or `4`.

    Returns
    -------
    number
        The strictly positive integer.

    Raises
    ------
    StepUpError
        If the value is not an integer or is not strictly positive.

    Examples
    --------
    >>> positive_int('4')
    4
    """
    try:
        number = int(value)
    except ValueError as exc:
        raise StepUpError(f"not an integer: {value!r}") from exc
    if number <= 0:
        raise StepUpError(f"must be strictly positive: {value!r}")
    return number


def positive_decimal(value: str | float) -> Decimal:
    """Convert a value to a strictly positive `Decimal`.

    The value is converted through its string form,
    so that the number of digits after the decimal point is preserved:
    `Decimal(3.0)` is `Decimal("3")` while `Decimal("3.0")` keeps the digit,
    and a setting may give that digit a meaning of its own.

    Parameters
    ----------
    value
        The value to convert, e.g. `"1.5"` or `1.5`.

    Returns
    -------
    number
        The strictly positive decimal number.

    Raises
    ------
    StepUpError
        If the value is not a number or is not strictly positive.

    Examples
    --------
    >>> positive_decimal('3.0')
    Decimal('3.0')
    """
    try:
        number = Decimal(str(value))
    except decimal.InvalidOperation as exc:
        raise StepUpError(f"not a number: {value!r}") from exc
    if number <= 0:
        raise StepUpError(f"must be strictly positive: {value!r}")
    return number
