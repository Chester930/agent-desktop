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
    # database.REGISTRY_HOME is the single source of truth (defaults to
    # database.CLAUDE_HOME — zero-cost for existing installs). Passing
    # CLAUDE_HOME as claude_native_home unconditionally is safe: the service
    # itself treats it as a no-op whenever the two paths resolve to the same
    # directory, which is the default case where Claude Code already reads
    # the registry directly.
    return ResourceSyncService(
        database.REGISTRY_HOME, codex_home, codex_skills,
        claude_native_home=database.CLAUDE_HOME,
    )


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


async def handle_resource_sync_import(request: web.Request) -> web.Response:
    """Adopt engine-native Agents/Skills that have no registry counterpart yet
    (the codex_only / claude_only entries in status()) into the registry, so
    a Codex-only user — or an existing user's hand-made native resources —
    can stop being permanent conflicts."""
    data = await request.json() if request.can_read_body else {}
    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return web.json_response({"error": "dry_run must be boolean"}, status=400)
    async with _sync_lock:
        service = _service()
        result = await asyncio.to_thread(service.import_native, dry_run)
        result["dry_run"] = dry_run
        result["status"] = await asyncio.to_thread(service.status)
    return web.json_response(result)


def register_resource_sync_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_get("/api/resource-sync", handle_resource_sync_status))
    cors_add(app.router.add_post("/api/resource-sync", handle_resource_sync))
    cors_add(app.router.add_post("/api/resource-sync/import", handle_resource_sync_import))
