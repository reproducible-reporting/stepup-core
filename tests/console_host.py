# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run a program in the Windows console of this process, and play the user of that console.

Usage: `console_host.py OUTPUT COMMAND [ARG ...]`

This script must be started in a console of its own (`CREATE_NEW_CONSOLE`),
with a pipe as its standard input.
The program reads from the console, like a program that a user starts in a terminal,
and writes its output to the file `OUTPUT`.
Each line on the standard input of this script is something the user does:

- `ctrl-c` and `ctrl-break` generate the console control event of that key combination,
  for every process in the console.
- `keys TEXT` types the characters of `TEXT`.
- `kill` terminates the program without notice, as the task manager does.

When the standard input is closed,
the script waits for the program and exits with its return code.
"""

import ctypes
import msvcrt
import os
import signal
import subprocess
import sys
from ctypes import wintypes

from stepup.core.winapi import KEY_EVENT, InputRecord

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.SetConsoleCtrlHandler.argtypes = (wintypes.LPVOID, wintypes.BOOL)
kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
kernel32.CreateFileW.argtypes = (
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.WriteConsoleInputW.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(InputRecord),
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.WriteConsoleInputW.restype = wintypes.BOOL


def open_console_input() -> int:
    """Open the input buffer of the console, for reading keys and for writing them."""
    handle = kernel32.CreateFileW(
        "CONIN$",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def type_keys(handle: int, text: str) -> None:
    """Put the events of pressing and releasing the key of each character in the input buffer."""
    records = (InputRecord * (2 * len(text)))()
    for i, char in enumerate(text):
        for j, down in enumerate((True, False)):
            record = records[2 * i + j]
            record.EventType = KEY_EVENT
            record.KeyEvent.bKeyDown = down
            record.KeyEvent.wRepeatCount = 1
            record.KeyEvent.UnicodeChar = ord(char)
    written = wintypes.DWORD()
    if not kernel32.WriteConsoleInputW(handle, records, len(records), ctypes.byref(written)):
        raise ctypes.WinError(ctypes.get_last_error())


def main() -> int:
    path_output = sys.argv[1]
    argv = sys.argv[2:]
    # A pipe is otherwise read in the ANSI code page, with Python 3.14 and older.
    sys.stdin.reconfigure(encoding="utf-8")
    # A shell starts its programs with Ctrl-C enabled.
    # This process may have inherited the opposite, e.g. from a parent in a new process group.
    if not kernel32.SetConsoleCtrlHandler(None, False):
        raise ctypes.WinError(ctypes.get_last_error())
    console_input = open_console_input()
    stdin_fd = msvcrt.open_osfhandle(console_input, os.O_RDONLY)
    with open(path_output, "wb") as output:
        process = subprocess.Popen(argv, stdin=stdin_fd, stdout=output, stderr=subprocess.STDOUT)
    # The events below are meant for the program, while the user stays where it is.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGBREAK, signal.SIG_IGN)
    for line in sys.stdin:
        action, _, argument = line.rstrip("\n").partition(" ")
        if action == "ctrl-c":
            os.kill(0, signal.CTRL_C_EVENT)
        elif action == "ctrl-break":
            os.kill(0, signal.CTRL_BREAK_EVENT)
        elif action == "keys":
            type_keys(console_input, argument)
        elif action == "kill":
            process.kill()
        else:
            raise ValueError(f"Unknown action: {action}")
    return process.wait()


if __name__ == "__main__":
    sys.exit(main())
