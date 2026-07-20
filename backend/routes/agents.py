"""
routes/agents.py — Agent + Skill CRUD route handlers.

All pure I/O logic; shared mutable state (REGISTRY_AGENTS_DIR, REGISTRY_SKILLS_DIR)
is read from the database module via sys.modules['__main__'] at call time
so it respects dynamic re-configuration (claudeHome/registryHome changes).

Every mutating handler writes to the registry (which defaults to CLAUDE_HOME,
so for most installs this is unchanged) and then fires a best-effort resource
sync so Codex — and, once registryHome has been decoupled from Claude Code's
own home, Claude itself — sees the change without the user having to remember
to click "sync" by hand.
"""

from __future__ import annotations

import asyncio
import re as _re
from pathlib import Path

from aiohttp import web

# Helpers live in the package root
from helpers import (
    _agent_dict,
    _agent_dict_safe,
    _parse_full_frontmatter,
    _write_frontmatter,
    _skill_dict_from_file,
    _skill_dict_from_dir,
    _skill_dict_safe,
)
from dir_cache import cached_parallel_scan


def _dirs():
    import database as _db
    return _db.REGISTRY_AGENTS_DIR, _db.REGISTRY_SKILLS_DIR


async def _trigger_resource_sync(*, agent_ids: set[str] | None = None, skill_ids: set[str] | None = None) -> None:
    """Best-effort auto-render of the registry into every native engine home
    right after a CRUD write. Never raises — a sync hiccup must not fail the
    save the user just made; the manual "檢查/同步" button in the sidebar
    remains available to retry and to surface any conflict that blocked it."""
    try:
        from routes.resource_sync import _service
        await asyncio.to_thread(_service().sync, False, agent_ids, skill_ids)
    except Exception:
        pass


# ── Agent handlers ────────────────────────────────────────────────────────────

def _agent_dict_or_none(f: Path) -> "dict | None":
    try:
        return _agent_dict(f)
    except Exception:
        return None


async def handle_agents(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    if not AGENTS_DIR.exists():
        return web.json_response([])
    files = sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower())
    agents = await cached_parallel_scan(f"agents:{AGENTS_DIR}", files, _agent_dict_or_none)
    return web.json_response(agents)


async def handle_agents_registry(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    registry = []
    if AGENTS_DIR.exists():
        files = sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower())
        results = await asyncio.gather(*[_agent_dict_safe(f) for f in files])
        for f, d in zip(files, results):
            if d is None:
                continue
            registry.append({
                "id":          f.stem,
                "name":        d.get("name", f.stem),
                "description": d.get("description", ""),
                "skills":      d.get("skills", []),
            })
    return web.json_response(registry)


async def handle_agent_get(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    aid = request.match_info["id"]
    if not aid or "/" in aid or "\\" in aid or ".." in aid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = AGENTS_DIR / f"{aid}.md"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_agent_dict(f))


async def handle_agent_put(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    aid = request.match_info["id"]
    if not aid or "/" in aid or "\\" in aid or ".." in aid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = AGENTS_DIR / f"{aid}.md"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    if data.get("engine"):
        from engines.registry import ENGINES
        if data["engine"] not in ENGINES:
            return web.json_response({"error": "invalid engine"}, status=400)
    fm = _parse_full_frontmatter(f)
    for field in ("name", "description", "soul", "skills", "memory", "mcp", "output_memory", "tools", "engine", "favorite"):
        if field in data:
            fm[field] = data[field]
    _write_frontmatter(f, fm)
    # 最愛標籤同步：favorite=true → 複製到 CLAUDE_HOME/agents/，false → 移除。
    # 預設安裝沒有另外設定 registryHome 時，registry（f 所在目錄）本來就
    # 是 CLAUDE_HOME/agents ——這時候 dest 跟 f 是同一個檔案，取消收藏會
    # 變成 unlink() 剛剛才寫好的 registry 檔案本身，直接把整個 agent 刪掉，
    # 而不只是清掉 favorite 欄位。這種單一來源設定下，frontmatter 早就
    # 直接是「同一份」，不需要另外複製/刪除。
    if "favorite" in data:
        import database as _db
        import shutil
        claude_agents_dir = _db.CLAUDE_HOME / "agents"
        claude_agents_dir.mkdir(parents=True, exist_ok=True)
        dest = claude_agents_dir / f"{aid}.md"
        if dest.resolve() != f.resolve():
            if data["favorite"]:
                shutil.copy2(str(f), str(dest))
            else:
                dest.unlink(missing_ok=True)
    # 在背景非同步執行資源同步，避免 Windows Docker 掛載的慢速磁碟 I/O 阻塞 API 回應
    asyncio.create_task(_trigger_resource_sync(agent_ids={aid}))
    return web.json_response({"ok": True})


async def handle_agent_post(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    data = await request.json()
    raw = data.get("name", "").strip()
    name = _re.sub(r"[^\w-]", "-", raw).lower().strip("-")
    if not name:
        return web.json_response({"error": "invalid name"}, status=400)
    engine = data.get("engine", "")
    if engine:
        from engines.registry import ENGINES
        if engine not in ENGINES:
            return web.json_response({"error": "invalid engine"}, status=400)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    f = AGENTS_DIR / f"{name}.md"
    if f.exists():
        return web.json_response({"error": "already exists"}, status=409)
    desc = data.get("description", "")
    engine_line = f"engine: {engine}\n" if engine else ""
    f.write_text(
        f"---\nname: {name}\ndescription: {desc}\ntools: Read, Grep, Glob\n"
        f"soul: \nskills: []\nmemory: []\nmcp: []\noutput_memory: []\n{engine_line}---\n\n## {name}\n\n{desc}\n",
        encoding="utf-8"
    )
    asyncio.create_task(_trigger_resource_sync(agent_ids={name}))
    return web.json_response({"ok": True, "id": name})


async def handle_agent_delete(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    aid = request.match_info["id"]
    if not aid or "/" in aid or "\\" in aid or ".." in aid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = AGENTS_DIR / f"{aid}.md"
    if f.exists():
        f.unlink()
        asyncio.create_task(_trigger_resource_sync(agent_ids={aid}))
    return web.json_response({"ok": True})


# ── Skill handlers ────────────────────────────────────────────────────────────

def _skill_mtime(e: Path) -> float:
    mtime = e.stat().st_mtime
    if e.is_dir():
        # 目錄自己的 mtime 只有新增/刪除/改名項目才會變，SKILL.md/
        # README.md 內容被原地編輯不會動到——真正的內容檔案 mtime 也要
        # 一起考慮，不然快取會漏掉這種就地編輯。
        for name in ("SKILL.md", "README.md"):
            try:
                mtime = max(mtime, (e / name).stat().st_mtime)
            except OSError:
                pass
    return mtime


async def handle_skills(request: web.Request) -> web.Response:
    _, SKILLS_DIR = _dirs()
    if not SKILLS_DIR.exists():
        return web.json_response([])
    entries = sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name.lower())
    skills = await cached_parallel_scan(
        f"skills:{SKILLS_DIR}", entries, _skill_dict_safe, mtime_fn=_skill_mtime
    )
    return web.json_response(skills)


async def handle_skill_get(request: web.Request) -> web.Response:
    _, SKILLS_DIR = _dirs()
    sid = request.match_info["id"]
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = SKILLS_DIR / f"{sid}.md"
    if f.exists():
        return web.json_response(_skill_dict_from_file(f))
    d = SKILLS_DIR / sid
    if d.is_dir():
        return web.json_response(_skill_dict_from_dir(d))
    return web.json_response({"error": "not found"}, status=404)


async def handle_skill_put(request: web.Request) -> web.Response:
    _, SKILLS_DIR = _dirs()
    sid = request.match_info["id"]
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = SKILLS_DIR / f"{sid}.md"
    if not f.exists():
        d = SKILLS_DIR / sid
        for c in (d / "SKILL.md", d / "README.md"):
            if c.exists():
                f = c
                break
        else:
            return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    fm = _parse_full_frontmatter(f)
    for field in ("description", "mcp", "memory", "output_memory"):
        if field in data:
            fm[field] = data[field]
    _write_frontmatter(f, fm)
    asyncio.create_task(_trigger_resource_sync(skill_ids={sid}))
    return web.json_response({"ok": True})


async def handle_agent_import_agency(request: web.Request) -> web.Response:
    try:
        from agency_agents_importer import run_import
        res = await asyncio.to_thread(run_import, False)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"ok": False, "message": f"Import failed: {str(e)}"}, status=500)


# ── Route registration ────────────────────────────────────────────────────────

def register_agent_routes(app: web.Application, cors_add) -> None:
    """Register all agent routes on the aiohttp app."""
    cors_add(app.router.add_get("/api/agents",            handle_agents))
    cors_add(app.router.add_get("/api/agents/registry",   handle_agents_registry))
    cors_add(app.router.add_get("/api/agents/{id}",       handle_agent_get))
    cors_add(app.router.add_put("/api/agents/{id}",       handle_agent_put))
    cors_add(app.router.add_post("/api/agents",           handle_agent_post))
    cors_add(app.router.add_delete("/api/agents/{id}",    handle_agent_delete))
    cors_add(app.router.add_post("/api/agents/import-agency", handle_agent_import_agency))


def register_skill_routes(app: web.Application, cors_add) -> None:
    """Register all skill routes on the aiohttp app."""
    cors_add(app.router.add_get("/api/skills",            handle_skills))
    cors_add(app.router.add_get("/api/skills/{id}",       handle_skill_get))
    cors_add(app.router.add_put("/api/skills/{id}",       handle_skill_put))
