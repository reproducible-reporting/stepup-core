# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Unit tests for stepup.core.popen

Most of the Windows logic works on any platform when it is given a `PATH` and a `PATHEXT`,
so it is tested everywhere, with lower-case extensions because other file systems tell case apart.
The tests that start a program with the result can only run on Windows.
"""

import json
import os
import subprocess
import sys

import pytest

import stepup.core.popen as popen_mod
from stepup.core.exceptions import ConfigError
from stepup.core.path import Path
from stepup.core.popen import (
    POSIX_SHELL,
    SHELL_DOCS_URL,
    _batch_command_line,
    _cygwin_command_line,
    _find_windows_shell,
    _iter_on_path,
    _parse_shebang,
    _quote_batch_arg,
    _read_shebang,
    _resolve_interpreter,
    _resolve_program,
    _uses_cygwin_argv,
    _windows_args,
    find_shell,
    popen_args,
)

EXE = ".exe" if sys.platform == "win32" else ""
"""The extension that makes `shutil.which` accept a file as a program on this platform."""

PATHEXT = ".exe;.cmd"
"""A `PATHEXT` for tests of the lookup, in lower case to match the files the tests create."""

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")


def _touch(path: Path, text: str = "") -> Path:
    """Create a file and its parent directories, and make it executable."""
    path.parent.makedirs_p()
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


#
# Shell discovery
#


@posix_only
def test_find_shell_posix_default(monkeypatch, fresh_shell_cache):
    monkeypatch.delenv("STEPUP_SHELL", raising=False)
    assert find_shell() == POSIX_SHELL


@windows_only
def test_find_shell_windows_default(monkeypatch, fresh_shell_cache):
    """The shell that is found has the usual tools on its `PATH`, and is not the one of WSL."""
    monkeypatch.delenv("STEPUP_SHELL", raising=False)
    shell = find_shell()
    assert shell.is_file()
    assert shell.isabs()
    assert not shell.normcase().startswith(Path(os.environ["SYSTEMROOT"]).normcase() / "")
    completed = subprocess.run(
        popen_args("type cp sed grep mktemp && uname -s", shell=True, env=os.environ, cwd="."),
        capture_output=True,
        check=True,
        text=True,
    )
    assert "Linux" not in completed.stdout


def test_find_shell_setting_with_path(monkeypatch, path_tmp, fresh_shell_cache):
    shell = _touch(path_tmp / f"myshell{EXE}")
    monkeypatch.setenv("STEPUP_SHELL", shell)
    assert find_shell() == shell.absolute()


def test_find_shell_setting_with_name(monkeypatch, path_tmp, fresh_shell_cache):
    shell = _touch(path_tmp / "bin" / f"myshell{EXE}")
    monkeypatch.setenv("PATH", shell.parent)
    monkeypatch.setenv("STEPUP_SHELL", "myshell")
    assert find_shell().normcase() == shell.absolute().normcase()


def test_find_shell_empty_setting_is_no_setting(monkeypatch, fresh_shell_cache):
    monkeypatch.delenv("STEPUP_SHELL", raising=False)
    default = find_shell()
    find_shell.cache_clear()
    monkeypatch.setenv("STEPUP_SHELL", "")
    assert find_shell() == default


def test_find_shell_setting_without_program(monkeypatch, path_tmp, fresh_shell_cache):
    monkeypatch.setenv("STEPUP_SHELL", path_tmp / "missing")
    with pytest.raises(ConfigError, match="STEPUP_SHELL does not name an executable program"):
        find_shell()


def test_find_shell_remembers_a_shell_but_not_a_failure(monkeypatch, path_tmp, fresh_shell_cache):
    monkeypatch.setenv("STEPUP_SHELL", path_tmp / f"later{EXE}")
    with pytest.raises(ConfigError):
        find_shell()
    # The failure is not remembered, so a shell that appears later is found.
    shell = _touch(path_tmp / f"later{EXE}")
    assert find_shell() == shell.absolute()
    # The shell is remembered, whatever happens to the setting.
    monkeypatch.setenv("STEPUP_SHELL", path_tmp / "missing")
    assert find_shell() == shell.absolute()


def test_find_windows_shell_on_path(path_tmp, monkeypatch):
    monkeypatch.setattr(popen_mod, "_registry_git_roots", list)
    _touch(path_tmp / "first" / "other.exe")
    shell = _touch(path_tmp / "msys" / "usr" / "bin" / "sh.exe")
    path_var = os.pathsep.join([path_tmp / "first", shell.parent])
    assert _find_windows_shell(path_var, PATHEXT, path_tmp / "Windows") == shell


def test_find_windows_shell_ignores_the_windows_directory(path_tmp, monkeypatch):
    """The `sh` or `bash` in the Windows directory starts WSL, which runs Linux programs."""
    monkeypatch.setattr(popen_mod, "_registry_git_roots", list)
    wsl = _touch(path_tmp / "Windows" / "System32" / "sh.exe")
    shell = _touch(path_tmp / "msys" / "usr" / "bin" / "sh.exe")
    path_var = os.pathsep.join([wsl.parent, shell.parent])
    assert _find_windows_shell(path_var, PATHEXT, path_tmp / "Windows") == shell
    with pytest.raises(ConfigError, match="is ignored, because it is in the Windows directory"):
        _find_windows_shell(wsl.parent, PATHEXT, path_tmp / "Windows")


@pytest.mark.parametrize("git_dir", ["cmd", "mingw64/bin"])
def test_find_windows_shell_next_to_git(git_dir: str, path_tmp, monkeypatch):
    """Git for Windows puts `git` on `PATH`, but not its shell."""
    monkeypatch.setattr(popen_mod, "_registry_git_roots", list)
    root = path_tmp / "Program Files" / "Git"
    git = _touch(root / git_dir / "git.exe")
    # The shell in `usr/bin` must not be chosen: it runs without the tools on its `PATH`.
    _touch(root / "usr" / "bin" / "sh.exe")
    shell = _touch(root / "bin" / "sh.exe")
    assert _find_windows_shell(git.parent, PATHEXT, "") == shell


def test_find_windows_shell_in_the_registry(path_tmp, monkeypatch):
    user_root = path_tmp / "user" / "Git"
    machine_root = path_tmp / "machine" / "Git"
    monkeypatch.setattr(popen_mod, "_registry_git_roots", lambda: [user_root, machine_root])
    shell = _touch(machine_root / "bin" / "sh.exe")
    assert _find_windows_shell("", PATHEXT, "") == shell.absolute()
    user_shell = _touch(user_root / "bin" / "sh.exe")
    assert _find_windows_shell("", PATHEXT, "") == user_shell.absolute()


def test_find_windows_shell_lists_what_was_tried(path_tmp, monkeypatch):
    monkeypatch.setattr(popen_mod, "_registry_git_roots", lambda: [path_tmp / "gone"])
    git = _touch(path_tmp / "scoop" / "shims" / "git.exe")
    with pytest.raises(ConfigError) as excinfo:
        _find_windows_shell(git.parent, PATHEXT, "")
    message = str(excinfo.value)
    assert "STEPUP_SHELL is not set" in message
    assert "sh is not on PATH" in message
    assert f"{path_tmp / 'scoop' / 'bin' / 'sh.exe'} does not exist" in message
    assert f"{path_tmp / 'gone' / 'bin' / 'sh.exe'} does not exist" in message
    assert "Install Git for Windows or set STEPUP_SHELL" in message
    assert SHELL_DOCS_URL in message


def test_find_windows_shell_without_git(path_tmp, monkeypatch):
    monkeypatch.setattr(popen_mod, "_registry_git_roots", list)
    with pytest.raises(ConfigError) as excinfo:
        _find_windows_shell(path_tmp, PATHEXT, "")
    message = str(excinfo.value)
    assert "git is not on PATH" in message
    assert "the registry has no InstallPath" in message


@posix_only
def test_registry_git_roots_outside_windows():
    assert popen_mod._registry_git_roots() == []


#
# Program lookup
#


def test_iter_on_path_follows_the_order_of_path(path_tmp):
    first = _touch(path_tmp / "first" / "tool.exe")
    second = _touch(path_tmp / "second" / "tool.exe")
    path_var = os.pathsep.join([second.parent, first.parent, second.parent])
    assert list(_iter_on_path("tool", path_var, PATHEXT, path_tmp)) == [second, first]


def test_iter_on_path_follows_the_order_of_pathext(path_tmp):
    cmd = _touch(path_tmp / "bin" / "tool.cmd")
    exe = _touch(path_tmp / "bin" / "tool.exe")
    assert list(_iter_on_path("tool", cmd.parent, ".exe;.cmd", path_tmp)) == [exe, cmd]
    assert list(_iter_on_path("tool", cmd.parent, ".cmd;.exe", path_tmp)) == [cmd, exe]
    assert list(_iter_on_path("tool.cmd", cmd.parent, ".exe;.cmd", path_tmp)) == [cmd]


def test_iter_on_path_needs_a_program_extension(path_tmp):
    """A file without one of the extensions is not a program, as in `cmd.exe`."""
    _touch(path_tmp / "bin" / "script.sh")
    _touch(path_tmp / "bin" / "tool")
    assert list(_iter_on_path("script.sh", path_tmp / "bin", PATHEXT, path_tmp)) == []
    assert list(_iter_on_path("tool", path_tmp / "bin", PATHEXT, path_tmp)) == []


def test_iter_on_path_skips_the_current_directory(path_tmp):
    """Unlike Windows and `shutil.which`, the lookup only uses `PATH`."""
    _touch(path_tmp / "work" / "tool.exe")
    on_path = _touch(path_tmp / "bin" / "tool.exe")
    assert list(_iter_on_path("tool", on_path.parent, PATHEXT, path_tmp / "work")) == [on_path]
    assert list(_iter_on_path("tool", "", PATHEXT, path_tmp / "work")) == []


def test_iter_on_path_resolves_a_relative_directory(path_tmp):
    tool = _touch(path_tmp / "work" / "bin" / "tool.exe")
    assert list(_iter_on_path("tool", "bin", PATHEXT, path_tmp / "work")) == [tool]


def test_iter_on_path_default_pathext(path_tmp):
    tool = _touch(path_tmp / "bin" / "tool.EXE")
    assert list(_iter_on_path("tool", tool.parent, "", path_tmp)) == [tool]


def test_resolve_program_on_path(path_tmp):
    tool = _touch(path_tmp / "bin" / "tool.cmd")
    _touch(path_tmp / "work" / "tool.exe")
    env = {"PATH": tool.parent, "PATHEXT": PATHEXT}
    assert _resolve_program("tool", env, path_tmp / "work") == tool
    with pytest.raises(FileNotFoundError, match="Command not found on PATH"):
        _resolve_program("other", env, path_tmp / "work")
    with pytest.raises(FileNotFoundError, match="Command not found on PATH"):
        _resolve_program("tool", {}, path_tmp / "work")


def test_resolve_program_with_directory(path_tmp):
    """A program with a directory is relative to the working directory of the step."""
    local = _touch(path_tmp / "work" / "tool.cmd")
    script = _touch(path_tmp / "work" / "sub" / "script.sh")
    _touch(path_tmp / "bin" / "tool.exe")
    env = {"PATH": path_tmp / "bin", "PATHEXT": PATHEXT}
    workdir = path_tmp / "work"
    assert _resolve_program("./tool", env, workdir) == local
    assert _resolve_program("./tool.cmd", env, workdir) == local
    assert _resolve_program("sub/script.sh", env, workdir) == script
    assert _resolve_program(script, env, path_tmp) == script
    with pytest.raises(FileNotFoundError, match="No such program"):
        _resolve_program("./script.sh", env, workdir)


#
# Shebang lines
#


@pytest.mark.parametrize(
    ("shebang", "words"),
    [
        ("/bin/sh", ["sh"]),
        ("/bin/bash -e", ["bash", "-e"]),
        # Everything after the interpreter is one argument, as on Linux.
        ("/bin/bash -e -u", ["bash", "-e -u"]),
        ("/usr/bin/env python3", ["python3"]),
        ("/usr/bin/env  sh", ["sh"]),
        ("/usr/bin/env -S bash -x", ["bash", "-x"]),
        ("/usr/bin/env -S bash -e -u", ["bash", "-e", "-u"]),
        ("/usr/bin/env -Sawk -f", ["awk", "-f"]),
        ("/usr/bin/env --split-string=python3 -u", ["python3", "-u"]),
        ("/usr/bin/env --split-string python3 -u", ["python3", "-u"]),
        ("/usr/bin/env -S 'my prog' \"an arg\" plain", ["my prog", "an arg", "plain"]),
        ("/usr/local/bin/python3.12", ["python3.12"]),
        ("C:/tools/bin/tool", ["tool"]),
    ],
)
def test_parse_shebang(shebang: str, words: list[str]):
    assert _parse_shebang(shebang) == words


@pytest.mark.parametrize(
    ("shebang", "message"),
    [
        ("", "names no interpreter"),
        ("/usr/bin/env", "names no interpreter after env"),
        ("/usr/bin/env -S", "names no interpreter after env"),
        ("/usr/bin/env -i sh", "unsupported feature of env: -i sh"),
        ("/usr/bin/env -S -i sh", "unsupported feature of env: -i"),
        ("/usr/bin/env -S FOO=1 sh", "unsupported feature of env: FOO=1"),
    ],
)
def test_parse_shebang_invalid(shebang: str, message: str):
    with pytest.raises(ValueError, match=message):
        _parse_shebang(shebang)


@pytest.mark.parametrize(
    ("head", "shebang"),
    [
        (b"#!/bin/sh\necho hello\n", "/bin/sh"),
        (b"#! /usr/bin/env -S bash -x  \n", "/usr/bin/env -S bash -x"),
        # A script that was saved with the line endings of Windows.
        (b"#!/bin/sh\r\necho hello\r\n", "/bin/sh"),
        (b"#!/bin/sh", "/bin/sh"),
        (b"echo hello\n", None),
        (b"", None),
        (b"MZ\x90\x00", None),
        (b"#!\xff\xfe\n", None),
    ],
)
def test_read_shebang(head: bytes, shebang: str | None, path_tmp):
    script = path_tmp / "script"
    script.write_bytes(head)
    assert _read_shebang(script) == shebang


@pytest.mark.parametrize("name", ["python", "python3"])
def test_resolve_interpreter_python(name: str, path_tmp):
    """Both names stand for the running Python, whatever `PATH` holds."""
    _touch(path_tmp / "bin" / f"{name}.exe")
    env = {"PATH": path_tmp / "bin", "PATHEXT": PATHEXT}
    assert _resolve_interpreter(name, env, path_tmp) == Path(sys.executable)


def test_resolve_interpreter_shell(monkeypatch, path_tmp, fresh_shell_cache):
    """The shells are those of `find_shell`, not what `PATH` holds."""
    shell = _touch(path_tmp / "shell" / f"sh{EXE}")
    _touch(path_tmp / "bin" / "sh.exe")
    _touch(path_tmp / "bin" / "bash.exe")
    monkeypatch.setenv("STEPUP_SHELL", shell)
    env = {"PATH": path_tmp / "bin", "PATHEXT": PATHEXT}
    assert _resolve_interpreter("sh", env, path_tmp) == shell
    # Without a `bash` next to the shell, the shell has to do.
    assert _resolve_interpreter("bash", env, path_tmp) == shell
    bash = _touch(path_tmp / "shell" / f"bash{EXE}")
    assert _resolve_interpreter("bash", env, path_tmp) == bash


def test_resolve_interpreter_shell_not_found(monkeypatch, path_tmp, fresh_shell_cache):
    monkeypatch.setenv("STEPUP_SHELL", path_tmp / "missing")
    with pytest.raises(ConfigError):
        _resolve_interpreter("bash", {}, path_tmp)


def test_resolve_interpreter_on_path(path_tmp):
    awk = _touch(path_tmp / "bin" / "awk.exe")
    env = {"PATH": awk.parent, "PATHEXT": PATHEXT}
    assert _resolve_interpreter("awk", env, path_tmp) == awk
    with pytest.raises(FileNotFoundError, match="Interpreter of the script not found on PATH"):
        _resolve_interpreter("perl", env, path_tmp)


#
# Command lines
#


def test_uses_cygwin_argv(path_tmp):
    msys = _touch(path_tmp / "msys64" / "usr" / "bin" / "sh.exe")
    _touch(msys.parent / "msys-2.0.dll")
    assert _uses_cygwin_argv(msys)
    assert _uses_cygwin_argv(msys.parent / "cp.exe")
    cygwin = _touch(path_tmp / "cygwin64" / "bin" / "bash.exe")
    _touch(cygwin.parent / "cygwin1.dll")
    assert _uses_cygwin_argv(cygwin)
    # Programs that MSYS2 builds for Windows itself are not built on its runtime.
    assert not _uses_cygwin_argv(_touch(path_tmp / "msys64" / "ucrt64" / "bin" / "gcc.exe"))
    assert not _uses_cygwin_argv(_touch(path_tmp / "python" / "python.exe"))


def test_uses_cygwin_argv_launchers_of_git(path_tmp):
    """The launchers in `bin` of Git for Windows pass their command line on to the real shell."""
    root = path_tmp / "Git"
    _touch(root / "usr" / "bin" / "msys-2.0.dll")
    assert _uses_cygwin_argv(_touch(root / "bin" / "sh.exe"))
    assert _uses_cygwin_argv(_touch(root / "bin" / "bash.exe"))
    assert not _uses_cygwin_argv(_touch(root / "bin" / "git.exe"))
    assert not _uses_cygwin_argv(_touch(root / "cmd" / "git.exe"))


@pytest.mark.parametrize(
    ("argv", "command_line"),
    [
        (["C:/Git/bin/sh.exe", "-c", "echo hello"], '"C:/Git/bin/sh.exe" "-c" "echo hello"'),
        (
            ["sh", "it's", "a*.txt", "@file", "{a,b}", "~"],
            '"sh" "it\'s" "a*.txt" "@file" "{a,b}" "~"',
        ),
        (["sh", 'say "hi"'], r'"sh" "say \"hi\""'),
        (["sh", r"back\slash", r"two\\", "end\\"], r'"sh" "back\\slash" "two\\\\" "end\\"'),
        (["sh", '\\"'], r'"sh" "\\\""'),
        (["sh", "", "a\nb"], '"sh" "" "a\nb"'),
    ],
)
def test_cygwin_command_line(argv: list[str], command_line: str):
    assert _cygwin_command_line(argv) == command_line


@pytest.mark.parametrize(
    ("arg", "quoted"),
    [
        ("plain", "plain"),
        ("C:/dir/file.txt", "C:/dir/file.txt"),
        (r"C:\dir\file.txt", r"C:\dir\file.txt"),
        ("--opt=1", '"--opt=1"'),
        ("", '""'),
        ("a b", '"a b"'),
        ("a&b", '"a&b"'),
        ("a^b", '"a^b"'),
        ("50%", '"50%%cd:~,%"'),
        ("%PATH%", '"%%cd:~,%PATH%%cd:~,%"'),
        ('say "hi"', '"say ""hi"""'),
        ("C:\\dir\\", '"C:\\dir\\\\"'),
        ('a\\"b', '"a\\\\""b"'),
        ("caf\u00e9", "caf\u00e9"),
        ("a\tb", '"a\tb"'),
    ],
)
def test_quote_batch_arg(arg: str, quoted: str):
    assert _quote_batch_arg(arg) == quoted


def test_batch_command_line():
    env = {"SYSTEMROOT": r"D:\Win"}
    command_line = _batch_command_line(Path("C:/my tools/run.cmd"), ["a b", "x"], env)
    assert command_line == (
        r'"D:\Win\System32\cmd.exe" /e:ON /v:OFF /d /c ""C:\my tools\run.cmd" "a b" x"'
    )


@pytest.mark.parametrize("arg", ["a\nb", "a\rb", "a\0b"])
def test_batch_command_line_refuses_line_breaks(arg: str):
    with pytest.raises(OSError, match="A batch file cannot take an argument with a line break"):
        _batch_command_line(Path("C:/run.cmd"), ["fine", arg], {})


#
# Translation of whole commands
#


def test_windows_args_program(path_tmp):
    tool = _touch(path_tmp / "bin" / "tool.exe")
    env = {"PATH": tool.parent, "PATHEXT": PATHEXT}
    assert _windows_args(["tool", "a b", "c"], env, path_tmp) == [tool, "a b", "c"]


def test_windows_args_cygwin_program(path_tmp):
    cp = _touch(path_tmp / "usr" / "bin" / "cp.exe")
    _touch(cp.parent / "msys-2.0.dll")
    env = {"PATH": cp.parent, "PATHEXT": PATHEXT}
    assert _windows_args(["cp", "a*.txt", "dst"], env, path_tmp) == f'"{cp}" "a*.txt" "dst"'


def test_windows_args_batch_file(path_tmp):
    script = _touch(path_tmp / "work" / "run.cmd")
    env = {"PATH": "", "PATHEXT": PATHEXT, "SYSTEMROOT": "C:/Windows"}
    command_line = _windows_args(["./run", "a&b"], env, path_tmp / "work")
    native_script = str(script).replace("/", "\\")
    assert command_line == (
        f'"C:\\Windows\\System32\\cmd.exe" /e:ON /v:OFF /d /c ""{native_script}" "a&b""'
    )


def test_windows_args_script_with_shebang(monkeypatch, path_tmp, fresh_shell_cache):
    """A script runs with its interpreter, and keeps the spelling it has in the command."""
    shell = _touch(path_tmp / "shell" / f"sh{EXE}")
    monkeypatch.setenv("STEPUP_SHELL", shell)
    workdir = path_tmp / "work"
    _touch(workdir / "sub" / "script.sh", "#!/usr/bin/env -S bash -e -u\necho hello\n")
    _touch(workdir / "tool", "#!/usr/bin/env python3\nprint('hello')\n")
    env = {"PATH": "", "PATHEXT": PATHEXT}
    assert _windows_args(["sub/script.sh", "arg"], env, workdir) == [
        shell,
        "-e",
        "-u",
        "sub/script.sh",
        "arg",
    ]
    assert _windows_args(["./tool"], env, workdir) == [Path(sys.executable), "./tool"]


def test_windows_args_script_with_unsupported_shebang(path_tmp):
    _touch(path_tmp / "script", "#!/usr/bin/env -i sh\n")
    with pytest.raises(OSError, match="unsupported feature of env") as excinfo:
        _windows_args(["./script"], {"PATHEXT": PATHEXT}, path_tmp)
    assert excinfo.value.filename == path_tmp / "script"


def test_windows_args_file_that_is_no_program(path_tmp):
    """Without a shebang line, Windows gets to decide what the file is."""
    data = _touch(path_tmp / "data.bin", "not a script\n")
    assert _windows_args(["./data.bin"], {"PATHEXT": PATHEXT}, path_tmp) == [data]


def test_windows_args_not_found(path_tmp):
    with pytest.raises(FileNotFoundError):
        _windows_args(["missing"], {"PATH": path_tmp, "PATHEXT": PATHEXT}, path_tmp)


@posix_only
def test_popen_args_posix(monkeypatch, fresh_shell_cache):
    monkeypatch.delenv("STEPUP_SHELL", raising=False)
    assert popen_args("echo 'a b' | cat", shell=True, env={}, cwd=".") == [
        "/bin/sh",
        "-c",
        "echo 'a b' | cat",
    ]
    command = ["./script.sh", "a b"]
    args = popen_args(command, shell=False, env={}, cwd="work")
    assert args == command
    assert args is not command


def test_popen_args_shell_setting(monkeypatch, path_tmp, fresh_shell_cache):
    shell = _touch(path_tmp / f"myshell{EXE}")
    monkeypatch.setenv("STEPUP_SHELL", shell)
    assert popen_args("echo hello", shell=True, env={}, cwd=".") == [shell, "-c", "echo hello"]


def test_popen_args_shell_not_found(monkeypatch, path_tmp, fresh_shell_cache):
    monkeypatch.setenv("STEPUP_SHELL", path_tmp / "missing")
    with pytest.raises(ConfigError):
        popen_args("echo hello", shell=True, env={}, cwd=".")


#
# Programs started on Windows
#


TRICKY_ARGS = [
    "plain",
    "two words",
    "a*.txt",
    "a?.txt",
    "[ab]1.txt",
    "it's",
    "''",
    'say "hi"',
    '\\"',
    r"a\b",
    r"a\\b",
    r"a\\ b",
    r"C:\Users\x y",
    r"\\server\share",
    "trailing\\",
    "trailing space\\",
    "{a,b}",
    "~",
    "~/x",
    "@resp.txt",
    "$HOME",
    "%PATH%",
    "50%",
    "^&|<>()!;#=",
    "tab\there",
    "caf\u00e9 \u65e5\u672c",
    "",
]
"""Arguments that some program on Windows changes when they are quoted for another one."""

ECHO_ARGS = "import json, sys; print(json.dumps(sys.argv[1:]))"
"""A Python program that prints its arguments in a form that can be compared."""


def _prepare_tricky_dir(path_tmp: Path) -> None:
    """Create the files that the wildcards and the response file of `TRICKY_ARGS` would match."""
    for name in ("a1.txt", "a2.txt", "b1.txt", "resp.txt"):
        (path_tmp / name).write_text("from-file another\n", encoding="utf-8")


def _run(args: str | list[str], path_tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=path_tmp,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


@windows_only
def test_windows_shell_receives_arguments_unchanged(path_tmp, fresh_shell_cache, monkeypatch):
    monkeypatch.delenv("STEPUP_SHELL", raising=False)
    _prepare_tricky_dir(path_tmp)
    args = popen_mod._program_args(
        [find_shell(), "-c", "printf '%s\\0' \"$@\"", "sh", *TRICKY_ARGS, "new\nline"]
    )
    completed = _run(args, path_tmp)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split("\0")[:-1] == [*TRICKY_ARGS, "new\nline"]


@windows_only
def test_windows_program_receives_arguments_unchanged(path_tmp):
    _prepare_tricky_dir(path_tmp)
    args = popen_args(
        [sys.executable, "-c", ECHO_ARGS, *TRICKY_ARGS], shell=False, env=os.environ, cwd=path_tmp
    )
    completed = _run(args, path_tmp)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == TRICKY_ARGS


@windows_only
@pytest.mark.parametrize("dirname", ["tools", "my tools"])
def test_windows_batch_file_receives_arguments_unchanged(dirname: str, path_tmp):
    _prepare_tricky_dir(path_tmp)
    script = path_tmp / dirname / "echo-args.cmd"
    script.parent.makedirs_p()
    script.write_text(f'@"{sys.executable}" -c "{ECHO_ARGS}" %*\n', encoding="utf-8")
    args = popen_args(
        [f"{dirname}/echo-args", *TRICKY_ARGS], shell=False, env=os.environ, cwd=path_tmp
    )
    completed = _run(args, path_tmp)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == TRICKY_ARGS


@windows_only
def test_windows_batch_file_on_the_path_of_the_step(path_tmp):
    """A program is looked up on the `PATH` of the step, not on that of the director."""
    script = path_tmp / "bin" / "hello.cmd"
    script.parent.makedirs_p()
    script.write_text("@echo hello from %~n0\n", encoding="utf-8")
    env = dict(os.environ, PATH=os.pathsep.join([script.parent, os.environ["PATH"]]))
    completed = _run(popen_args(["hello"], shell=False, env=env, cwd=path_tmp), path_tmp)
    assert completed.stdout.strip() == "hello from hello"
    with pytest.raises(FileNotFoundError):
        popen_args(["hello"], shell=False, env=os.environ, cwd=path_tmp)
