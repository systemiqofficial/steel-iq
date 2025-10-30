"""
Windows Job Object wrapper for ensuring Django and all child processes
are terminated when Electron exits.

Usage: python windows_job_wrapper.py <command> <args...>
"""

import ctypes
import subprocess
import sys
from ctypes import wintypes

# Windows API constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


# Windows API structures
class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", wintypes.ULARGE_INTEGER),
        ("WriteOperationCount", wintypes.ULARGE_INTEGER),
        ("OtherOperationCount", wintypes.ULARGE_INTEGER),
        ("ReadTransferCount", wintypes.ULARGE_INTEGER),
        ("WriteTransferCount", wintypes.ULARGE_INTEGER),
        ("OtherTransferCount", wintypes.ULARGE_INTEGER),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def main():
    if len(sys.argv) < 2:
        print("Usage: python windows_job_wrapper.py <command> <args...>", file=sys.stderr)
        sys.exit(1)

    # Load kernel32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Create job object
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        print(f"Failed to create job object: {ctypes.get_last_error()}", file=sys.stderr)
        sys.exit(1)

    # Set kill-on-close flag
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    result = kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )

    if not result:
        print(f"Failed to set job information: {ctypes.get_last_error()}", file=sys.stderr)
        kernel32.CloseHandle(job)
        sys.exit(1)

    # Start subprocess
    proc = subprocess.Popen(sys.argv[1:])

    # Assign to job (critical: use process handle, not PID)
    process_handle = ctypes.c_void_p(proc._handle)
    result = kernel32.AssignProcessToJobObject(job, process_handle)

    if not result:
        print(f"Failed to assign process to job: {ctypes.get_last_error()}", file=sys.stderr)
        proc.kill()
        kernel32.CloseHandle(job)
        sys.exit(1)

    print(f"Django started in job object (PID: {proc.pid})")

    # Wait for process to complete
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass

    # Clean up (OS will kill all processes in job when handle closes)
    kernel32.CloseHandle(job)
    sys.exit(proc.returncode or 0)


if __name__ == "__main__":
    main()
