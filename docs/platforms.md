---
description: >-
  What StepUp does the same on Linux, macOS and Windows, which features only some of them have,
  and what a workflow needs to give the same results on each of them.
---

# Supported Platforms

<!--
SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

StepUp runs on Linux, macOS and Windows.
It is developed on Linux, which also has the most features.
This page explains what StepUp does the same on every platform,
which features are limited to some of them,
and what you have to take care of for a workflow to run on all of them.

## What StepUp Does the Same Everywhere

StepUp is platform independent in everything it does itself:

- A path has `/` as its only separator,
  in the workflow graph, in the results of the API and in the variables `HERE` and `ROOT`.
- [`copy()`][stepup.core.api.copy] copies a file without starting a program.
- A local script with a shebang line runs with the interpreter on that line,
  also without a shell.
- The output of a step is recorded as UTF-8 with `\n` line endings.
- The files that StepUp writes are UTF-8 with `\n` line endings.
  This includes the files in `.stepup/`, `graph.txt`
  and the files written by `dumpns()` and `render_jinja()`.

Whatever happens outside the `plan.py` files, e.g. in shell commands and scripts,
is for you to make reproducible across platforms,
because StepUp does not change what the programs of a step do.
The differences you are most likely to run into are:

- **Line endings and encodings of the files that scripts write.**
  On Windows, Python ends the lines of a text file with `\r\n` by default,
  and Python 3.14 and older write it in the ANSI code page, e.g. `cp1252`.
  Open text files with `open(path, "w", encoding="utf-8", newline="\n")`.
  When a shell redirects the output of a Python script to a file, e.g. `./work.py > out.txt`,
  the script has to configure its standard output in the same way:

    ```python
    import sys

    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    ```

- **Line endings of files in Git.**
  On Windows, Git converts the line endings of text files to `\r\n` when it checks them out,
  unless it is told otherwise.
  A shell script with `\r\n` line endings fails in bash,
  and every converted file has another digest and size than on Linux and macOS.
  Add a file `.gitattributes` with the following line to the root of the repository:

    ```text
    * text=auto eol=lf
    ```

    If you only want to protect shell scripts, use `*.sh text eol=lf` instead.

- **Executable bits in Git.**
  Windows has no executable bit,
  so a script that you add to Git on Windows is not executable after a checkout on Linux or macOS.
  Mark it with `git add --chmod=+x script.py`.
- **Case of file names.**
  The default file systems of macOS and Windows do not distinguish upper and lower case,
  while StepUp compares paths as strings.
  For StepUp, `Data.csv` and `data.csv` are two different files,
  even when they are the same file on disk.
  Spell each path the same way throughout a workflow,
  and do not create files whose names only differ in case.
- **The programs of shell commands.**
  A shell command runs the programs that its shell finds on `PATH`:
  the GNU tools on Linux, the BSD tools on macOS, and on Windows those of the shell you installed.
  They do not all accept the same options, e.g. `sed -i` needs an argument on macOS.

## Features per Platform

| Feature | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Shell for `shell=True` | `/bin/sh` | `/bin/sh` | [Git for Windows or another][posix-shell] |
| Watch mode | inotify | watchfiles | watchfiles |
| Forkserver for Python steps | on by default | off by default | not available |
| `--cgroup` | with cgroup v2 and `systemd-run` | not available | not available |
| `--perf` | available | not available | not available |
| Suspending with Ctrl-Z | available | available | not available |
| Connections between processes | Unix domain sockets | Unix domain sockets | TCP on `127.0.0.1` |

Enabling a feature on a platform that does not have it is an error,
which StepUp reports before the build starts.
The libraries for watch mode are compared in
[How Watch Mode Observes Changes](reference/interactive.md#how-watch-mode-observes-changes),
and the other features are explained in [Configuration](reference/configuration.md).

## Linux

Linux needs nothing beyond the [installation](installation.md).
[WSL](https://learn.microsoft.com/en-us/windows/wsl/) counts as Linux,
with one limitation in watch mode:
WSL 2 does not report changes to files on a Windows drive, such as `/mnt/c`,
when Windows programs make them.
This is a limitation of WSL 2 itself,
which Microsoft tracks in [microsoft/WSL#4739](https://github.com/microsoft/WSL/issues/4739),
and StepUp has no way around it.
Keep a project that you edit with Windows programs in the file system of WSL,
or run StepUp on Windows itself.

## macOS

macOS needs nothing beyond the [installation](installation.md).
Shell commands run with `/bin/sh`, and they find the BSD versions of the usual Unix tools,
which differ in some options from the GNU versions on Linux.

## Windows

StepUp runs on Windows natively, without WSL.
Shell steps need a POSIX shell, see [POSIX Shell on Windows](installation.md#posix-shell-on-windows),
and the [installation](installation.md) lists the minimal version of Python for Windows,
below which other users of the computer may be able to connect to a running build.

### Paths

StepUp writes paths with `/`, also on Windows, e.g. `C:/Users/me/project/data.csv`,
always with an upper-case drive letter.
Windows accepts `/` in all its file functions,
so these paths can be passed to `open()` and to every other function of Python.
All paths that StepUp returns are instances of [`Path`][stepup.core.path.Path]
from `stepup.core.path`, which keeps this spelling when you join or change them.
Import `Path` from `stepup.core.path` instead of from the `path` library,
because the latter gives mixed separators on Windows:
`path.Path("sub") / "file.txt"` is `sub\file.txt`.

You will notice this convention in the following situations:

- **Comparing paths.**
  A path from StepUp is not equal to `str()` of a `pathlib.Path` of the same file,
  which has backslashes on Windows.
  Convert the latter with `Path` from `stepup.core.path` before comparing them,
  or compare `pathlib.Path` objects:

    ```python
    import pathlib
    from stepup.core.path import Path

    native = pathlib.Path("data") / "table.csv"
    str(native)  # 'data\\table.csv' on Windows
    Path(native)  # 'data/table.csv' on every platform
    ```

- **Paths in commands.**
  Embed a path in a command with [`shq()`][stepup.core.api.shq],
  which writes it with `/` on every platform,
  or with the method `as_posix()` of a `pathlib.Path`.
  A command is text, which StepUp passes on exactly as you wrote it.
  Its words are split with the rules of a POSIX shell,
  also when it runs without a shell,
  and in those rules a backslash escapes the next character.
  A native path that you put into a command with an f-string therefore loses its backslashes,
  without any error:

    ```python
    import pathlib
    from stepup.core.api import run, shq

    src = pathlib.Path("data") / "table.csv"
    # Wrong: on Windows, analyze.py receives the argument datatable.csv.
    run(f"./analyze.py {src}", inp=src)
    # Right: analyze.py receives data/table.csv on every platform.
    run(f"./analyze.py {shq(src)}", inp=src)
    ```

- **Programs that need backslashes.**
  `os.path.normpath(path)` gives the native spelling of a path, for a program that needs it.
  Put such a path into a command with `shlex.quote()`, which keeps its backslashes,
  and not with `shq()`, which writes `/` again.
- **Lists of paths.**
  The items of `STEPUP_PATH_FILTER`
  and of a variable read with [`getenv(..., multi=True)`][stepup.core.api.getenv]
  are separated by `;` on Windows, as in `PATH`, because `:` is part of a path like `C:/data`.
  On Linux and macOS, the separator is `:`.

### Commands Without a Shell

On Linux and macOS, the operating system finds the program of a command
and reads the shebang line of a script.
Windows does neither, so StepUp does it, in the same way:

- A program without a directory, e.g. `typst`,
  is looked up on the `PATH` of the step, with the extensions of `PATHEXT`, e.g. `typst.exe`.
  Unlike in `cmd.exe`, it is never looked up in the current directory.
  Write `./tool.exe` for a program there.
- A program with a directory, e.g. `./tool`, is looked up in the working directory of the step,
  with an extension of `PATHEXT` when the file does not exist without one.
- A batch file (`*.bat` or `*.cmd`) runs with `cmd.exe`,
  and StepUp quotes its arguments so that they arrive unchanged.
  An argument with a line break cannot be passed to a batch file, and makes the step fail.
- A script without such an extension runs with the interpreter on its shebang line.
  StepUp only uses the name of that interpreter, without its directory,
  and supports `#!/bin/NAME`, `#!/usr/bin/env NAME` and `#!/usr/bin/env -S NAME ARGS`.
  As on Linux, everything after `NAME` is a single argument, except with `env -S`.
  The other options of `env` are not supported.
  The names `python` and `python3` stand for the Python that runs StepUp.
  The names `sh` and `bash` stand for the [POSIX shell](installation.md#posix-shell-on-windows).
  Any other name is looked up on the `PATH` of the step.

A script whose name ends in `.py` runs with the Python that runs StepUp, on every platform.

### Shell Commands

A shell command runs in the POSIX shell that StepUp finds,
which is usually the bash of Git for Windows.
That bash behaves differently from bash on Linux in a few ways:

- **It converts arguments that look like POSIX paths to Windows paths**
  when it starts a program that is not part of Git or MSYS2, such as `python.exe`.
  For example, `/tmp/x` becomes `C:/Users/me/AppData/Local/Temp/x`
  and `--prefix=/usr` becomes `--prefix=C:/Program Files/Git/usr`.
  Relative paths and paths that start with a drive letter stay as they are,
  so the paths of StepUp are not affected.
  This conversion is what allows the result of e.g. `mktemp -d` to be passed to a Windows program.
  To pass such an argument unchanged, set `MSYS_NO_PATHCONV=1` for that one command,
  e.g. `MSYS_NO_PATHCONV=1 ./tool.exe /opt`.
- **It drops every carriage return from a command**, also inside quotes.
  A shell command with a literal `\r` therefore does something else than on Linux and macOS.
- **It has no `python3`.**
  A virtual environment on Windows has a `python.exe` and no `python3.exe`.
  StepUp starts a script with a `python3` shebang line with its own Python,
  but when a shell starts the same script, e.g. in the shell step `./work.py > out.txt`,
  the shell looks for `python3` on `PATH` and fails.
  StepUp does not provide this program, because what a shell command needs is up to you.
  The following lines, run in Git Bash with the virtual environment activated,
  create a wrapper named `python3` in the directory of the environment's `python.exe`:

    ```bash
    bindir="$(dirname "$(type -P python)")"
    printf '#!/bin/sh\nexec python "$@"\n' > "${bindir}/python3"
    ```

    The wrapper runs the first `python` on `PATH`,
    which is that of the virtual environment while it is activated.
    Any other directory on the `PATH` of `stepup build` will do as well.

### Stopping a Build

A Windows console has no signals as Linux and macOS know them,
which changes how a build is stopped:

- Ctrl-C and Ctrl-Break abort a build, Ctrl-Z does not suspend it,
  and the keyboard commands need a Windows console.
- A step that is interrupted ends at once, without cleaning up:
  a Python step gets no `KeyboardInterrupt`, and a `trap` in a shell step does not run.
- Processes that a step leaves running end together with `stepup build`.
- When `stepup build` is killed, everything it started ends at once.

The details are in
[Using StepUp in a Windows Console](reference/interactive.md#using-stepup-in-a-windows-console).

### Files

- **A file that another program holds open cannot be overwritten or removed.**
  This is often the case for an output that is shown in a viewer, e.g. a PDF file.
  A step that writes such a file then fails,
  and so does the removal of an output that is no longer needed.
  Close the file, or use a viewer that does not hold the files it shows open.
- **Paths are limited to 260 characters**, unless long paths are enabled in Windows,
  which takes administrator rights.
  This limit includes the directory of the project.
  Even with long paths enabled, a program that a step runs may not support them.
- **Windows has no permission bits.**
  Every file counts as executable, so a script needs no `chmod +x`,
  but it still needs its shebang line.
- **Creating a symbolic link takes a privilege**,
  which only administrators have by default,
  and every user when the Developer Mode of Windows is on.

### Output and Encodings

- **A Python step on Python 3.14 and older writes its output in the ANSI code page**,
  unless it is configured otherwise.
  StepUp reads the output of every step as UTF-8 and drops what is not valid UTF-8,
  so non-ASCII characters may be missing from the output that StepUp shows.
  Set the environment variable `PYTHONUTF8=1` before you start `stepup build`,
  so that Python steps write UTF-8.
  Python 3.15 writes UTF-8 by default.
- **The output of the `stepup` command is UTF-8 with `\n` line endings**
  when it is redirected to a file or a pipe.
  The PowerShell 5.1 that comes with Windows decodes the output of every program
  with `[Console]::OutputEncoding` and writes redirected output in UTF-16,
  so `stepup build > out.txt` gives a garbled file there.
  Redirect in `cmd.exe`, in Git Bash or in PowerShell 7.4 or later instead,
  or set `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8` in PowerShell 5.1,
  which gets the characters right in a file that is still UTF-16.

[posix-shell]: installation.md#posix-shell-on-windows
