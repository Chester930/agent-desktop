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
from helpers import _team_dict, _write_team_yaml, _agent_dict, _read_skills_content, safe_kill_process
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


def _skills_dir():
    import database as _db
    return _db.SKILLS_DIR


def _is_safe_id(name: str) -> bool:
    """
    健檢後修復：agent id 與 memory key 會被直接拼進檔案路徑
    （AGENTS_DIR/f"{agent_id}.md"、mem_dir/f"{key}.md"）。當 team run 走
    inline team payload（POST /api/team/run 的 `team` 欄位，繞過已儲存、
    受信任的 team YAML）時，這些值完全來自請求本體，未經任何驗證就能做
    路徑穿越讀寫任意 .md 檔。比照 routes/agents.py CRUD handler 既有的
    id 驗證慣例（拒絕 `/`、`\\`、`..`）。
    """
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def _claude_bin_and_key():
    main = _get_main_module()
    claude_bin = getattr(main, "CLAUDE_BIN", "claude") if main else "claude"
    resolve_key = getattr(main, "_resolve_api_key", lambda: "") if main else (lambda: "")
    return claude_bin, resolve_key


def _resolve_codex_key_fn():
    main = _get_main_module()
    return getattr(main, "_resolve_codex_api_key", lambda: "") if main else (lambda: "")


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
# 健檢修復：parallel 模式下同一個 run_id 底下的多個 step 會並行各自 spawn 一個
# process；原本用單一 dict[run_id]=proc 只能記住最後一個，後啟動的會覆蓋先前
# 的，導致 cancel/timeout 只能殺掉其中一個，其餘變成孤兒 process 繼續跑、繼續
# 燒 API 額度。改成每個 run_id 對應一個 process 集合。
_team_run_processes: dict[str, set] = {}


def _register_team_proc(run_id: str, proc) -> None:
    _team_run_processes.setdefault(run_id, set()).add(proc)


def _unregister_team_proc(run_id: str, proc) -> None:
    procs = _team_run_processes.get(run_id)
    if procs is not None:
        procs.discard(proc)
        if not procs:
            _team_run_processes.pop(run_id, None)


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
    model: str, cwd: str,
    permission_mode: str = "acceptEdits",
    default_engine: str = "",
) -> str:
    """
    可插拔 agent engine：實際呼叫哪個 CLI（Claude / Codex / ...）、怎麼組
    flags、怎麼解析輸出，全部封裝在 engines/<name>_engine.py 裡（見該套件的
    docstring）。這裡只負責：組 prompt（agent frontmatter body + soul）、
    決定要用哪個引擎（agent 自己 frontmatter 宣告的 `engine:` 優先，其次是
    這個 run 的預設值）、呼叫 engine.run_turn()、把串流文字轉發成既有的
    SSE step_text 事件、追蹤 process 以供 cancel/timeout 使用。
    """
    from database import get_engine_mode
    from engines.registry import resolve_engine_name_gated, get_engine
    from engines.availability import apply_availability_fallback, NoEngineAvailableError

    _, AGENTS_DIR = _dirs()
    _, resolve_key = _claude_bin_and_key()
    resolve_codex_key = _resolve_codex_key_fn()
    agent_file = AGENTS_DIR / f"{agent_id}.md"
    agent_body = ""
    agent_own_engine = ""
    skills_content = ""
    if _is_safe_id(agent_id) and agent_file.exists():
        try:
            raw_text = agent_file.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                agent_body = parts[2].strip() if len(parts) >= 3 else ""
            else:
                agent_body = raw_text
        except Exception:
            pass
        try:
            # 只解析一次 frontmatter，engine 跟 skills 都從同一個 dict 拿，
            # 避免重複讀檔/parse。
            agent_meta = _agent_dict(agent_file)
            agent_own_engine = agent_meta.get("engine", "")
            skills_content = _read_skills_content(_skills_dir(), agent_meta.get("skills", []))
        except Exception:
            pass

    soul = _get_agent_soul(agent_id)
    full_prompt = prompt
    if agent_body:
        full_prompt = f"[代理人：{agent_id}]\n{agent_body}\n\n---\n\n{full_prompt}"
    if skills_content:
        # Skill 內容以前只當 metadata 標籤存在，從沒被讀出來塞進 prompt，
        # 真正生效與否完全依賴底層 CLI 自己原生的 slash-skill 機制（兩邊
        # CLI 讀的路徑還不一樣，Codex 那條已知目前是壞的）。這裡改成 app
        # 自己讀內容手動折進去，讓 skill 對兩個引擎都真正生效。
        full_prompt = f"[Skills]\n{skills_content}\n\n---\n\n{full_prompt}"
    if soul:
        full_prompt = f"[System Persona]\n{soul}\n\n{full_prompt}"

    if _team_runs.get(run_id, {}).get("status") == "cancelled":
        return "[Team Run 已取消]"

    mode = get_engine_mode()
    allowed = frozenset({mode}) if mode in ("claude", "codex") else frozenset({"claude", "codex"})
    preferred_name = resolve_engine_name_gated(agent_own_engine, default_engine, mode)
    try:
        final_name, engine_notice = await apply_availability_fallback(preferred_name, allowed)
    except NoEngineAvailableError as e:
        err = f"\n[Error running {agent_id}: {e}]\n"
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": err})
        return err
    engine = get_engine(final_name)
    if engine_notice:
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": engine_notice})

    async def _on_text(chunk: str) -> None:
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": chunk})

    proc_holder: dict = {}

    def _on_process(proc) -> None:
        proc_holder["proc"] = proc
        _register_team_proc(run_id, proc)

    def _is_cancelled() -> bool:
        return _team_runs.get(run_id, {}).get("status") == "cancelled"

    # resolve_key() 只解析 Anthropic API key（_resolve_api_key()）、
    # resolve_codex_key() 只解析 Codex API key（_resolve_codex_api_key()），
    # 兩者完全分開、互不共用邏輯——如果使用者設定了 Anthropic key、又選了
    # Codex 引擎，把 Anthropic key 誤植進 codex_engine.py 的 CODEX_API_KEY
    # 環境變數會蓋掉正常運作的 `codex login` 憑證，反之亦然。engine.name
    # 是 claude/codex 以外的引擎時，兩把 key 都不套用，統一傳空字串、讓
    # CLI 退回自己已登入的憑證。
    engine_api_key = (
        resolve_key() if engine.name == "claude"
        else resolve_codex_key() if engine.name == "codex"
        else ""
    )

    try:
        result = await engine.run_turn(
            prompt=full_prompt,
            cwd=cwd,
            model=model,
            permission_mode=permission_mode,
            resume_session_id=None,
            api_key=engine_api_key,
            on_text=_on_text,
            on_process=_on_process,
            is_cancelled=_is_cancelled,
        )
    finally:
        if "proc" in proc_holder:
            _unregister_team_proc(run_id, proc_holder["proc"])

    if result.error:
        err = f"\n[Error running {agent_id}: {result.error}]\n"
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": err})
        return result.output + err

    return result.output


def _take_workspace_snapshot(cwd: str) -> dict[str, float]:
    snapshot = {}
    if not cwd or not Path(cwd).is_dir():
        return snapshot
    
    base = Path(cwd).resolve()
    exclude_dirs = {".git", ".venv", "__pycache__", "node_modules", ".gemini", "dist", "build"}
    
    count = 0
    try:
        for root, dirs, files in os.walk(base, topdown=True):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                full_path = Path(root) / file
                try:
                    rel_path = str(full_path.relative_to(base)).replace("\\", "/")
                    snapshot[rel_path] = full_path.stat().st_mtime
                except Exception:
                    pass
                count += 1
                if count > 2000:
                    return snapshot
    except Exception:
        pass
    return snapshot


def _diff_workspace_snapshot(old_snap: dict, new_snap: dict) -> list[str]:
    changed = []
    for k, v in new_snap.items():
        if k not in old_snap:
            changed.append(k)
        elif v > old_snap[k] + 0.5:
            changed.append(k)
    return changed


def _kill_team_run_processes(run_id: str) -> None:
    """Kill every process still tracked for this run (parallel mode can have several)."""
    for proc in list(_team_run_processes.get(run_id, ())):
        try:
            safe_kill_process(proc)
        except Exception:
            pass


async def _execute_team_run(run_id: str, task: str, model: str, cwd: str) -> None:
    TIMEOUT = 300
    run = _team_runs.get(run_id, {})
    custom_timeout = run.get("_test_timeout")
    timeout_val = custom_timeout if custom_timeout is not None else TIMEOUT
    
    try:
        await asyncio.wait_for(_execute_team_run_core(run_id, task, model, cwd), timeout=timeout_val)
    except asyncio.TimeoutError:
        run = _team_runs.get(run_id)
        if run and run.get("status") == "running":
            run["status"] = "cancelled"
            run["_finished_at"] = time.time()
            run["summary"] = f"### [系統熔斷] 執行時間超過 {timeout_val} 秒，已自動超時中斷以保護系統資源。"
            _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
            from message_bus import global_bus
            global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
            _kill_team_run_processes(run_id)
    except Exception as e:
        # 健檢修復：原本 `except Exception: pass` 把 _execute_team_run_core 拋出
        # 的任何例外整個吃掉——run 的 status 永遠停在 "running"（不會走到 core
        # 函式最後設定 status/_finished_at 的那段），SSE stream 也永遠不會收到
        # done/error 事件（handle_team_run_stream 只會每 30 秒送 ping，永遠不
        # 結束），前端進度面板會無限期卡在「執行中」不會有任何錯誤提示。而且
        # 因為 _finished_at 永遠不會被設定，_cleanup_old_runs() 的 2 小時回收
        # 機制也抓不到它，run/events/queues 會一直留在記憶體裡直到 process 重啟。
        _log(f"[TeamRun {run_id}] 執行時發生未預期例外：{e!r}")
        run = _team_runs.get(run_id)
        if run and run.get("status") == "running":
            run["status"] = "error"
            run["_finished_at"] = time.time()
            run["summary"] = f"### [執行錯誤] {e}"
            # 比照上面 TimeoutError 分支的既有慣例：只送一個 "done" 事件（前端
            # streamTeamRun 只認 done/error/cancelled 三種類型為終止事件，見
            # handle_team_run_stream），summary 帶錯誤文字讓使用者看得到發生了
            # 什麼事，而不是靜默停在「執行中」。
            _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
            from message_bus import global_bus
            global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
            _kill_team_run_processes(run_id)


async def _get_agent_memory_prompt(team_id: str, all_member_ids: list[str], agent_id: str, cwd: str, build_team_mem) -> str:
    _, AGENTS_DIR = _dirs()
    agent_info = {}
    try:
        f_agent = AGENTS_DIR / f"{agent_id}.md"
        if _is_safe_id(agent_id) and f_agent.exists():
            agent_info = _agent_dict(f_agent)
    except Exception:
        pass

    # 1. 分層 team memory 注入
    mem_ctx = ""
    if build_team_mem:
        try:
            mem_ctx = await asyncio.to_thread(build_team_mem, team_id, all_member_ids, agent_id, cwd)
        except Exception:
            pass

    # 2. 讀取 legacy input_memory
    mem_dir = _memory_dir()
    step_input_keys = agent_info.get("memory", [])
    legacy_memory = []
    for key in step_input_keys:
        if not _is_safe_id(key):
            continue
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
        
    return "\n\n".join(prompt_parts)


async def _execute_team_run_core(run_id: str, task: str, model: str, cwd: str) -> None:
    from message_bus import global_bus
    global_bus.publish("team:run_start", {"run_id": run_id, "task": task})
    
    old_snap = _take_workspace_snapshot(cwd)
    _, AGENTS_DIR = _dirs()
    run = _team_runs[run_id]
    steps = run["steps"]
    
    if len(steps) > 15:
        run["status"] = "cancelled"
        run["summary"] = "### [系統熔斷] 步驟數超過最大極限 15 步，已自動中斷以防止死循環。"
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
        return
    team_id = run.get("team_id", "")
    all_member_ids = [s["agent"] for s in steps]
    prev_output = ""

    build_team_mem = None
    try:
        build_team_mem = _get_build_team_memory_context()
    except Exception:
        pass

    # 健檢修復：execution_mode/leader 改用 handle_team_run_post 當下就存進
    # run state 的值（見該函式），不再靠 team_id 回頭查 TEAMS_DIR 的 yaml 檔。
    # 舊寫法對 inline team payload（HR Agent 自動組隊 submitHRTeamRun() 送出
    # 的 plan 沒有 "id" 欄位）永遠查不到 team_info，導致 execution_mode 靜默
    # fallback 成 "parallel"——即使 HR Agent 的 prompt 明確要求「循序執行、
    # 前一位輸出傳給下一位」，實際卻用 asyncio.gather 平行跑，member 之間的
    # input_memory/output_memory 讀寫會有 race（讀到的時候上一位可能還沒寫完）。
    mode = run.get("execution_mode", "parallel")
    permission_mode = run.get("permission_mode", "acceptEdits")
    # 可插拔 agent engine 的 run 層級預設值；個別 agent frontmatter 的
    # `engine:` 宣告優先於這個值（見 _agent_run_capture／engines/registry.py）。
    agent_engine_default = run.get("agent_engine", "")

    if mode == "consensus" and len(steps) >= 2:
        agent_a = steps[0]["agent"]
        agent_b = steps[1]["agent"]
        leader  = run.get("leader") or agent_a

        # 健檢修復：consensus 是固定 4 步驟流程（Coder 草稿 → Auditor 審查 →
        # Coder 修正 → Leader 總結），只會用到 agent_a/agent_b/leader 三個
        # 角色，跟「有幾位成員就有幾個 step」的 parallel/sequential 假設不
        # 相容。team 成員數 ≥3 時，原本的寫法會直接覆寫第 3 位成員原本的
        # steps[2]（step_start 事件回報的 agent 名字跟 steps[2]["agent"] 對
        # 不上，UI 顯示錯的成員名稱），第 4 位以後的成員則永遠停在
        # "pending"，即使整個 run 已經 "done"，看起來像卡住了。改成一開始
        # 就把 run["steps"] 換成 consensus 專用的固定 4 步驟結構，不再重用
        # 其他成員原本的 step slot。
        steps = [
            {"agent": agent_a, "role": "Coder (Initial Draft)",           "status": "pending", "output": ""},
            {"agent": agent_b, "role": "Auditor (Review)",                "status": "pending", "output": ""},
            {"agent": agent_a, "role": "Coder (Revision)",                "status": "pending", "output": ""},
            {"agent": leader,  "role": "Team Leader (Consensus Summary)", "status": "pending", "output": ""},
        ]
        run["steps"] = steps

        # 1. Initial Draft (Agent A)
        _tr_emit(run_id, {"type": "step_start", "step": 0, "agent": agent_a, "role": "Coder (Initial Draft)"})
        mem_a = await _get_agent_memory_prompt(team_id, all_member_ids, agent_a, cwd, build_team_mem)
        prompt_1_parts = []
        if mem_a:
            prompt_1_parts.append(mem_a)
        prompt_1_parts.append(f"[任務]\n{task}\n\n請根據任務，產出你的初始設計方案（Initial Draft）。")
        prompt_1 = "\n\n".join(prompt_1_parts)
        
        draft = await _agent_run_capture(run_id, 0, agent_a, prompt_1, model, cwd, permission_mode, agent_engine_default)
        steps[0]["output"] = draft
        steps[0]["status"] = "done"
        _tr_emit(run_id, {"type": "step_done", "step": 0})
        global_bus.publish("team:step_done", {"run_id": run_id, "step": 0, "agent": agent_a, "output": draft})
        
        if run.get("status") == "cancelled":
            return
            
        # 2. Review / Auditing (Agent B)
        _tr_emit(run_id, {"type": "step_start", "step": 1, "agent": agent_b, "role": "Auditor (Review)"})
        mem_b = await _get_agent_memory_prompt(team_id, all_member_ids, agent_b, cwd, build_team_mem)
        prompt_2_parts = []
        if mem_b:
            prompt_2_parts.append(mem_b)
        prompt_2_parts.append(
            f"[任務]\n{task}\n\n"
            f"[前置 Coder 產出的初始草案]\n{draft}\n\n"
            "你現在是審查與安全稽核專員。請對前置 Coder 產出的草案進行嚴格的漏洞審查，"
            "指出潛在的安全性漏洞或 Bugs，並提供詳細的修改意見。"
        )
        prompt_2 = "\n\n".join(prompt_2_parts)
        
        feedback = await _agent_run_capture(run_id, 1, agent_b, prompt_2, model, cwd, permission_mode, agent_engine_default)
        steps[1]["output"] = feedback
        steps[1]["status"] = "done"
        _tr_emit(run_id, {"type": "step_done", "step": 1})
        global_bus.publish("team:step_done", {"run_id": run_id, "step": 1, "agent": agent_b, "output": feedback})

        if run.get("status") == "cancelled":
            return

        # 3. Revision (Agent A)
        _tr_emit(run_id, {"type": "step_start", "step": 2, "agent": agent_a, "role": "Coder (Revision)"})
        prompt_3_parts = []
        if mem_a:
            prompt_3_parts.append(mem_a)
        prompt_3_parts.append(
            f"[任務]\n{task}\n\n"
            f"[你原先的初始草案]\n{draft}\n\n"
            f"[審查專員的修改意見]\n{feedback}\n\n"
            "請針對審查專員的意見進行深度的修正與答辯，提供最終的優化代碼與設計方案（Revised Draft）。"
        )
        prompt_3 = "\n\n".join(prompt_3_parts)
        
        revised = await _agent_run_capture(run_id, 2, agent_a, prompt_3, model, cwd, permission_mode, agent_engine_default)
        steps[2]["output"] = revised
        steps[2]["status"] = "done"
        _tr_emit(run_id, {"type": "step_done", "step": 2})
        global_bus.publish("team:step_done", {"run_id": run_id, "step": 2, "agent": agent_a, "output": revised})

        if run.get("status") == "cancelled":
            return

        # 4. Consensus Summary (Leader)
        _tr_emit(run_id, {"type": "step_start", "step": 3, "agent": leader, "role": "Team Leader (Consensus Summary)"})
        mem_leader = await _get_agent_memory_prompt(team_id, all_member_ids, leader, cwd, build_team_mem)
        prompt_4_parts = []
        if mem_leader:
            prompt_4_parts.append(mem_leader)
        prompt_4_parts.append(
            f"[任務]\n{task}\n\n"
            f"[初始草案]\n{draft}\n\n"
            f"[審查意見]\n{feedback}\n\n"
            f"[最終修正草案]\n{revised}\n\n"
            "你現在是團隊 Leader。請總結 Coder 與 Auditor 之間的這場技術辯論與修改歷程，"
            "並給出一份完美的最終共識決策與代碼匯總（Consensus Summary）。"
        )
        prompt_4 = "\n\n".join(prompt_4_parts)
        
        summary = await _agent_run_capture(run_id, 3, leader, prompt_4, model, cwd, permission_mode, agent_engine_default)
        steps[3]["output"] = summary
        steps[3]["status"] = "done"
        _tr_emit(run_id, {"type": "step_done", "step": 3})
        global_bus.publish("team:step_done", {"run_id": run_id, "step": 3, "agent": leader, "output": summary})

        run["status"] = "done"
        run["_finished_at"] = time.time()
        run["summary"] = summary
        
        new_snap = _take_workspace_snapshot(cwd)
        run["artifacts"] = _diff_workspace_snapshot(old_snap, new_snap)

        _tr_emit(run_id, {"type": "done", "summary": summary})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": summary})
        
        if team_id and cwd:
            slug = _encode_slug(cwd)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            proj_summary = (
                f"# Team Consensus Run 記錄 — {timestamp}\n\n"
                f"## 任務\n\n{task}\n\n"
                f"## 辯論成員\n\n- Coder: {agent_a}\n- Auditor: {agent_b}\n- Leader: {leader}\n\n"
                f"## 共識摘要\n\n{summary[:1800]}"
            )
            _write_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md", proj_summary)
        
        _cleanup_old_runs()
        return

    if mode == "parallel":
        async def run_parallel_step(i, step):
            if run.get("status") == "cancelled":
                return
            step["status"] = "running"
            _tr_emit(run_id, {"type": "step_start", "step": i,
                               "agent": step["agent"], "role": step["role"]})
            
            agent_id = step["agent"]
            agent_info = {}
            try:
                f_agent = AGENTS_DIR / f"{agent_id}.md"
                if _is_safe_id(agent_id) and f_agent.exists():
                    agent_info = _agent_dict(f_agent)
            except Exception:
                pass

            # 分層 team memory 注入
            mem_ctx = ""
            if build_team_mem:
                try:
                    mem_ctx = await asyncio.to_thread(build_team_mem, team_id, all_member_ids, agent_id, cwd)
                except Exception:
                    pass

            mem_dir = _memory_dir()
            step_input_keys  = [k for k in (step.get("input_memory",  []) or agent_info.get("memory", [])) if _is_safe_id(k)]
            step_output_keys = [k for k in (step.get("output_memory", []) or agent_info.get("output_memory", [])) if _is_safe_id(k)]

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

            prompt_parts.append(f"---\n## 任務\n\n{task}")
            prompt = "\n\n".join(prompt_parts)

            output = await _agent_run_capture(run_id, i, agent_id, prompt, model, cwd, permission_mode, agent_engine_default)
            step["output"] = output
            step["status"] = "done"
            _tr_emit(run_id, {"type": "step_done", "step": i})
            global_bus.publish("team:step_done", {"run_id": run_id, "step": i, "agent": agent_id, "output": output})

            if step_output_keys:
                mem_dir.mkdir(parents=True, exist_ok=True)
                for key in step_output_keys:
                    try:
                        (mem_dir / f"{key}.md").write_text(output, encoding="utf-8")
                    except Exception:
                        pass

        await asyncio.gather(*(run_parallel_step(i, step) for i, step in enumerate(steps)))

    else:
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
                if _is_safe_id(agent_id) and f_agent.exists():
                    agent_info = _agent_dict(f_agent)
            except Exception:
                pass

            # 分層 team memory 注入
            if build_team_mem:
                mem_ctx = await asyncio.to_thread(build_team_mem, team_id, all_member_ids, agent_id, cwd)
            else:
                mem_ctx = ""

            # P2-B2: per-member input_memory keys (from team YAML) take precedence;
            # fallback to agent-level memory keys (from agent frontmatter)
            mem_dir = _memory_dir()
            step_input_keys  = [k for k in (step.get("input_memory",  []) or agent_info.get("memory", [])) if _is_safe_id(k)]
            step_output_keys = [k for k in (step.get("output_memory", []) or agent_info.get("output_memory", [])) if _is_safe_id(k)]

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

            output = await _agent_run_capture(run_id, i, agent_id, prompt, model, cwd, permission_mode, agent_engine_default)
            step["output"] = output
            step["status"] = "done"
            prev_output = output
            _tr_emit(run_id, {"type": "step_done", "step": i})
            global_bus.publish("team:step_done", {"run_id": run_id, "step": i, "agent": agent_id, "output": output})

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
        
        new_snap = _take_workspace_snapshot(cwd)
        run["artifacts"] = _diff_workspace_snapshot(old_snap, new_snap)

        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})

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
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return web.json_response({"error": "invalid id"}, status=400)
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
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return web.json_response({"error": "invalid id"}, status=400)
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
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return web.json_response({"error": "invalid id"}, status=400)
    f = TEAMS_DIR / f"{tid}.yaml"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


# ── Team Run handlers ─────────────────────────────────────────────────────────

# 白名單取兩個引擎各自合法值的聯集（見 engines/claude_engine.py、
# engines/codex_engine.py 的 VALID_PERMISSION_MODES）——同一個 team 可能
# 混用 Claude/Codex 成員，request 層級的 permission_mode 因此可能是任一邊
# 的字彙，不能只認 Claude 那組。個別引擎收到不屬於自己字彙的值時，會在
# run_turn() 內部 fallback 成自己的預設值（見 codex_engine._normalize_sandbox_mode）
# 而不是報錯，所以這裡的白名單只需要擋掉「兩邊都不認得」的垃圾值。
from engines.registry import ENGINES as _ENGINES
_VALID_PERMISSION_MODES = frozenset().union(
    *(getattr(_e, "VALID_PERMISSION_MODES", frozenset()) for _e in _ENGINES.values())
)


async def handle_team_run_post(request: web.Request) -> web.Response:
    TEAMS_DIR, _ = _dirs()
    data    = await request.json()
    team_id = data.get("team_id", "")
    task    = data.get("task", "").strip()
    model   = data.get("model", "")
    cwd     = data.get("cwd", "")
    team_payload = data.get("team", None)
    permission_mode = data.get("permission_mode", "acceptEdits")
    if permission_mode not in _VALID_PERMISSION_MODES:
        return web.json_response({"error": "invalid permission_mode"}, status=400)
    agent_engine = data.get("agent_engine", "")
    if agent_engine and agent_engine not in _ENGINES:
        return web.json_response({"error": "invalid agent_engine"}, status=400)

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

    # 健檢修復：inline team payload 完全來自請求本體，agent id 與
    # input_memory/output_memory key 會被拿去拼檔案路徑，必須在此擋下
    # path traversal（見 _is_safe_id 的說明）。
    for m in team["members"]:
        if not isinstance(m, dict) or not _is_safe_id(m.get("agent", "")):
            return web.json_response({"error": "invalid member agent id"}, status=400)
        for key in list(m.get("input_memory") or []) + list(m.get("output_memory") or []):
            if not _is_safe_id(key):
                return web.json_response({"error": "invalid memory key"}, status=400)

    run_id = uuid.uuid4().hex[:8]
    _team_runs[run_id] = {
        "id":      run_id,
        "team_id": team.get("id", team_id),
        "name":    team.get("name", "Auto Team"),
        "task":    task,
        "cwd":     cwd,
        "status":  "running",
        # 健檢修復：於 dispatch 當下就把 execution_mode/leader 存進 run state，
        # 見 _execute_team_run_core 內的說明。
        "execution_mode": team.get("execution_mode", "parallel"),
        "leader":         team.get("leader", ""),
        # 健檢修復（發現 2）：預設帶 acceptEdits，讓 team member 能真正執行
        # Write/Edit/Bash 等操作，而不是被 headless -p 模式無條件自動拒絕。
        # 見 _agent_run_capture 內的說明。
        "permission_mode": permission_mode,
        # 可插拔 agent engine 的 run 層級預設值（空字串代表沒指定，個別 agent
        # frontmatter 的 engine: 宣告優先於這個值）。見 engines/registry.py。
        "agent_engine": agent_engine,
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
        run["_finished_at"] = time.time()
        _tr_emit(run_id, {"type": "cancelled", "text": "cancelled"})
        _kill_team_run_processes(run_id)
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
