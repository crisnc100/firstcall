"""Named crash points for fault tests.

A process started with FIRSTCALL_FAILPOINT=<name> logs one line and exits with code 137
when it reaches that point, the same code a killed process gets. Settings refuse a
failpoint in beta, so this can never fire there.
"""

from __future__ import annotations

import os
import sys


def fire(point: str) -> None:
    if os.environ.get("FIRSTCALL_FAILPOINT") == point:
        print(f"FIRSTCALL_FAILPOINT fired {point}", file=sys.stderr, flush=True)
        os._exit(137)
