# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Translate a command into the `args` of `subprocess.Popen`, on every platform.

On POSIX, the kernel does most of the work:
it finds a program on `PATH`, resolves a relative program in the working directory of the child
and runs a script with the interpreter named on its shebang line.
Windows does none of that,
and it hands a child one command line instead of a list of arguments,
which every program splits by rules of its own.
This module fills in what is missing, so that a command means the same on every platform:

- A shell command runs as `[shell, "-c", command]`, with the POSIX shell of `find_shell`,
  never with `cmd.exe`.
- A program without a directory is looked up on the `PATH` of the child, and nowhere else.
- A program with a directory is resolved in the working directory of the child.
- A script runs with the interpreter of its shebang line.
- The arguments reach the program unchanged,
  also when it is a batch file or built on the Cygwin or MSYS2 runtime.
"""

import errno
import functools
import os
import shlex
import shutil
import sys
from collections.abc import Iterator, Mapping, Sequence

from .exceptions import ConfigError
from .path import Path, StrPath

if sys.platform == "win32":
    import winreg

__all__ = ("find_shell", "popen_args")


POSIX_SHELL = "/bin/sh"
"""The shell of POSIX systems, which is also what `subprocess` runs there with `shell=True`."""

SHELL_DOCS_URL = "https://reproducible-reporting.github.io/stepup-core/installation/"
"""Where the documentation explains how to install a POSIX shell on Windows."""

DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"
"""The extensions of programs when the environment has no `PATHEXT`, as in `cmd.exe`."""

BATCH_EXTENSIONS = (".bat", ".cmd")
"""The extensions of batch files, which Windows runs with `cmd.exe`."""

CYGWIN_RUNTIMES = ("msys-2.0.dll", "cygwin1.dll")
"""The libraries that sit next to the programs built on the MSYS2 or Cygwin runtime."""

GIT_REGISTRY_KEY = r"SOFTWARE\GitForWindows"
"""The registry key in which Git for Windows records its `InstallPath`."""

SHEBANG_SIZE = 1024
"""The number of bytes read from a script to find its shebang line."""

BATCH_UNQUOTED = frozenset(r"#$*+-./:?@\_")
"""The ASCII symbols that `cmd.exe` leaves alone in an argument without quotes."""


#
# Shell discovery
#


@functools.cache
def find_shell() -> Path:
    """Find the POSIX shell that runs shell commands and shell scripts.

    The setting `STEPUP_SHELL` names the shell, as a path or as a name to look up on `PATH`.
    Without it, the shell is `/bin/sh` on POSIX.
    On Windows, where no shell is part of the system, it is the first of:
    `sh` on `PATH` outside the Windows directory (which holds the launcher of WSL),
    `bin/sh.exe` of the Git installation whose `git` is on `PATH`,
    and `bin/sh.exe` of the Git for Windows installation recorded in the registry.

    A shell that was found is remembered for the lifetime of the process.

    Returns
    -------
    shell
        The absolute path of the shell.

    Raises
    ------
    ConfigError
        If `STEPUP_SHELL` does not name a program, or if no shell is found on Windows.
        The message lists what was tried.
    """
    setting = os.environ.get("STEPUP_SHELL", "")
    if setting:
        found = shutil.which(setting)
        if found is None:
            raise ConfigError(f"STEPUP_SHELL does not name an executable program: {setting}")
        return Path(found).absolute()
    if sys.platform != "win32":
        return Path(POSIX_SHELL)
    return _find_windows_shell(
        os.environ.get("PATH", ""),
        os.environ.get("PATHEXT", ""),
        os.environ.get("SYSTEMROOT", ""),
    )


def _find_windows_shell(path_var: str, pathext_var: str, windows_dir: str) -> Path:
    """Search for a POSIX shell in the places where Windows users have one.

    Parameters
    ----------
    path_var, pathext_var
        The values of `PATH` and `PATHEXT` to search with.
    windows_dir
        The Windows directory, in which a shell is never accepted,
        or an empty string to accept a shell anywhere.

    Returns
    -------
    shell
        The absolute path of the shell.

    Raises
    ------
    ConfigError
        If the search fails.
    """
    tried = ["STEPUP_SHELL is not set"]
    cwd = Path.cwd()

    on_path = False
    for candidate in _iter_on_path("sh", path_var, pathext_var, cwd):
        on_path = True
        if windows_dir and _is_inside(candidate, Path(windows_dir)):
            tried.append(f"{candidate} is ignored, because it is in the Windows directory")
            continue
        return candidate
    if not on_path:
        tried.append("sh is not on PATH")

    git = next(_iter_on_path("git", path_var, pathext_var, cwd), None)
    if git is None:
        tried.append("git is not on PATH")
    else:
        # Git for Windows has `git.exe` in `cmd`, in `bin` and in `mingw64/bin`.
        for root in (git.parent.parent, git.parent.parent.parent):
            candidate = root / "bin" / "sh.exe"
            if candidate.is_file():
                return candidate
            tried.append(f"{candidate} does not exist, for {git} on PATH")

    roots = _registry_git_roots()
    if len(roots) == 0:
        tried.append(f"the registry has no InstallPath under {GIT_REGISTRY_KEY}")
    for root in roots:
        candidate = root / "bin" / "sh.exe"
        if candidate.is_file():
            return candidate.absolute()
        tried.append(f"{candidate} does not exist, for the InstallPath in the registry")

    lines = [
        "No POSIX shell was found, which a shell command or a shell script needs:",
        *(f"- {line}" for line in tried),
        "Install Git for Windows or set STEPUP_SHELL to the path of a POSIX shell.",
        f"See {SHELL_DOCS_URL}",
    ]
    raise ConfigError("\n".join(lines))


def _registry_git_roots() -> list[Path]:
    """Return the directories in which Git for Windows is installed, according to the registry.

    Returns
    -------
    roots
        The installation of the current user first, followed by the one of the machine.
        The list is empty when there is none, and always on other platforms than Windows.
    """
    roots = []
    if sys.platform == "win32":
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, GIT_REGISTRY_KEY) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallPath")
            except OSError:
                continue
            if isinstance(value, str) and value:
                roots.append(Path(value))
    return roots


def _is_inside(path: Path, directory: Path) -> bool:
    """Whether a path lies in a directory, comparing the way the file system of Windows does."""
    inner = path.absolute().normcase()
    outer = directory.absolute().normcase()
    return inner.startswith(outer / "")


#
# Program lookup on Windows
#


def _split_pathext(pathext_var: str) -> list[str]:
    """Return the extensions of a `PATHEXT` value, or the default ones when it is empty."""
    # The separator is that of Windows, whatever the platform, because so is the variable.
    return [ext for ext in (pathext_var or DEFAULT_PATHEXT).split(";") if ext]


def _has_program_extension(name: str, extensions: Sequence[str]) -> bool:
    """Whether a file name ends in one of the extensions of programs, ignoring case."""
    lower = name.lower()
    return any(lower.endswith(ext.lower()) for ext in extensions)


def _iter_on_path(name: str, path_var: str, pathext_var: str, cwd: Path) -> Iterator[Path]:
    """Iterate over the programs that a name without directory stands for.

    This differs from `shutil.which` and from Windows itself,
    which both try the current directory before `PATH`.
    A program in the project would then replace one with the same name on `PATH`,
    which POSIX never does.

    Parameters
    ----------
    name
        The name of the program, with or without extension.
    path_var, pathext_var
        The values of `PATH` and `PATHEXT` to search with.
    cwd
        The directory in which relative directories on `PATH` are resolved.

    Yields
    ------
    program
        Each existing file with a program extension, in the order of `PATH`.
    """
    extensions = _split_pathext(pathext_var)
    names = [name] if _has_program_extension(name, extensions) else []
    names.extend(name + ext for ext in extensions)
    seen = set()
    for dirname in path_var.split(os.pathsep):
        if dirname == "" or dirname in seen:
            continue
        seen.add(dirname)
        directory = cwd / dirname
        for candidate_name in names:
            candidate = directory / candidate_name
            if candidate.is_file():
                yield candidate.absolute()


def _resolve_program(program: str, env: Mapping[str, str], cwd: Path) -> Path:
    """Find the file that the first word of a command stands for.

    Parameters
    ----------
    program
        The first word of the command.
        Without a directory, it is looked up on `PATH`.
        With a directory, it is a path in the working directory of the child,
        to which an extension of `PATHEXT` is added when it does not exist as given.
    env
        The environment of the child.
    cwd
        The working directory of the child.

    Returns
    -------
    path
        The absolute path of the program.

    Raises
    ------
    FileNotFoundError
        If there is no such program.
    """
    path_var = env.get("PATH", "")
    pathext_var = env.get("PATHEXT", "")
    path = Path(program)
    if path == path.name:
        found = next(_iter_on_path(program, path_var, pathext_var, cwd), None)
        if found is None:
            raise FileNotFoundError(errno.ENOENT, "Command not found on PATH", program)
        return found
    path = cwd / path
    for candidate in (path, *(path + ext for ext in _split_pathext(pathext_var))):
        if candidate.is_file():
            return candidate.absolute()
    raise FileNotFoundError(errno.ENOENT, "No such program", str(path))


#
# Shebang lines
#


def _read_shebang(script: Path) -> str | None:
    """Return what follows `#!` on the first line of a script, or `None` if it has no such line."""
    with open(script, "rb") as fh:
        head = fh.read(SHEBANG_SIZE)
    if not head.startswith(b"#!"):
        return None
    try:
        return head[2:].split(b"\n", 1)[0].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def _parse_shebang(shebang: str) -> list[str]:
    """Split a shebang line into the name of the interpreter and its arguments.

    The interpreter is known by its name alone, without the directory it has on POSIX,
    and `env` is replaced by the program that it would start.
    As on Linux, everything after the interpreter is a single argument,
    except with `env -S`, whose argument is split in words with the quoting rules of a shell.
    The other options of `env` and its `NAME=VALUE` assignments are not supported.

    Parameters
    ----------
    shebang
        The first line of a script, without the leading `#!`.

    Returns
    -------
    words
        The name of the interpreter, followed by its arguments.

    Raises
    ------
    ValueError
        If the line names no interpreter, or uses a feature of `env` that is not supported.
    """
    parts = shebang.split(None, 1)
    if len(parts) == 0:
        raise ValueError("The shebang line names no interpreter.")
    name = Path(parts[0]).name
    rest = parts[1].strip() if len(parts) == 2 else ""
    if name != "env":
        return [name, rest] if rest else [name]
    if rest.startswith("--split-string"):
        words = shlex.split(rest[len("--split-string") :].removeprefix("="))
    elif rest.startswith("-S"):
        words = shlex.split(rest[2:])
    else:
        words = [rest] if rest else []
    if len(words) == 0:
        raise ValueError("The shebang line names no interpreter after env.")
    if words[0].startswith("-") or "=" in words[0]:
        raise ValueError(f"The shebang line uses an unsupported feature of env: {words[0]}")
    return words


def _resolve_interpreter(name: str, env: Mapping[str, str], cwd: Path) -> Path:
    """Find the program that stands for the interpreter of a shebang line.

    The interpreters `sh` and `bash` are those of `find_shell`,
    because a search on `PATH` could end up in WSL.
    The interpreters `python` and `python3` are the running Python,
    because a virtual environment on Windows has no `python3`,
    and because `PATH` may hold stubs with these names that open the Microsoft Store.
    Any other interpreter is looked up on `PATH`.

    Parameters
    ----------
    name
        The name of the interpreter, without directory.
    env
        The environment of the child.
    cwd
        The working directory of the child.

    Returns
    -------
    interpreter
        The absolute path of the interpreter.

    Raises
    ------
    ConfigError
        If the interpreter is a shell and none is found.
    FileNotFoundError
        If the interpreter is not on `PATH`.
    """
    if name == "sh":
        return find_shell()
    if name == "bash":
        # A shell named `sh` may be bash in POSIX mode, in which some features of bash are off.
        shell = find_shell()
        bash = shell.parent / f"bash{shell.suffix}"
        return bash if bash.is_file() else shell
    if name in ("python", "python3"):
        return Path(sys.executable)
    found = next(_iter_on_path(name, env.get("PATH", ""), env.get("PATHEXT", ""), cwd), None)
    if found is None:
        raise FileNotFoundError(errno.ENOENT, "Interpreter of the script not found on PATH", name)
    return found


#
# Command lines
#


@functools.cache
def _uses_cygwin_argv(program: Path) -> bool:
    """Whether a program splits its command line with the rules of the Cygwin runtime.

    Programs of Cygwin, MSYS2 and Git for Windows do so when a native program starts them.
    Unlike the C runtime of Microsoft, for which `subprocess` quotes a list of arguments,
    they treat single quotes as quotes, collapse double backslashes,
    read arguments from a file after `@`, and expand wildcards, braces and a tilde.
    Such programs are recognized by the runtime library in their directory.
    The launchers `bin/sh.exe` and `bin/bash.exe` of Git for Windows are native programs
    that pass their command line on unchanged to the shell in `usr/bin`.
    """
    bindir = program.parent
    if any((bindir / runtime).is_file() for runtime in CYGWIN_RUNTIMES):
        return True
    usrbin = bindir.parent / "usr" / "bin"
    return program.stem.lower() in ("sh", "bash") and any(
        (usrbin / runtime).is_file() for runtime in CYGWIN_RUNTIMES
    )


def _cygwin_command_line(argv: Sequence[str]) -> str:
    """Build a command line that the Cygwin runtime splits into exactly these arguments.

    Every argument is put between double quotes,
    in which the runtime only gives a meaning to a backslash and a double quote.
    This was tested with random arguments on the MSYS2 runtime of Git for Windows 2.56.

    Parameters
    ----------
    argv
        The program, by its absolute path, followed by its arguments.

    Returns
    -------
    command_line
        The command line to pass to `subprocess.Popen` as a string.
    """
    # The program is not an argument to the runtime, and a file name cannot hold a double quote.
    words = [f'"{argv[0]}"']
    words.extend(
        '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' for arg in map(str, argv[1:])
    )
    return " ".join(words)


def _quote_batch_arg(arg: str) -> str:
    """Quote one argument of a batch file, so that `cmd.exe` passes it on unchanged.

    This follows the standard library of Rust, as it quotes such arguments since CVE-2024-24576,
    including its trick to keep a percent sign from starting a variable:
    `%cd:~,%` is a substring of zero characters, which separates the percent sign before it
    from whatever follows.
    """
    quote = arg == "" or arg.endswith("\\")
    for char in arg:
        if char.isascii():
            quote = quote or not (char.isalnum() or char in BATCH_UNQUOTED)
        else:
            quote = quote or not char.isprintable()
    result = ['"'] if quote else []
    nbackslash = 0
    for char in arg:
        if char == "\\":
            nbackslash += 1
        else:
            if char == '"':
                # Backslashes before a double quote are doubled, and so is the quote itself.
                result.append("\\" * nbackslash)
                result.append('"')
            elif char == "%":
                result.append("%%cd:~,")
            nbackslash = 0
        result.append(char)
    if quote:
        result.append("\\" * nbackslash)
        result.append('"')
    return "".join(result)


def _batch_command_line(script: Path, args: Sequence[str], env: Mapping[str, str]) -> str:
    """Build the command line that runs a batch file with `cmd.exe`.

    Windows starts `cmd.exe` by itself when it is given a batch file,
    but then with the command line that `subprocess` made for a regular program,
    in which `cmd.exe` gives a meaning of its own to `&`, `|`, `%` and other characters,
    and which fails for a batch file in a directory with a space when an argument is quoted.

    Parameters
    ----------
    script
        The absolute path of the batch file.
    args
        The arguments of the batch file.
    env
        The environment of the child, which locates the Windows directory.

    Returns
    -------
    command_line
        The command line to pass to `subprocess.Popen` as a string.

    Raises
    ------
    OSError
        If an argument holds a character that cannot be passed to a batch file.
    """
    for arg in args:
        if any(char in arg for char in "\r\n\0"):
            raise OSError(
                errno.EINVAL, "A batch file cannot take an argument with a line break", arg
            )
    # Windows spells this variable `SystemRoot`, and Python puts all names in upper case.
    cmd_exe = Path(env.get("SYSTEMROOT", "C:/Windows")) / "System32" / "cmd.exe"
    words = [f'"{_native(script)}"', *(_quote_batch_arg(arg) for arg in args)]
    # The outer quotes are removed by `cmd.exe`, which then sees the quoted words as they are.
    return f'"{_native(cmd_exe)}" /e:ON /v:OFF /d /c "{" ".join(words)}"'


def _native(path: Path) -> str:
    """Spell a path with backslashes, for `cmd.exe`, which takes a slash for an option."""
    return str(path).replace("/", "\\")


def _program_args(argv: Sequence[str]) -> str | list[str]:
    """Return the arguments in the form that the program of `argv` splits correctly."""
    if _uses_cygwin_argv(Path(argv[0])):
        return _cygwin_command_line(argv)
    return [str(arg) for arg in argv]


def _windows_args(argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> str | list[str]:
    """Translate a program and its arguments into what Windows can start.

    Parameters
    ----------
    argv
        The program, as written in the command, followed by its arguments.
    env
        The environment of the child.
    cwd
        The working directory of the child.

    Returns
    -------
    args
        The `args` of `subprocess.Popen`.

    Raises
    ------
    ConfigError
        If the program is a shell script and no shell is found.
    OSError
        If the program or the interpreter of its shebang line cannot be found or started.
    """
    program = _resolve_program(argv[0], env, cwd)
    args = list(argv[1:])
    if program.suffix.lower() in BATCH_EXTENSIONS:
        return _batch_command_line(program, args, env)
    if not _has_program_extension(program.name, _split_pathext(env.get("PATHEXT", ""))):
        shebang = _read_shebang(program)
        if shebang is not None:
            try:
                words = _parse_shebang(shebang)
            except ValueError as exc:
                raise OSError(errno.ENOEXEC, str(exc), str(program)) from exc
            interpreter = _resolve_interpreter(words[0], env, cwd)
            # The script keeps the spelling of the command, as on POSIX,
            # which the interpreter resolves in the working directory of the child.
            return _program_args([interpreter, *words[1:], argv[0], *args])
    return _program_args([program, *args])


#
# Entry point
#


def popen_args(
    command: str | Sequence[str], *, shell: bool, env: Mapping[str, str], cwd: StrPath
) -> str | list[str]:
    """Translate a command into the `args` of `subprocess.Popen`, to use with `shell=False`.

    Parameters
    ----------
    command
        With `shell`, the command line for the shell.
        Otherwise, the program followed by its arguments.
    shell
        Whether a POSIX shell runs the command, see `find_shell`.
    env
        The environment of the child.
    cwd
        The working directory of the child.

    Returns
    -------
    args
        A list of arguments, or a complete command line on Windows
        when the program does not split its command line the way `subprocess` assumes.
        On POSIX, the result is the unchanged list of arguments without `shell`.

    Raises
    ------
    ConfigError
        If the command needs a POSIX shell and none is found.
    OSError
        If Windows cannot start the command:
        the program or the interpreter of a script is not found,
        a shebang line is not supported,
        or a batch file would get an argument that it cannot take.
    """
    if shell:
        argv = [find_shell(), "-c", command]
        return _program_args(argv) if sys.platform == "win32" else [str(arg) for arg in argv]
    if sys.platform == "win32":
        return _windows_args(command, env, Path(cwd))
    return list(command)
