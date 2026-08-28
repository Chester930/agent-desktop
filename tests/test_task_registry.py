import asyncio

import pytest

from task_registry import (
    cancel_background_tasks,
    create_background_task,
    tracked_task_count,
)


@pytest.mark.asyncio
async def test_registry_cancels_tasks_created_by_route_modules():
    started = asyncio.Event()

    async def worker():
        started.set()
        await asyncio.Event().wait()

    task = create_background_task(worker())
    await started.wait()
    assert tracked_task_count() == 1

    await cancel_background_tasks()

    assert task.cancelled()
    assert tracked_task_count() == 0


@pytest.mark.asyncio
async def test_completed_tasks_are_removed_from_registry():
    task = create_background_task(asyncio.sleep(0))
    await task
    await asyncio.sleep(0)

    assert tracked_task_count() == 0
