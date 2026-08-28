"""Tests for shared async subprocess cleanup."""

from unittest.mock import AsyncMock

import pytest

from process_lifecycle import terminate_and_reap


@pytest.mark.asyncio
async def test_terminate_and_reap_kills_running_process_then_waits(monkeypatch):
    proc = type("FakeProcess", (), {"returncode": None, "wait": AsyncMock()})()
    killed = []
    monkeypatch.setattr("process_lifecycle.safe_kill_process", lambda value: killed.append(value))

    await terminate_and_reap(proc)

    assert killed == [proc]
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminate_and_reap_does_not_kill_finished_process(monkeypatch):
    proc = type("FakeProcess", (), {"returncode": 0, "wait": AsyncMock()})()
    killed = []
    monkeypatch.setattr("process_lifecycle.safe_kill_process", lambda value: killed.append(value))

    await terminate_and_reap(proc)

    assert killed == []
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminate_and_reap_waits_even_when_kill_fails(monkeypatch):
    proc = type("FakeProcess", (), {"returncode": None, "wait": AsyncMock()})()
    monkeypatch.setattr(
        "process_lifecycle.safe_kill_process",
        lambda _value: (_ for _ in ()).throw(RuntimeError("kill failed")),
    )

    await terminate_and_reap(proc)

    proc.wait.assert_awaited_once()
