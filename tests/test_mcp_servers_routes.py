"""2026-07-11：/api/mcp-servers CRUD 路由（backend/routes/mcp_servers.py）。
mcp_sync.sync_add/sync_remove 這裡一律 mock 掉，不呼叫真實 CLI——同步邏輯
本身的測試見 tests/test_mcp_sync.py，這裡只驗證路由層的欄位驗證跟
store 讀寫是否正確接上。
"""
import pytest

import mcp_sync

pytestmark = pytest.mark.asyncio


async def test_post_requires_valid_name(client, monkeypatch, app):
    resp = await client.post("/api/mcp-servers", json={"name": "../evil", "type": "stdio", "command": "echo"})
    assert resp.status == 400


async def test_post_requires_valid_type(client, monkeypatch, app):
    resp = await client.post("/api/mcp-servers", json={"name": "my-server", "type": "ftp"})
    assert resp.status == 400


async def test_post_stdio_requires_command(client, monkeypatch, app):
    resp = await client.post("/api/mcp-servers", json={"name": "my-server", "type": "stdio"})
    assert resp.status == 400


async def test_post_http_requires_url(client, monkeypatch, app):
    resp = await client.post("/api/mcp-servers", json={"name": "my-server", "type": "http"})
    assert resp.status == 400


async def test_post_creates_and_syncs_stdio_server(client, monkeypatch, app):
    async def fake_sync_add(name, cfg):
        return {"claude": True, "codex": False}

    monkeypatch.setattr(mcp_sync, "sync_add", fake_sync_add)

    resp = await client.post("/api/mcp-servers", json={
        "name": "my-server", "type": "stdio", "command": "npx", "args": ["my-mcp-server"], "env": {"K": "V"},
    })
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["synced"] == {"claude": True, "codex": False}

    # 讀回確認真的寫進 store 了
    get_resp = await client.get("/api/mcp-servers")
    servers = await get_resp.json()
    assert "my-server" in servers
    assert servers["my-server"]["command"] == "npx"


async def test_post_duplicate_name_409(client, monkeypatch, app):
    async def fake_sync_add(name, cfg):
        return {"claude": True, "codex": True}

    monkeypatch.setattr(mcp_sync, "sync_add", fake_sync_add)

    payload = {"name": "dup-server", "type": "stdio", "command": "echo"}
    first = await client.post("/api/mcp-servers", json=payload)
    assert first.status == 200
    second = await client.post("/api/mcp-servers", json=payload)
    assert second.status == 409


async def test_delete_removes_and_syncs(client, monkeypatch, app):
    async def fake_sync_add(name, cfg):
        return {"claude": True, "codex": True}

    async def fake_sync_remove(name):
        return {"claude": True, "codex": True}

    monkeypatch.setattr(mcp_sync, "sync_add", fake_sync_add)
    monkeypatch.setattr(mcp_sync, "sync_remove", fake_sync_remove)

    await client.post("/api/mcp-servers", json={"name": "to-delete", "type": "stdio", "command": "echo"})

    resp = await client.delete("/api/mcp-servers/to-delete")
    assert resp.status == 200
    body = await resp.json()
    assert body["synced"] == {"claude": True, "codex": True}

    get_resp = await client.get("/api/mcp-servers")
    servers = await get_resp.json()
    assert "to-delete" not in servers


async def test_delete_nonexistent_404(client, monkeypatch, app):
    resp = await client.delete("/api/mcp-servers/does-not-exist")
    assert resp.status == 404


async def test_delete_invalid_name_400(client, monkeypatch, app):
    resp = await client.delete("/api/mcp-servers/..%2Fevil")
    assert resp.status == 400
