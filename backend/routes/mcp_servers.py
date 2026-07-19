"""
routes/mcp_servers.py — App 自己的 MCP server 定義 CRUD + 雙 CLI 同步。

這是「單一來源」本身（app 自己記錄 command/args/env 或 url/headers），
跟既有 routes/mcp_debugger.py（JSON-RPC 除錯）、main.py 的
handle_mcp_action/handle_local_mcp_config_*（Docker/compose 執行期生命
週期、跟 server 定義本身是正交的兩件事）不是同一個東西。

新增/刪除一筆定義時會呼叫 backend/mcp_sync.py 把定義同步到 Claude／Codex
兩邊 CLI 各自的原生設定——不是這個 app 自己去解析/改寫 ~/.claude.json 或
~/.codex/config.toml（那兩個檔案常帶 API key/token，自己動手風險高），
而是 shell out 借用兩邊 CLI 自己的 `mcp add`/`mcp remove`。
"""

from __future__ import annotations

import re as _re

from aiohttp import web

import mcp_sync


def _is_safe_mcp_name(name: str) -> bool:
    """MCP server 名稱會被當成 CLI 位置參數傳給 `claude mcp add <name> ...`／
    `codex mcp add <name> ...`（非 shell，走 subprocess_exec，沒有 shell
    injection 風險），但仍要擋開頭 `-`（會被誤判成旗標）與路徑分隔符/`..`
    （這個名稱同時是 app 自己 JSON store 的 dict key，防禦性地一併擋掉）。
    比照 main.py::_is_safe_docker_ident 的規則。
    """
    return bool(name) and bool(_re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name))


def _dirs():
    import database as _db
    return _db


async def handle_mcp_servers_get(request: web.Request) -> web.Response:
    db = _dirs()
    return web.json_response(db._load_mcp_servers())


async def handle_mcp_servers_post(request: web.Request) -> web.Response:
    data = await request.json()
    name = data.get("name", "").strip()
    if not _is_safe_mcp_name(name):
        return web.json_response({"error": "invalid name"}, status=400)

    mcp_type = data.get("type", "stdio")
    if mcp_type not in ("stdio", "http"):
        return web.json_response({"error": "type must be stdio or http"}, status=400)

    if mcp_type == "stdio":
        command = data.get("command", "").strip()
        if not command:
            return web.json_response({"error": "command is required for stdio type"}, status=400)
        cfg = {
            "type": "stdio",
            "command": command,
            "args": data.get("args") if isinstance(data.get("args"), list) else [],
            "env": data.get("env") if isinstance(data.get("env"), dict) else {},
        }
    else:
        url = data.get("url", "").strip()
        if not url:
            return web.json_response({"error": "url is required for http type"}, status=400)
        cfg = {
            "type": "http",
            "url": url,
            "headers": data.get("headers") if isinstance(data.get("headers"), dict) else {},
        }

    db = _dirs()
    servers = db._load_mcp_servers()
    if name in servers:
        return web.json_response({"error": "already exists"}, status=409)

    synced = await mcp_sync.sync_add(name, cfg)
    cfg["synced"] = synced
    servers[name] = cfg
    db._save_mcp_servers(servers)

    return web.json_response({"ok": True, "name": name, **cfg})


async def handle_mcp_servers_delete(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not _is_safe_mcp_name(name):
        return web.json_response({"error": "invalid name"}, status=400)

    db = _dirs()
    servers = db._load_mcp_servers()
    if name not in servers:
        return web.json_response({"error": "not found"}, status=404)

    synced = await mcp_sync.sync_remove(name)
    servers.pop(name, None)
    db._save_mcp_servers(servers)

    return web.json_response({"ok": True, "synced": synced})


async def handle_mcp_servers_importable(request: web.Request) -> web.Response:
    """Claude／Codex 原生已經有、但這個 app 的單一來源還沒採納的 MCP
    server——resource_sync.py 的 import_native() 對 Agent/Skill 早就有，
    這是 MCP 版本。"""
    result = await mcp_sync.compute_importable()
    return web.json_response(result)


async def handle_mcp_servers_import(request: web.Request) -> web.Response:
    data = await request.json()
    name = data.get("name", "").strip()
    if not _is_safe_mcp_name(name):
        return web.json_response({"error": "invalid name"}, status=400)
    result = await mcp_sync.import_native(name)
    if not result.get("ok"):
        status = 409 if result.get("error") == "already in registry" else 404
        return web.json_response(result, status=status)
    return web.json_response(result)


def register_mcp_server_routes(app: web.Application, cors_add) -> None:
    """Register app-owned MCP server definition CRUD routes."""
    cors_add(app.router.add_get("/api/mcp-servers",         handle_mcp_servers_get))
    cors_add(app.router.add_post("/api/mcp-servers",        handle_mcp_servers_post))
    cors_add(app.router.add_delete("/api/mcp-servers/{name}", handle_mcp_servers_delete))
    cors_add(app.router.add_get("/api/mcp-servers/importable", handle_mcp_servers_importable))
    cors_add(app.router.add_post("/api/mcp-servers/import",    handle_mcp_servers_import))
