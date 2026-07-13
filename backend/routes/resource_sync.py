"""Agent/Skill sync status and deployment routes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from aiohttp import web

from resource_sync import ResourceSyncService

_sync_lock = asyncio.Lock()


def _service() -> ResourceSyncService:
    import database

    codex_home = Path(
        os.environ.get("CODEX_RESOURCE_HOME", os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    ).expanduser()
    codex_skills = Path(
        os.environ.get("CODEX_SKILLS_HOME", Path.home() / ".agents" / "skills")
    ).expanduser()
    return ResourceSyncService(database.CLAUDE_HOME, codex_home, codex_skills)


async def handle_resource_sync_status(request: web.Request) -> web.Response:
    async with _sync_lock:
        status = await asyncio.to_thread(_service().status)
    return web.json_response(status)


async def handle_resource_sync(request: web.Request) -> web.Response:
    data = await request.json() if request.can_read_body else {}
    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return web.json_response({"error": "dry_run must be boolean"}, status=400)
    async with _sync_lock:
        service = _service()
        result = await asyncio.to_thread(service.sync, dry_run)
        result["dry_run"] = dry_run
        result["status"] = await asyncio.to_thread(service.status)
    return web.json_response(result)


def register_resource_sync_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_get("/api/resource-sync", handle_resource_sync_status))
    cors_add(app.router.add_post("/api/resource-sync", handle_resource_sync))
