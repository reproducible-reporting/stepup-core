# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Bindings to the Windows API, through `ctypes`, for what the standard library does not offer.

This module can only be imported on Windows.
"""

import ctypes
from ctypes import wintypes

__all__ = ("get_peak_working_set", "is_process_alive")


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)

SYNCHRONIZE = 0x00100000
"""Access right needed to wait for a process handle."""

ERROR_ACCESS_DENIED = 5
"""Error code of `OpenProcess` for a process that exists, but may not be opened."""

WAIT_OBJECT_0 = 0x0
"""Return value of `WaitForSingleObject` when the object is signaled, i.e. the process exited."""

WAIT_TIMEOUT = 0x102
"""Return value of `WaitForSingleObject` when the object is not yet signaled."""


class ProcessMemoryCounters(ctypes.Structure):
    """The `PROCESS_MEMORY_COUNTERS` structure of `GetProcessMemoryInfo`."""

    _fields_ = (
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    )


_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.GetCurrentProcess.argtypes = ()
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_psapi.GetProcessMemoryInfo.argtypes = (
    wintypes.HANDLE,
    ctypes.POINTER(ProcessMemoryCounters),
    wintypes.DWORD,
)
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def is_process_alive(pid: int) -> bool:
    """Check whether a process with this pid exists and has not exited yet.

    A process that has exited may still be known to Windows,
    as long as another process holds a handle to it.
    Such a process counts as not alive,
    which is why this waits on the process handle
    instead of comparing its exit code with `STILL_ACTIVE`,
    a value that a process may also return as its exit code.

    Returns
    -------
    alive
        `True` if the process is running,
        or if it exists but this process is not allowed to open it.

    Raises
    ------
    OSError
        If the state of the process cannot be determined.
    """
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # Any other error, typically `ERROR_INVALID_PARAMETER`, means there is no such process.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        result = _kernel32.WaitForSingleObject(handle, 0)
        if result == WAIT_TIMEOUT:
            return True
        if result == WAIT_OBJECT_0:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32.CloseHandle(handle)


def get_peak_working_set() -> int:
    """Return the peak working set of the current process [bytes].

    Raises
    ------
    OSError
        If Windows does not report the memory counters.
    """
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(ProcessMemoryCounters)
    ok = _psapi.GetProcessMemoryInfo(
        _kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize
