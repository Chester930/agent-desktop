"""2026-07-11：GET /api/engines/status——前端查詢 Claude／Codex 可用性的
唯讀端點。這裡把 engines.availability.get_status 換掉，不驗證真實 CLI
（真實 CLI 的驗證另外在對話紀錄裡用這台機器上真實已登入的 claude/codex
CLI 跑過)。
"""
import pytest

from engines import availability

pytestmark = pytest.mark.asyncio


async def test_engine_status_route_returns_get_status_result(client, monkeypatch, app):
    async def _fake_get_status(force: bool = False) -> dict:
        return {
            "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            "codex": {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"},
        }

    monkeypatch.setattr(availability, "get_status", _fake_get_status)

    resp = await client.get("/api/engines/status")
    assert resp.status == 200
    body = await resp.json()
    assert body["claude"]["available"] is True
    assert body["codex"]["available"] is False
    assert body["codex"]["reason"] == "not_installed"


async def test_engine_status_route_force_query_param(client, monkeypatch, app):
    captured = {}

    async def _fake_get_status(force: bool = False) -> dict:
        captured["force"] = force
        return {
            "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            "codex": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
        }

    monkeypatch.setattr(availability, "get_status", _fake_get_status)

    await client.get("/api/engines/status")
    assert captured["force"] is False

    await client.get("/api/engines/status?force=1")
    assert captured["force"] is True
