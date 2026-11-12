# SPDX-FileCopyrightText: 2026 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Bindings to the Windows API, through `ctypes`, for what the standard library does not offer.

This module can only be imported on Windows.
"""

import ctypes
from ctypes import wintypes

import attrs

__all__ = ("JobObject", "JobUsage", "get_peak_working_set", "is_process_alive")


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

PROCESS_TERMINATE = 0x0001
"""Access right needed to put a process in a job, together with `PROCESS_SET_QUOTA`."""

PROCESS_SET_QUOTA = 0x0100
"""Access right needed to put a process in a job, together with `PROCESS_TERMINATE`."""

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
"""Limit flag that terminates the members of a job when its last handle is closed."""

JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
"""Information class of `JobObjectBasicAccountingInformation`."""

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
"""Information class of `JobObjectExtendedLimitInformation`."""

JOB_TIME_UNIT = 1e-7
"""The unit of the CPU times in the accounting information of a job [s], i.e. 100 ns."""


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


class IoCounters(ctypes.Structure):
    """The `IO_COUNTERS` structure, a part of `JobObjectExtendedLimitInformation`."""

    _fields_ = (
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    )


class JobObjectBasicLimitInformation(ctypes.Structure):
    """The `JOBOBJECT_BASIC_LIMIT_INFORMATION` structure."""

    _fields_ = (
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class JobObjectExtendedLimitInformation(ctypes.Structure):
    """The `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` structure of `SetInformationJobObject`."""

    _fields_ = (
        ("BasicLimitInformation", JobObjectBasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class JobObjectBasicAccountingInformation(ctypes.Structure):
    """The `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION` structure of `QueryInformationJobObject`."""

    _fields_ = (
        ("TotalUserTime", wintypes.LARGE_INTEGER),
        ("TotalKernelTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
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
_kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
_kernel32.QueryInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel32.TerminateJobObject.restype = wintypes.BOOL


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


@attrs.define(frozen=True)
class JobUsage:
    """What the processes of a job have consumed so far, including those that have exited."""

    utime: float = attrs.field()
    """User CPU time [s]."""

    stime: float = attrs.field()
    """Kernel CPU time [s]."""

    active: int = attrs.field()
    """The number of processes in the job that have not exited yet."""


@attrs.define
class JobObject:
    """A Windows Job Object: a set of processes that is accounted for and terminated as a whole.

    A process started by a member of a job becomes a member too,
    so a job that receives a process right after its creation holds everything it spawns.
    The job has the limit `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`:
    Windows terminates the remaining members when the last handle to the job is closed,
    which also happens when the process that created the job dies.

    An instance must be used from one thread only.
    """

    _handle: int | None = attrs.field()
    """The handle of the job, or `None` after `close`."""

    @classmethod
    def create(cls) -> "JobObject":
        """Create a job without members.

        Returns
        -------
        job
            The new job, which the caller must `close`.

        Raises
        ------
        OSError
            If Windows cannot create or configure the job.
        """
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        job = cls(handle)
        info = JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _kernel32.SetInformationJobObject(
            handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            error = ctypes.WinError(ctypes.get_last_error())
            job.close()
            raise error
        return job

    @property
    def closed(self) -> bool:
        """Whether `close` has been called."""
        return self._handle is None

    def assign(self, pid: int) -> None:
        """Make a process a member of the job.

        Parameters
        ----------
        pid
            The id of a process that the caller keeps a handle to,
            so that the id cannot have been given to another process in the meantime.

        Raises
        ------
        OSError
            If the process cannot become a member, e.g. because it has exited already.
        """
        process = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _kernel32.AssignProcessToJobObject(self._open_handle(), process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(process)

    def terminate(self, exit_code: int) -> None:
        """Terminate all members of the job, which then report `exit_code`.

        Raises
        ------
        OSError
            If Windows refuses to terminate the job.
        """
        if not _kernel32.TerminateJobObject(self._open_handle(), exit_code):
            raise ctypes.WinError(ctypes.get_last_error())

    def query_usage(self) -> JobUsage:
        """Return the CPU time of all processes that are or were members, and how many remain.

        Raises
        ------
        OSError
            If Windows does not report the accounting information.
        """
        info = JobObjectBasicAccountingInformation()
        ok = _kernel32.QueryInformationJobObject(
            self._open_handle(),
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return JobUsage(
            utime=info.TotalUserTime * JOB_TIME_UNIT,
            stime=info.TotalKernelTime * JOB_TIME_UNIT,
            active=info.ActiveProcesses,
        )

    def close(self) -> None:
        """Close the handle of the job, which terminates the members that remain.

        Members that Windows terminates this way reported exit code 0
        when this was tested on Windows 11 24H2,
        so call `terminate` first when their exit code must tell that they were stopped.
        Closing a job twice is harmless.
        """
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def _open_handle(self) -> int:
        """Return the handle of the job, refusing to work with a closed one."""
        if self._handle is None:
            raise ValueError("The job object is closed.")
        return self._handle
