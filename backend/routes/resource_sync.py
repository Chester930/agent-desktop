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


async def handle_resource_sync_conflict_preview(request: web.Request) -> web.Response:
    """Raw content of a conflicting name's registry source and its Codex /
    Claude-mirror render targets, so the sidebar can show the user *why*
    something is flagged as a conflict (what their own native copy actually
    contains) before they decide whether to overwrite it."""
    kind = request.match_info["kind"]
    name = request.match_info["name"]
    if kind not in ("agent", "skill"):
        return web.json_response({"error": "kind must be 'agent' or 'skill'"}, status=400)
    if not name or "/" in name or "\\" in name or ".." in name:
        return web.json_response({"error": "invalid name"}, status=400)
    async with _sync_lock:
        preview = await asyncio.to_thread(_service().conflict_preview, kind, name)
    return web.json_response(preview)


async def handle_resource_sync_conflict_resolve(request: web.Request) -> web.Response:
    """Explicit, single-target force-overwrite for one conflicting name —
    the only place a target without Agent Desktop's managed marker is ever
    replaced, and only because the user picked exactly this name and target
    after seeing the preview. Never called automatically."""
    kind = request.match_info["kind"]
    name = request.match_info["name"]
    if kind not in ("agent", "skill"):
        return web.json_response({"error": "kind must be 'agent' or 'skill'"}, status=400)
    if not name or "/" in name or "\\" in name or ".." in name:
        return web.json_response({"error": "invalid name"}, status=400)
    data = await request.json() if request.can_read_body else {}
    target = data.get("target")
    if target not in ("codex", "claude_mirror"):
        return web.json_response({"error": "target must be 'codex' or 'claude_mirror'"}, status=400)
    dry_run = data.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return web.json_response({"error": "dry_run must be boolean"}, status=400)
    async with _sync_lock:
        service = _service()
        try:
            result = await asyncio.to_thread(service.resolve_conflict, kind, name, target, dry_run)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        result["status"] = await asyncio.to_thread(service.status)
    return web.json_response(result)


def register_resource_sync_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_get("/api/resource-sync", handle_resource_sync_status))
    cors_add(app.router.add_post("/api/resource-sync", handle_resource_sync))
    cors_add(app.router.add_post("/api/resource-sync/import", handle_resource_sync_import))
    cors_add(app.router.add_get(
        "/api/resource-sync/conflict/{kind}/{name}", handle_resource_sync_conflict_preview
    ))
    cors_add(app.router.add_post(
        "/api/resource-sync/conflict/{kind}/{name}/resolve", handle_resource_sync_conflict_resolve
    ))
