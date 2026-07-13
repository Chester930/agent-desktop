"""
routes/engine_routes.py — 前端查詢「Claude／Codex 現在各自能不能用」的唯讀端點。

單純轉發 engines/availability.get_status()（帶 TTL cache），不做任何寫入。
"""

from __future__ import annotations

from aiohttp import web

from engines import availability


async def handle_engine_status_get(request: web.Request) -> web.Response:
    force = request.rel_url.query.get("force") == "1"
    status = await availability.get_status(force=force)
    return web.json_response(status)


def register_engine_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_get("/api/engines/status", handle_engine_status_get))
