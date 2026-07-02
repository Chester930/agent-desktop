"""
routes/teams.py — Team CRUD and Team Run route handlers.

All team execution and sequential running logic is encapsulated here.
Dynamic config variables (TEAMS_DIR, AGENTS_DIR, CLAUDE_BIN, etc.)
are resolved dynamically so changes to claudeHome are respected.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from aiohttp import web

# Helpers and DB imports
from helpers import _team_dict, _write_team_yaml, _agent_dict
from database import (
    _memory_dir,
    _team_memory_dir,
    _encode_slug,
    _write_md,
    _log,
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
    return _db.TEAMS_DIR, _db.AGENTS_DIR


def _claude_bin_and_key():
    main = _get_main_module()
    claude_bin = getattr(main, "CLAUDE_BIN", "claude") if main else "claude"
    resolve_key = getattr(main, "_resolve_api_key", lambda: "") if main else (lambda: "")
    return claude_bin, resolve_key


def _get_agent_soul(agent_id: str) -> str:
    main = _get_main_module()
    get_soul = getattr(main, "get_agent_soul", None) if main else None
    if get_soul:
        return get_soul(agent_id)
    return ""


def _get_build_team_memory_context():
    main = _get_main_module()
    return getattr(main, "build_team_memory_context", None) if main else None


# ── Team Run State ────────────────────────────────────────────────────────────

_team_runs:   dict[str, dict] = {}
_team_events: dict[str, list] = {}
_team_queues: dict[str, list] = {}
_team_run_processes: dict[str, asyncio.subprocess.Process] = {}


def _cleanup_old_runs(max_age: float = 7200.0) -> None:
    """Remove finished runs older than max_age seconds (default 2 h)."""
    now = time.time()
    stale = [rid for rid, r in _team_runs.items()
             if r.get("_finished_at") and now - r["_finished_at"] > max_age]
    for rid in stale:
        _team_runs.pop(rid, None)
        _team_events.pop(rid, None)
        _team_queues.pop(rid, None)


def _tr_emit(run_id: str, event: dict) -> None:
    _team_events.setdefault(run_id, []).append(event)
    for q in _team_queues.get(run_id, []):
        q.put_nowait(event)


async def _gc_team_runs_task() -> None:
    """Background task to cleanup old team runs, preventing leaks."""
    while True:
        await asyncio.sleep(1800)  # 30 min
        try:
            _cleanup_old_runs(7200.0)
        except Exception:
            pass


async def _agent_run_capture(
    run_id: str, step_idx: int,
    agent_id: str, prompt: str,
    model: str, cwd: str
) -> str:
    _, AGENTS_DIR = _dirs()
    claude_bin, resolve_key = _claude_bin_and_key()
    agent_file = AGENTS_DIR / f"{agent_id}.md"
    agent_body = ""
    if agent_file.exists():
        try:
            raw_text = agent_file.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                agent_body = parts[2].strip() if len(parts) >= 3 else ""
            else:
                agent_body = raw_text
        except Exception:
            pass

    soul = _get_agent_soul(agent_id)
    full_prompt = prompt
    if agent_body:
        full_prompt = f"[代理人：{agent_id}]\n{agent_body}\n\n---\n\n{full_prompt}"
    if soul:
        full_prompt = f"[System Persona]\n{soul}\n\n{full_prompt}"

    cmd = [claude_bin, "-p", full_prompt, "--output-format", "stream-json", "--verbose"]
    if model and model not in ("sonnet", ""):
        cmd += ["--model", model]

    env = {**os.environ}
    api_key = resolve_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    if _team_runs.get(run_id, {}).get("status") == "cancelled":
        return "[Team Run 已取消]"

    safe_cwd = cwd if (cwd and Path(cwd).is_dir()) else str(Path.home())
    output_parts: list[str] = []
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=safe_cwd,
            env=env,
        )
        _team_run_processes[run_id] = proc

        async for line in proc.stdout:
            if _team_runs.get(run_id, {}).get("status") == "cancelled":
                try:
                    proc.terminate()
                except Exception:
                    pass
                break

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                chunk = ""
                if ev.get("type") == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunk += block["text"]
                elif ev.get("type") == "text":
                    chunk = ev.get("text", "")
                if chunk:
                    output_parts.append(chunk)
                    _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": chunk})
            except json.JSONDecodeError:
                pass
        await proc.wait()
    except Exception as e:
        err = f"\n[Error running {agent_id}: {e}]\n"
        output_parts.append(err)
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": err})
    finally:
        _team_run_processes.pop(run_id, None)

    return "".join(output_parts)


async def _execute_team_run(run_id: str, task: str, model: str, cwd: str) -> None:
    _, AGENTS_DIR = _dirs()
    run = _team_runs[run_id]
    steps = run["steps"]
    team_id = run.get("team_id", "")
    all_member_ids = [s["agent"] for s in steps]
    prev_output = ""

    build_team_mem = _get_build_team_memory_context()

    for i, step in enumerate(steps):
        if run.get("status") == "cancelled":
            break
        step["status"] = "running"
        _tr_emit(run_id, {"type": "step_start", "step": i,
                           "agent": step["agent"], "role": step["role"]})

        agent_id = step["agent"]
        agent_info = {}
        try:
            f_agent = AGENTS_DIR / f"{agent_id}.md"
            if f_agent.exists():
                agent_info = _agent_dict(f_agent)
        except Exception:
            pass

        # 分層 team memory 注入
        if build_team_mem:
            mem_ctx = build_team_mem(team_id, all_member_ids, agent_id, cwd)
        else:
            mem_ctx = ""

        # P2-B2: per-member input_memory keys (from team YAML) take precedence;
        # fallback to agent-level memory keys (from agent frontmatter)
        mem_dir = _memory_dir()
        step_input_keys  = step.get("input_memory",  []) or agent_info.get("memory", [])
        step_output_keys = step.get("output_memory", []) or agent_info.get("output_memory", [])

        legacy_memory: list[str] = []
        for key in step_input_keys:
            key_file = mem_dir / f"{key}.md"
            if key_file.exists():
                try:
                    content = key_file.read_text(encoding="utf-8")
                    legacy_memory.append(f"### {key}\n\n{content}")
                except Exception:
                    pass

        prompt_parts = []
        if mem_ctx:
            prompt_parts.append(f"[Memory Context]\n{mem_ctx}")
        if legacy_memory:
            prompt_parts.append("---\n## 相關 Memory 上下文\n\n" + "\n\n".join(legacy_memory))

        if i == 0:
            prompt_parts.append(f"---\n## 任務\n\n{task}")
        else:
            prompt_parts.append(
                f"---\n## 任務\n\n{task}\n\n"
                f"---\n## 前置 Agent（{steps[i-1]['agent']}）的輸出\n\n{prev_output}"
            )

        prompt = "\n\n".join(prompt_parts)

        output = await _agent_run_capture(run_id, i, agent_id, prompt, model, cwd)
        step["output"] = output
        step["status"] = "done"
        prev_output = output
        _tr_emit(run_id, {"type": "step_done", "step": i})

        # P2-B2: write output to per-member output_memory keys
        if step_output_keys:
            mem_dir.mkdir(parents=True, exist_ok=True)
            for key in step_output_keys:
                try:
                    (mem_dir / f"{key}.md").write_text(output, encoding="utf-8")
                except Exception:
                    pass

    if run.get("status") != "cancelled":
        run["status"] = "done"
        run["_finished_at"] = time.time()
        summary_parts = [
            f"### {s['agent']}（{s['role']}）\n\n{s['output']}" for s in steps
        ]
        run["summary"] = "\n\n---\n\n".join(summary_parts)
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})

        # Team Run 完成後自動更新 team project memory
        if team_id and cwd:
            slug = _encode_slug(cwd)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            proj_summary = (
                f"# Team Run 記錄 — {timestamp}\n\n"
                f"## 任務\n\n{task}\n\n"
                f"## 成員\n\n" +
                "\n".join(f"- {mid}" for mid in all_member_ids) +
                f"\n\n## 執行摘要\n\n{run['summary'][:1800]}"
            )
            _write_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md", proj_summary)
    else:
        # cancelled
        run["_finished_at"] = time.time()

    _cleanup_old_runs()


# ── Team CRUD handlers ────────────────────────────────────────────────────────

async def handle_teams(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    teams = []
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(TEAMS_DIR.glob("*.yaml"), key=lambda p: p.name.lower()):
        try:
            teams.append(_team_dict(f))
        except Exception:
            pass
    return web.json_response(teams)


async def handle_team_get(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_team_dict(f))


async def handle_team_post(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    data = await request.json()
    import re as _re3
    raw = data.get("name", "").strip()
    tid = _re3.sub(r"[^\w-]", "-", raw).lower().strip("-") or "new-team"
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    f = TEAMS_DIR / f"{tid}.yaml"
    if f.exists():
        return web.json_response({"error": "already exists"}, status=409)
    _write_team_yaml(f, {
        "name": raw or tid,
        "description": data.get("description", ""),
        "leader": data.get("leader", ""),
        "members": data.get("members", []),
        "execution_mode": data.get("execution_mode", "parallel"),
    })
    return web.json_response({"ok": True, "id": tid})


async def handle_team_put(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    current = _team_dict(f)
    payload = {
        "name":           data.get("name", current["name"]),
        "description":    data.get("description", current["description"]),
        "leader":         data.get("leader", current.get("leader", "")),
        "members":        data.get("members", current["members"]),
        "execution_mode": data.get("execution_mode", current.get("execution_mode", "parallel")),
    }
    _write_team_yaml(f, payload)
    return web.json_response({"ok": True})


async def handle_team_delete(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


# ── Team Run handlers ─────────────────────────────────────────────────────────

async def handle_team_run_post(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    data    = await request.json()
    team_id = data.get("team_id", "")
    task    = data.get("task", "").strip()
    model   = data.get("model", "")
    cwd     = data.get("cwd", "")
    team_payload = data.get("team", None)

    if not task:
        return web.json_response({"error": "task required"}, status=400)

    if team_payload:
        team = team_payload
    else:
        f = TEAMS_DIR / f"{team_id}.yaml"
        if not f.exists():
            return web.json_response({"error": "team not found"}, status=404)
        team = _team_dict(f)

    if not team.get("members"):
        return web.json_response({"error": "team has no members"}, status=400)

    run_id = uuid.uuid4().hex[:8]
    _team_runs[run_id] = {
        "id":      run_id,
        "team_id": team.get("id", team_id),
        "name":    team.get("name", "Auto Team"),
        "task":    task,
        "status":  "running",
        "steps": [
            {
                "agent":         m["agent"],
                "role":          m["role"],
                # P2-B2: carry per-member memory routing into run state
                "input_memory":  m.get("input_memory",  []) if isinstance(m, dict) else [],
                "output_memory": m.get("output_memory", []) if isinstance(m, dict) else [],
                "status":        "pending",
                "output":        "",
            }
            for m in team["members"]
        ],
        "summary": "",
    }
    _team_events[run_id] = []
    _team_queues[run_id] = []

    asyncio.create_task(_execute_team_run(run_id, task, model, cwd))
    return web.json_response({"ok": True, "run_id": run_id})


async def handle_team_run_get(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    run = _team_runs.get(run_id)
    if not run:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(run)


async def handle_team_run_stream(request: web.Request) -> web.StreamResponse:
    run_id = request.match_info["run_id"]
    if run_id not in _team_runs:
        return web.Response(status=404)

    response = web.StreamResponse(headers={
        "Content-Type":      "text/event-stream",
        "Cache-Control":     "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    q: asyncio.Queue = asyncio.Queue()
    _team_queues.setdefault(run_id, []).append(q)

    for ev in _team_events.get(run_id, []):
        await response.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
        if ev.get("type") in ("done", "error", "cancelled"):
            _team_queues[run_id].remove(q)
            return response

    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await response.write(b'data: {"type":"ping"}\n\n')
                continue
            await response.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            if ev.get("type") in ("done", "error", "cancelled"):
                break
    finally:
        queues = _team_queues.get(run_id, [])
        if q in queues:
            queues.remove(q)

    return response


async def handle_team_run_cancel(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    run = _team_runs.get(run_id)
    if run:
        run["status"] = "cancelled"
        _tr_emit(run_id, {"type": "cancelled", "text": "cancelled"})
        proc = _team_run_processes.get(run_id)
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
    return web.json_response({"ok": True})


# ── Route registration ────────────────────────────────────────────────────────

async def gc_team_runs_cleanup_ctx(app: web.Application):
    task = asyncio.create_task(_gc_team_runs_task())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def register_team_routes(app: web.Application, cors_add) -> None:
    """Register all team + team run routes on the aiohttp app."""
    app.cleanup_ctx.append(gc_team_runs_cleanup_ctx)

    cors_add(app.router.add_get("/api/teams",            handle_teams))
    cors_add(app.router.add_get("/api/teams/{id}",       handle_team_get))
    cors_add(app.router.add_post("/api/teams",           handle_team_post))
    cors_add(app.router.add_put("/api/teams/{id}",       handle_team_put))
    cors_add(app.router.add_delete("/api/teams/{id}",    handle_team_delete))

    cors_add(app.router.add_post("/api/team/run",                  handle_team_run_post))
    cors_add(app.router.add_get("/api/team/run/{run_id}",          handle_team_run_get))
    cors_add(app.router.add_get("/api/team/run/{run_id}/stream",   handle_team_run_stream))
    cors_add(app.router.add_delete("/api/team/run/{run_id}",       handle_team_run_cancel))
