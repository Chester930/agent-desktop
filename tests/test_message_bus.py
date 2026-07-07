"""T7: message_bus 的 async 訂閱者例外不應被靜默吃掉，且 create_task 需保留參照。"""
import asyncio

import pytest

from message_bus import MessageBus


@pytest.fixture
def bus():
    # 繞過 singleton，讓每個測試拿到乾淨的 bus 實例
    b = object.__new__(MessageBus)
    b._init_bus()
    return b


@pytest.mark.asyncio
async def test_async_subscriber_exception_is_logged_not_swallowed(bus, caplog):
    async def bad_cb(msg):
        raise ValueError("boom")

    bus.subscribe("topic", bad_cb)
    with caplog.at_level("ERROR"):
        bus.publish("topic", {"x": 1})
        await asyncio.sleep(0.05)

    assert any("boom" in r.message or "Error in async subscriber" in r.message for r in caplog.records)
    assert len(bus._pending_tasks) == 0


@pytest.mark.asyncio
async def test_async_subscriber_task_reference_is_kept_then_cleaned_up(bus):
    async def slow_cb(msg):
        await asyncio.sleep(0.02)

    bus.subscribe("topic", slow_cb)
    bus.publish("topic", {"x": 1})

    # Immediately after publish, the task must be referenced (not GC'd mid-flight)
    assert len(bus._pending_tasks) == 1

    await asyncio.sleep(0.05)
    assert len(bus._pending_tasks) == 0


@pytest.mark.asyncio
async def test_multiple_async_subscribers_all_run(bus):
    results = []

    async def cb1(msg):
        results.append(("cb1", msg["x"]))

    async def cb2(msg):
        results.append(("cb2", msg["x"]))

    bus.subscribe("topic", cb1)
    bus.subscribe("topic", cb2)
    bus.publish("topic", {"x": 42})
    await asyncio.sleep(0.05)

    assert ("cb1", 42) in results
    assert ("cb2", 42) in results
