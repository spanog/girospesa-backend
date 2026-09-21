"""Small, Linux-safe memory diagnostics for bounded background work."""

from __future__ import annotations

import ctypes
import gc
import os
from pathlib import Path
import resource
import sys


def current_rss_mib() -> float | None:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1_048_576, 1)
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def peak_rss_mib() -> float:
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1_048_576 if sys.platform == "darwin" else 1_024
    return round(peak_rss / divisor, 1)


def release_native_memory() -> None:
    gc.collect()
    if sys.platform != "linux":
        return
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (AttributeError, OSError):
        return
