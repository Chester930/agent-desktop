"""Shared cleanup for short-lived and cancelled asyncio subprocesses."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers import safe_kill_process


async def terminate_and_reap(proc: Any, timeout: float = 2.0) -> None:
    """Terminate a still-running child and await its process handle.

    ``CancelledError`` can interrupt a subprocess reader before its normal
    ``communicate()``/``wait()`` path runs.  Always awaiting ``wait()`` here
    lets Windows close the Proactor pipe transport before the event loop exits.
    Cleanup is best-effort because this helper is used from exception/finally
    paths and must never hide the original failure.
    """
    if proc is None:
        return

    if getattr(proc, "returncode", None) is None:
        try:
            safe_kill_process(proc)
        except Exception:
            pass

    wait = getattr(proc, "wait", None)
    if wait is None:
        return
    try:
        await asyncio.wait_for(wait(), timeout=timeout)
    except Exception:
        pass
