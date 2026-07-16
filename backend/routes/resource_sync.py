"""Agent/Skill sync status and deployment routes."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from aiohttp import web

from resource_sync import ResourceSyncService

_sync_lock = asyncio.Lock()
_DEFAULT_RECONCILE_INTERVAL = 30.0
_STATUS_CACHE_TTL = 300.0
_status_cache: dict | None = None
_status_cache_at = 0.0
_status_cache_location: Path | None = None


def _service() -> ResourceSyncService:
    import database

    codex_home = Path(
        os.environ.get("CODEX_RESOURCE_HOME", os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    ).expanduser()
    codex_skills = Path(
        os.environ.get("CODEX_SKILLS_HOME", Path.home() / ".agents" / "skills")
    ).expanduser()
    
    # 支援透過環境變數關閉自動同步到 Claude Code CLI
    disable_auto_sync = os.environ.get("DISABLE_AUTO_SYNC", "").lower() in ("true", "1", "yes")
    native_home = None if disable_auto_sync else database.CLAUDE_HOME
    
    return ResourceSyncService(
        database.REGISTRY_HOME, codex_home, codex_skills,
        claude_native_home=native_home,
    )


def _status_cache_path(service: ResourceSyncService) -> Path | None:
    home = getattr(service, "claude_home", None)
    return Path(home) / ".resource-sync-status.json" if home is not None else None


def _load_status_cache(service: ResourceSyncService) -> dict | None:
    global _status_cache, _status_cache_at, _status_cache_location
    path = _status_cache_path(service)
    if path is None:
        return None
    if _status_cache is not None and _status_cache_location == path:
        return _status_cache
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = payload.get("status")
        if isinstance(status, dict):
            _status_cache = status
            _status_cache_at = float(payload.get("updated_at", 0.0))
            _status_cache_location = path
            return status
    except (OSError, ValueError, TypeError):
        pass
    return None


def _compute_and_store_status(service: ResourceSyncService) -> dict:
    global _status_cache, _status_cache_at, _status_cache_location
    status = service.status()
    updated_at = time.time()
    path = _status_cache_path(service)
    if path is None:
        return status
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"updated_at": updated_at, "status": status}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    _status_cache = status
    _status_cache_at = updated_at
    _status_cache_location = path
    return status


async def handle_resource_sync_status(request: web.Request) -> web.Response:
    service = _service()
    status = _load_status_cache(service)
    if status is None:
        async with _sync_lock:
            status = await asyncio.to_thread(_compute_and_store_status, service)
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
        result["status"] = await asyncio.to_thread(_compute_and_store_status, service)
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
        result["status"] = await asyncio.to_thread(_compute_and_store_status, service)
    return web.json_response(result)


async def _auto_reconcile_loop() -> None:
    """Continuously adopt unambiguous native additions and render all targets."""
    raw_interval = os.environ.get("RESOURCE_RECONCILE_INTERVAL", str(_DEFAULT_RECONCILE_INTERVAL))
    try:
        interval = max(5.0, float(raw_interval))
    except ValueError:
        interval = _DEFAULT_RECONCILE_INTERVAL
    while True:
        await asyncio.sleep(interval)
        try:
            async with _sync_lock:
                await asyncio.to_thread(_service().reconcile, False)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Background reconciliation is best-effort. Status remains visible
            # through the API, while a transient filesystem problem must not
            # terminate the application lifecycle task.
            continue


async def _status_refresh_loop() -> None:
    while True:
        await asyncio.sleep(_STATUS_CACHE_TTL)
        try:
            async with _sync_lock:
                await asyncio.to_thread(_compute_and_store_status, _service())
        except asyncio.CancelledError:
            raise
        except Exception:
            continue


async def resource_reconcile_cleanup_ctx(app: web.Application):
    tasks = [
        asyncio.create_task(_auto_reconcile_loop()),
        asyncio.create_task(_status_refresh_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def register_resource_sync_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_get("/api/resource-sync", handle_resource_sync_status))
    cors_add(app.router.add_post("/api/resource-sync", handle_resource_sync))
    cors_add(app.router.add_post("/api/resource-sync/import", handle_resource_sync_import))
    app.cleanup_ctx.append(resource_reconcile_cleanup_ctx)
