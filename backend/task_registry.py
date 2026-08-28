"""Central registry for fire-and-forget asyncio tasks.

Route modules can schedule background work without importing ``main``.  The
application cleanup hook then cancels every task created through this module,
including tasks created by modular route handlers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

_tasks: set[asyncio.Task[Any]] = set()


def create_background_task(awaitable: Awaitable[Any]) -> asyncio.Task[Any]:
    """Create and retain an asyncio task until it completes or is cancelled."""
    task = asyncio.create_task(awaitable)
    _tasks.add(task)
    task.add_done_callback(_discard_finished_task)
    return task


def _discard_finished_task(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    # Retrieve unexpected exceptions so fire-and-forget work cannot produce
    # noisy "Task exception was never retrieved" warnings during shutdown.
    if not task.cancelled():
        task.exception()


async def cancel_background_tasks() -> None:
    """Cancel and await all currently tracked tasks."""
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


def tracked_task_count() -> int:
    """Return the number of live tasks; useful for diagnostics and tests."""
    return len(_tasks)
