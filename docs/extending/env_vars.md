---
description: >-
  Name, register and protect the STEPUP environment variables of an extension package,
  so that stepup config recognizes them and never prints a secret.
---

# Environment Variables

<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

An extension package may be configured through environment variables of its own,
e.g. the address of a server or a token to access it.
`stepup config` lists every `STEPUP_*` variable that is set,
and treats a variable that no package registered as a misspelled name.
It shows the value of a variable only when a package registered it as harmless,
see [Unrecognized Environment Variables](../reference/configuration.md#unrecognized-environment-variables).
This page explains how an extension registers its variables.

## Names

The variables of an extension start with `STEPUP_<NAME>_`,
where `<NAME>` is the name of the `stepup.env_vars` entry point of the package, in upper case,
with dots and hyphens replaced by underscores.
For example, the entry point `data` claims all variables starting with `STEPUP_DATA_`.

The name of a [subcommand](tool.md) of StepUp Core cannot be used as the name of the entry point,
because the variables of its settings already start with `STEPUP_<SUBCOMMAND>_`,
e.g. `STEPUP_BUILD_JOBS` for the `jobs` setting of `stepup build`.
The reserved names are those listed by `stepup --help`, apart from those of extensions:
`boot`, `browse`, `build`, `clean`, `config`, `drain`, `graph`, `join`, `rebuild`,
`shutdown`, `status` and `wait`.
The variables of StepUp Core itself, such as `STEPUP_DEBUG`, are exempt from this convention.

## Registration

A package registers its variables with a function that takes no arguments
and returns an [`EnvVarRegistry`][stepup.core.env_vars.EnvVarRegistry]:

```python
from stepup.core.converters import boolean
from stepup.core.env_vars import EnvVarInfo, EnvVarRegistry


def parse_mode(value: str) -> str:
    if value not in ("fast", "slow"):
        raise ValueError(f"must be fast or slow, got {value!r}")
    return value


def env_vars() -> EnvVarRegistry:
    return EnvVarRegistry(
        package="StepUp Fancy",
        env_vars={
            "STEPUP_FANCY_MODE": EnvVarInfo(secret=False, validator=parse_mode),
            "STEPUP_FANCY_VERBOSE": EnvVarInfo(secret=False, validator=boolean),
            "STEPUP_FANCY_TOKEN": EnvVarInfo(secret=True),
        },
    )
```

The function is listed as an entry point in the `stepup.env_vars` group of `pyproject.toml`:

```toml
[project.entry-points."stepup.env_vars"]
fancy = "stepup.fancy.env_vars:env_vars"
```

Every variable is described by an [`EnvVarInfo`][stepup.core.env_vars.EnvVarInfo]:

- `secret` must always be given.
  The value of a variable is only printed when it is `False`.
- `overridden` is `True` for a variable that StepUp sets itself for every step,
  overwriting any value the user gives it.
- `validator` is a function that raises `ValueError` when the value cannot be used.
  Its return value is ignored,
  so the function with which the package reads the variable can be passed directly,
  as `parse_mode` in the example.
  The converters in [`stepup.core.converters`](../reference/stepup.core.converters.md)
  are suitable validators,
  e.g. [`boolean()`][stepup.core.converters.boolean] for a variable that switches a feature,
  which then accepts the same values as the boolean variables of StepUp Core.
  An overridden variable has no validator.

The package name is shown as the header of the group in which `stepup config`
lists the variables of the package.
Only `stepup config` loads the entry points,
so the other subcommands do not import the extension packages to check their variables.

`stepup config` reports a problem with the registration of a package,
e.g. when the entry point cannot be imported,
when a variable does not start with `STEPUP_<NAME>_`,
or when a variable is registered twice or is also the variable of a setting.
It also reports a validator that raises another exception than `ValueError`,
as a bug in the package.

## Secrets

A secret, such as a token or a password, must be read from an environment variable
registered with `secret=True`, and never from a command-line option.
The settings of the subcommands can also be given in a config file,
and `stepup config` always shows their values, also when they come from an environment variable.

The value of a secret never appears in the output of `stepup config`,
not even when the validator rejects it,
because the message of the exception may quote the value.
The problem then only says that the value is invalid.

Name a secret with a suffix that makes its nature clear to the user,
such as `_TOKEN`, `_SECRET`, `_PASSWORD`, `_PASSPHRASE` or `_API_KEY`.
This is advice for readers only:
whether a value is shown depends on `secret` alone.
