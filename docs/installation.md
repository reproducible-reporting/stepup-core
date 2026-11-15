---
description: >-
  Install StepUp Core with pip on Linux, macOS or Windows, on Python 3.11 or later,
  and on Windows a POSIX shell for the steps that need one.
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
  On Windows, use 3.11.10, 3.12.4 or later,
  so that other users of the computer cannot connect to a running build,
  see [`STEPUP_RPC_TRANSPORT`](reference/configuration.md#stepup-core-module-environment-variables).
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

The [StepUp RepRep](https://reproducible-reporting.github.io/stepup-reprep/) extension
(for reproducible reporting) is installed with:

```bash
pip install stepup-reprep
```

The extension packages have not been ported to Windows yet.
On Windows, only StepUp Core is supported for now.

## POSIX Shell on Windows

A step created with `shell=True` runs its command in a POSIX shell,
and so does a local script whose shebang line names `sh` or `bash`.
Linux and macOS have such a shell as `/bin/sh`, but Windows has none.
Only these steps need one:
a workflow without them runs on Windows without a shell,
and a step that needs a shell when none can be found fails
with a message that lists where StepUp looked.

The shell must be a POSIX shell that finds the usual Unix tools, such as `sed` and `grep`, on its `PATH`.
Any of the following will do:

- [Git for Windows](https://gitforwindows.org/), installed with its default options.
  StepUp finds its shell through the `git` command on `PATH`,
  or through the registry when `git` is not on `PATH`,
  so nothing needs to be configured.
- [MSYS2](https://www.msys2.org/) or [Cygwin](https://www.cygwin.com/),
  with the directory that holds their `sh.exe` on `PATH`, e.g. `C:\msys64\usr\bin`.
- Any other POSIX shell, named by the environment variable `STEPUP_SHELL`.

[`STEPUP_SHELL`](reference/configuration.md#stepup-core-module-environment-variables)
also overrides the shell that StepUp would otherwise find,
and its entry explains the order in which StepUp searches.
To select the shell of Git for Windows with this variable, name its `bin\sh.exe`,
e.g. `C:\Program Files\Git\bin\sh.exe`.
Git also has a `usr\bin\sh.exe`, which does not put Git's Unix tools on `PATH`,
so that the commands of steps would not find them.

StepUp never uses the `bash` of WSL, which Windows may have in its own directory,
because it would run the command in Linux instead of Windows.

The shell of Git for Windows has a few peculiarities,
which [Supported Platforms](platforms.md#shell-commands) describes.
