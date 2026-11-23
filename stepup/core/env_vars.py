# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""The description of the `STEPUP_*` environment variables of StepUp Core and its extensions.

A package registers its variables with a callable that takes no arguments
and returns an `EnvVarRegistry`, which it lists as an entry point in the `stepup.env_vars` group:

```toml
[project.entry-points."stepup.env_vars"]
fancy = "stepup.fancy.env_vars:env_vars"
```

`stepup config` uses the registries to show and check the variables that are set,
and never prints the value of a variable that is not registered with `secret=False`.
"""

from collections.abc import Callable

import attrs

__all__ = ("EnvVarInfo", "EnvVarRegistry")


@attrs.define(frozen=True, kw_only=True)
class EnvVarInfo:
    """The description of one environment variable."""

    secret: bool = attrs.field()
    """Whether the value must never be printed.

    There is no default, so that every variable whose value is shown was declared harmless.
    """

    overridden: bool = attrs.field(default=False)
    """Whether StepUp ignores the variable and overwrites it in the environment of every step.

    Setting such a variable by hand configures nothing and is most likely a mistake.
    An overridden variable has no validator.
    """

    validator: Callable[[str], object] | None = attrs.field(default=None)
    """A function that raises `ValueError` when the value of the variable cannot be used.

    It is called with the value as a string, and its return value is ignored,
    so the function with which the package parses the variable can be passed directly,
    e.g. `int` or `float`.
    Any other exception than `ValueError` is reported as a bug in the package.
    `None` accepts every value.
    """


@attrs.define(frozen=True)
class EnvVarRegistry:
    """The environment variables of one package."""

    package: str = attrs.field()
    """The human readable name of the package, e.g. `StepUp Core`."""

    env_vars: dict[str, EnvVarInfo] = attrs.field()
    """The variables, keyed by their name."""
