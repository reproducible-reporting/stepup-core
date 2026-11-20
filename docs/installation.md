---
description: >-
  Install StepUp Core with pip on Linux, macOS or Windows, with Python 3.11 or later.
  On Windows, some steps also need a POSIX shell.
---

# Installation

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

Requirements:

- Linux, macOS or Windows.
  [Supported Platforms](platforms.md) explains what differs between them.
- [Python](https://www.python.org/) ≥ 3.11.
  On Windows, use Python 3.12.4 or later, or a 3.11 release from 3.11.10 on,
  so that other users of the computer cannot connect to a running build,
  see [`STEPUP_RPC_TRANSPORT`](reference/configuration.md#stepup-core-module-environment-variables).
  Older versions are only safe when `TEMP` lies in your user profile, which is the default.
- [Pip](https://pip.pypa.io/)
- On Windows, a POSIX shell for the steps that need one,
  see [POSIX Shell on Windows](#posix-shell-on-windows) below.

It is assumed that you know how to use [Pip](https://pip.pypa.io/).
We recommend performing the installation in a
[Python virtual environment](https://docs.python.org/3/library/venv.html)
and activating such environments with [direnv](https://direnv.net/).

The core package can be installed with:

```bash
pip install stepup
```

On Windows with Python 3.15, as of November 2026,
the dependency PyYAML has no wheels yet,
and building it from source fails without a C compiler.
Its C extension, which StepUp does not use, can be left out of that build:

```powershell
$env:PYYAML_FORCE_LIBYAML = "0"
pip install stepup
```

The [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/) extension
(for reproducible reporting) is installed with:

```bash
pip install stepup-reprep
```

## POSIX Shell on Windows

A step created with `shell=True` runs its command in a POSIX shell,
and so does a local script whose shebang line names `sh` or `bash`.
Linux and macOS have such a shell as `/bin/sh`, but Windows has none.
A workflow without shell steps and shell scripts runs on Windows without a POSIX shell.
When a step needs a shell and StepUp finds none,
that step fails with a message that lists where StepUp looked.

The shell must be a POSIX shell that finds the usual Unix tools,
such as `sed` and `grep`, on its `PATH`.
Any of the following will do:

- [Git for Windows](https://gitforwindows.org/), installed with its default options.
  StepUp finds its shell through the `git` command on `PATH`,
  or through the registry when `git` is not on `PATH`,
  so nothing needs to be configured.
- [MSYS2](https://www.msys2.org/) or [Cygwin](https://www.cygwin.com/),
  with the directory that holds their `sh.exe` on `PATH`, e.g. `C:\msys64\usr\bin`.
- Any other POSIX shell, named by the environment variable `STEPUP_SHELL`.

When `STEPUP_SHELL` is set, StepUp uses that shell and does not search.
The [configuration reference](reference/configuration.md#stepup-core-module-environment-variables)
lists the order of the search.
To select the shell of Git for Windows with this variable, name its `bin\sh.exe`,
e.g. `C:\Program Files\Git\bin\sh.exe`.
Do not select Git's `usr\bin\sh.exe`:
it does not add Git's Unix tools to `PATH`,
so the commands of your steps would not find `sed` or `grep`.

StepUp never uses the `bash.exe` in the Windows directory (`C:\Windows\System32`),
because that program starts WSL and would run the command in Linux.

The shell of Git for Windows has a few peculiarities,
which [Supported Platforms](platforms.md#shell-commands) describes.
