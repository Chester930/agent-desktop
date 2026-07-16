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
import json
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
)


def _get_main_module():
    import sys
    for name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(name)
        if mod and hasattr(mod, "CLAUDE_BIN"):
            return mod
    return None


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


def _claude_bin_and_key():
    main = _get_main_module()
    claude_bin = getattr(main, "CLAUDE_BIN", "claude") if main else "claude"
    resolve_key = getattr(main, "_resolve_api_key", lambda: "") if main else (lambda: "")
    return claude_bin, resolve_key


def _resolve_codex_key_fn():
    main = _get_main_module()
    return getattr(main, "_resolve_codex_api_key", lambda: "") if main else (lambda: "")


# ── Agent handlers ────────────────────────────────────────────────────────────

async def handle_agents(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    agents = []
    if AGENTS_DIR.exists():
        files = sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower())
        results = await asyncio.gather(*[_agent_dict_safe(f) for f in files])
        agents = [d for d in results if d is not None]
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
    # 最愛標籤同步：favorite=true → 複製到 CLAUDE_HOME/agents/，false → 移除
    if "favorite" in data:
        import database as _db
        import shutil
        claude_agents_dir = _db.CLAUDE_HOME / "agents"
        claude_agents_dir.mkdir(parents=True, exist_ok=True)
        dest = claude_agents_dir / f"{aid}.md"
        if data["favorite"]:
            shutil.copy2(str(f), str(dest))
        else:
            dest.unlink(missing_ok=True)
    await _trigger_resource_sync(agent_ids={aid})
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
    await _trigger_resource_sync(agent_ids={name})
    return web.json_response({"ok": True, "id": name})


async def handle_agent_delete(request: web.Request) -> web.Response:
    AGENTS_DIR, _ = _dirs()
    aid = request.match_info["id"]
    if not aid or "/" in aid or "\\" in aid or ".." in aid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = AGENTS_DIR / f"{aid}.md"
    if f.exists():
        f.unlink()
        await _trigger_resource_sync(agent_ids={aid})
    return web.json_response({"ok": True})


# ── HR Dispatch ───────────────────────────────────────────────────────────────

async def _run_hr_agent(task: str, engine_name: str = "") -> dict:
    """挑選 Agent 組隊的 HR 任務本身也是一次性的文字補全（要求模型輸出一段
    JSON），跟 Team Run 的單一 step 是同一種形狀，所以比照
    routes/teams.py::_agent_run_capture() 改走 engines/ 抽象，讓 HR 派發
    也能選 Codex 執行（例如使用者的 Claude 額度用盡、想改用 Codex 做組隊
    規劃）。"""
    from database import get_engine_mode
    from engines.registry import get_engine, resolve_engine_name_gated
    from engines.availability import apply_availability_fallback, NoEngineAvailableError

    AGENTS_DIR, _ = _dirs()
    _, resolve_key = _claude_bin_and_key()
    resolve_codex_key = _resolve_codex_key_fn()
    mode = get_engine_mode()
    allowed = frozenset({mode}) if mode in ("claude", "codex") else frozenset({"claude", "codex"})
    preferred_name = resolve_engine_name_gated("", engine_name, mode)
    try:
        final_name, engine_notice = await apply_availability_fallback(preferred_name, allowed)
    except NoEngineAvailableError as e:
        return {"error": f"HR dispatch failed: {e}"}
    engine = get_engine(final_name)
    agents_list = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                d = _agent_dict(f)
                agents_list.append({
                    "id":          f.stem,
                    "name":        d.get("name", f.stem),
                    "description": d.get("description", ""),
                    "skills":      d.get("skills", []),
                })
            except Exception:
                pass
    if not agents_list:
        return {"error": "尚未建立任何 Agent。請先至 Agent 頁籤建立 Agent 後，再使用自動組隊功能。"}

    registry_str = json.dumps(agents_list, ensure_ascii=False, indent=2)
    prompt = f"""你是一個 HR Agent（任務協調整合器）。請分析使用者的任務描述，並從下方的 Agent 列表中，挑選最適合的 Agent 組成一個循序執行的團隊（Team）來完成任務。

可用 Agent 列表：
{registry_str}

請務必遵守以下規定：
1. 僅從上述列表中挑選 Agent，不要捏造不存在的 Agent ID。
2. 根據任務的邏輯順序安排執行步驟（Step 1, Step 2...）。前一個 Agent 的輸出將作為下一個 Agent 的輸入上下文。
3. 為每個步驟的 Agent 設定合適的 role（任務職責說明）。
4. 設定 input_memory（讀取）與 output_memory（寫入）的鍵值（keys），用於在步驟間傳遞 context 或保存中間產物。例如第一步寫入 'step1-result'，第二步讀取 'step1-result'。
5. execution_mode 固定填 "sequential"——這個團隊本來就是設計成「前一位輸出傳給下一位」，一定要循序執行，不能平行跑。
6. 請只輸出一個純 JSON 對象，不要包含任何 markdown 標記（如 ```json ... ```）、引言或額外說明文字。

JSON Schema:
{{
  "name": "auto-created-team-name",
  "description": "說明此團隊如何協作與組隊理由",
  "execution_mode": "sequential",
  "members": [
    {{
      "agent": "選用的 Agent ID",
      "role": "該步驟的具體工作描述",
      "input_memory": ["要讀取的 memory 鍵"],
      "output_memory": ["要寫入的 memory 鍵"]
    }}
  ]
}}

使用者任務描述：
{task}
"""

    # 跟 routes/teams.py::_agent_run_capture() 同樣的理由：resolve_key() 只
    # 解析 Anthropic key、resolve_codex_key() 只解析 Codex key，兩者完全
    # 分開，避免誤植進對方引擎的環境變數蓋掉正常運作的登入憑證。
    engine_api_key = (
        resolve_key() if engine.name == "claude"
        else resolve_codex_key() if engine.name == "codex"
        else ""
    )

    async def _noop_on_text(chunk: str) -> None:
        pass

    try:
        result = await asyncio.wait_for(
            engine.run_turn(
                prompt=prompt,
                cwd=str(Path.home()),
                model="",
                permission_mode="",
                resume_session_id=None,
                api_key=engine_api_key,
                on_text=_noop_on_text,
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return {"error": "HR dispatch failed: timed out after 90s"}
    except Exception as e:
        return {"error": f"HR dispatch failed: {e}"}

    if result.error:
        return {"error": f"HR dispatch failed: {result.error}"}
    output_str = result.output.strip()

    s = output_str.strip()
    s = _re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = _re.sub(r"\s*```$", "", s.strip()).strip()

    def _with_sequential_default(plan):
        # 健檢修復：HR Agent 產生的團隊本來就是設計成「前一位輸出傳給下一位」
        # （見上方 prompt），一定要循序執行；prompt 已要求固定填 "sequential"，
        # 這裡再補一層防呆，避免模型偶爾漏填時又靜默 fallback 成 "parallel"
        # （routes/teams.py 的 handle_team_run_post 對缺欄位的預設值就是
        # "parallel"）。
        if isinstance(plan, dict) and "execution_mode" not in plan:
            plan["execution_mode"] = "sequential"
        if isinstance(plan, dict) and engine_notice:
            plan["engine_notice"] = engine_notice
        return plan

    try:
        return _with_sequential_default(json.loads(s))
    except Exception:
        start_idx, end_idx = s.find("{"), s.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                return _with_sequential_default(json.loads(s[start_idx:end_idx + 1]))
            except Exception:
                pass
        return {"error": "Failed to parse HR Agent JSON response", "raw": output_str[:500]}


async def handle_hr_dispatch(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        task = data.get("task", "").strip()
        if not task:
            return web.json_response({"error": "task is required"}, status=400)
        engine_name = data.get("engine", "")
        if engine_name:
            from engines.registry import ENGINES
            if engine_name not in ENGINES:
                return web.json_response({"error": "invalid engine"}, status=400)
        plan = await _run_hr_agent(task, engine_name)
        if "error" in plan:
            return web.json_response(plan, status=500)
        return web.json_response(plan)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Skill handlers ────────────────────────────────────────────────────────────

async def handle_skills(request: web.Request) -> web.Response:
    _, SKILLS_DIR = _dirs()
    skills = []
    if not SKILLS_DIR.exists():
        return web.json_response(skills)
    for entry in sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name.lower()):
        try:
            if entry.is_dir():
                skills.append(_skill_dict_from_dir(entry))
            elif entry.suffix == ".md":
                skills.append(_skill_dict_from_file(entry))
        except Exception:
            pass
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
    await _trigger_resource_sync(skill_ids={sid})
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
    """Register all agent + HR routes on the aiohttp app."""
    cors_add(app.router.add_get("/api/agents",            handle_agents))
    cors_add(app.router.add_get("/api/agents/registry",   handle_agents_registry))
    cors_add(app.router.add_get("/api/agents/{id}",       handle_agent_get))
    cors_add(app.router.add_put("/api/agents/{id}",       handle_agent_put))
    cors_add(app.router.add_post("/api/agents",           handle_agent_post))
    cors_add(app.router.add_delete("/api/agents/{id}",    handle_agent_delete))
    cors_add(app.router.add_post("/api/hr/dispatch",      handle_hr_dispatch))
    cors_add(app.router.add_post("/api/agents/import-agency", handle_agent_import_agency))


def register_skill_routes(app: web.Application, cors_add) -> None:
    """Register all skill routes on the aiohttp app."""
    cors_add(app.router.add_get("/api/skills",            handle_skills))
    cors_add(app.router.add_get("/api/skills/{id}",       handle_skill_get))
    cors_add(app.router.add_put("/api/skills/{id}",       handle_skill_put))
