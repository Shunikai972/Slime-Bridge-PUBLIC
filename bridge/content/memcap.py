"""Hard memory ceiling for the current process (Windows job object); a no-op elsewhere.

The launcher's extraction runs in a worker process that caps itself with this: an allocation past the ceiling fails inside the worker (a
MemoryError the extractor reports) instead of squeezing the player's whole machine.
"""
from __future__ import annotations

import sys


def limit_process_memory(gigabytes: float) -> bool:
    """Cap this process' committed memory at `gigabytes`; True when the ceiling is in force."""
    if sys.platform != 'win32' or not gigabytes or gigabytes <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount',
                'OtherTransferCount')]

        class _Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', _Basic), ('IoInfo', _IoCounters), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]

        kernel = ctypes.windll.kernel32
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return False
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x00000100            # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = int(gigabytes * 1024 ** 3)
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):       # ExtendedLimitInformation
            return False
        return bool(kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()))
    except (OSError, AttributeError, ValueError):
        return False
