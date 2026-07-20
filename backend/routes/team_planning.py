"""
routes/team_planning.py — "深度組隊"（Leader 協商組隊）規劃流程。

現有的「自動組隊」（routes/agents.py::_run_hr_agent）是一次性生成整份
team plan，沒有挑 Leader、沒有 Leader 參與決定組隊、沒有跟每個成員確認
任務的協商過程。這裡是一個新增的、可選的重量級路徑：

  Step A 產生 Plan 文件（一次通用文字補全，不綁定特定 agent）
  Step B 挑 Leader（同上），並偵測是否已有既有 Team 的 leader 是同一人
  Step C 以 Leader 的身分（_agent_run_capture，走 leader 自己的 persona）
         決定最終團隊組成
  Step D 對每個成員各自跑一個有界（最多 2 輪）的協商迴圈，Leader 提出
         Task、成員回覆 ✅ 確認理解 / ❓ 需要調整，直到共識或到輪數上限
  Step E 把結果寫成 Project 資料夾（plan.md + tasks/<agent>.md），並視
         需要把新團隊寫進 TEAMS_DIR（讓它從此變成一個可重複使用的 Team）

執行本身完全不動——規劃完成後，前端把結果餵給既有、未改動的
POST /api/team/run（routes/teams.py），沿用 sequential/parallel 執行引擎。

沿用 routes/teams.py 既有的 run 狀態／SSE 基礎設施（_team_runs／
_tr_emit／_team_events／_team_queues／_agent_run_capture／
handle_team_run_get／handle_team_run_stream 全部直接 import 重用，不重
寫一份）——GET／stream 端點甚至直接指到 teams.py 的同一個 handler，因為
那兩個 handler 本來就只認 run_id，不關心 run 的內容是「執行」還是
「規劃」。

命名刻意避開「consensus」——routes/teams.py 的 execution_mode 已經有一個
語意完全不同的 "consensus" 模式（Coder→Auditor→Coder→Leader 總結），這裡
統一叫「negotiate / 協商」，避免同一個字在同一個 domain 裡代表兩件事。
"""

from __future__ import annotations

import asyncio
import json
import re as _re
import time
import uuid
from datetime import datetime
from pathlib import Path

from aiohttp import web

from helpers import _team_dict, _write_team_yaml, _agent_dict
from database import _team_memory_dir, _encode_slug, _write_md, _log

from routes.teams import (
    _team_runs, _team_events, _team_queues,
    _tr_emit, _agent_run_capture, _cleanup_old_runs,
    handle_team_run_get, handle_team_run_stream,
    _is_safe_id, _kill_team_run_processes,
)

MAX_NEGOTIATION_ROUNDS = 2

# 比照 teams.py::_execute_team_run 的 300 秒逾時熔斷（見該檔案 349-388 行的
# 說明：早期版本 `except Exception: pass` 把整個規劃流程的例外吃掉，run 的
# status 永遠停在 "running"，SSE 也永遠不會收到終止事件，前端進度會無限期
# 卡住）。深度組隊比一般 team run 多了 Step A/B/C 三次額外的 LLM 呼叫，外加
# Step D 每個成員最多 2 輪、每輪 2 次呼叫的協商迴圈（雖然跨成員平行跑，但
# 單一成員的協商迴圈仍是序列的），給比一般 team run 更寬裕的時限。
PLAN_TEAM_TIMEOUT = 600


def _dirs():
    import database as _db
    return _db.TEAMS_DIR, _db.REGISTRY_AGENTS_DIR


def _list_agents(AGENTS_DIR: Path) -> list[dict]:
    agents_list = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                d = _agent_dict(f)
                agents_list.append({
                    "id": f.stem,
                    "name": d.get("name", f.stem),
                    "description": d.get("description", ""),
                    "skills": d.get("skills", []),
                })
            except Exception:
                pass
    return agents_list


async def _run_dispatcher_prompt(prompt: str, engine_name: str = "") -> tuple[str, str | None]:
    """通用（不綁定特定 agent persona）的一次文字補全——跟
    routes/agents.py::_run_hr_agent 用的是同一套 engine 解析／API key 邏輯
    （沒有共用函式可抽，那邊也是就地寫的，這裡照抄同一個模式維持一致）。
    回傳 (output_text, error)。"""
    from database import get_engine_mode
    from engines.registry import get_engine, resolve_engine_name_gated
    from engines.availability import apply_availability_fallback, NoEngineAvailableError

    import sys
    main = None
    for name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(name)
        if mod and hasattr(mod, "CLAUDE_BIN"):
            main = mod
            break
    resolve_key = getattr(main, "_resolve_api_key", lambda: "") if main else (lambda: "")
    resolve_codex_key = getattr(main, "_resolve_codex_api_key", lambda: "") if main else (lambda: "")

    mode = get_engine_mode()
    allowed = frozenset({mode}) if mode in ("claude", "codex") else frozenset({"claude", "codex"})
    preferred_name = resolve_engine_name_gated("", engine_name, mode)
    try:
        final_name, _notice = await apply_availability_fallback(preferred_name, allowed)
    except NoEngineAvailableError as e:
        return "", f"派發失敗：{e}"
    engine = get_engine(final_name)
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
                prompt=prompt, cwd=str(Path.home()), model="", permission_mode="",
                resume_session_id=None, api_key=engine_api_key, on_text=_noop_on_text,
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return "", "逾時（90 秒）"
    except Exception as e:
        return "", str(e)

    if result.error:
        return "", result.error
    return result.output.strip(), None


def _extract_json(text: str) -> dict | None:
    s = _re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    s = _re.sub(r"\s*```$", "", s.strip()).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            pass
    return None


def _find_existing_led_team(TEAMS_DIR: Path, leader_id: str) -> dict | None:
    """掃描 TEAMS_DIR，找出 leader 欄位等於這個 agent 的既有 Team——如果
    這個 Agent 已經帶過團隊，優先把既有班底當候選成員給 Leader 參考。"""
    if not TEAMS_DIR.exists():
        return None
    for f in sorted(TEAMS_DIR.glob("*.yaml"), key=lambda p: p.name.lower()):
        try:
            team = _team_dict(f)
        except Exception:
            continue
        if team.get("leader") == leader_id:
            team["id"] = f.stem
            return team
    return None


async def _negotiate_task(
    run_id: str, step_idx: int,
    leader_id: str, member_id: str, role: str,
    plan_doc: str, model: str, cwd: str,
    permission_mode: str, agent_engine_default: str,
) -> dict:
    """Leader 跟單一成員的有界協商迴圈。每輪兩次 _agent_run_capture 呼叫
    （Leader 提／修 Task → 成員回應）。成員回應以「✅」開頭視為達成共識，
    立刻結束；到 MAX_NEGOTIATION_ROUNDS 輪都沒共識，就用最後一版 Task，
    標記 consensus=False，交給使用者自己審閱決定要不要照跑。"""
    feedback = ""
    task_doc = ""
    member_reply = ""
    for round_idx in range(MAX_NEGOTIATION_ROUNDS):
        leader_prompt_parts = [
            f"[專案計畫]\n{plan_doc}",
            f"[這位成員] {member_id}，職責：{role}",
            "你是這個 Team 的 Leader。請針對這位成員產出具體的 Task："
            "要做什麼（具體到可以直接開始執行）、需要交接/參考的產出、"
            "完成的驗收標準。只輸出 Task 內容本身，不要加多餘的開場白。",
        ]
        if feedback:
            leader_prompt_parts.append(f"[對方的回饋]\n{feedback}\n\n請根據回饋修正 Task。")
        leader_prompt = "\n\n".join(leader_prompt_parts)

        task_doc = await _agent_run_capture(
            run_id, step_idx, leader_id, leader_prompt, model, cwd, permission_mode, agent_engine_default,
        )

        member_prompt = (
            f"你是 {member_id}。Team Leader 交給你以下 Task：\n\n{task_doc}\n\n"
            "請確認是否完全理解並同意這個 Task 的範圍與流程。\n"
            "同意請以「✅ 確認理解」開頭；需要調整請以「❓ 需要調整」開頭，並具體說明哪裡需要修改。"
        )
        member_reply = await _agent_run_capture(
            run_id, step_idx, member_id, member_prompt, model, cwd, permission_mode, agent_engine_default,
        )

        if member_reply.strip().startswith("✅"):
            return {"agent": member_id, "role": role, "task_doc": task_doc,
                    "consensus": True, "rounds": round_idx + 1}
        feedback = member_reply

    return {"agent": member_id, "role": role, "task_doc": task_doc,
            "consensus": False, "rounds": MAX_NEGOTIATION_ROUNDS,
            "last_feedback": member_reply}


async def _execute_plan_team_run(run_id: str, task: str, model: str, cwd: str,
                                  engine_name: str, permission_mode: str, agent_engine_default: str) -> None:
    from message_bus import global_bus
    TEAMS_DIR, AGENTS_DIR = _dirs()
    run = _team_runs[run_id]

    agents_list = _list_agents(AGENTS_DIR)
    if not agents_list:
        run["status"] = "error"
        run["_finished_at"] = time.time()
        run["summary"] = "尚未建立任何 Agent，無法規劃團隊。"
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
        return

    registry_str = json.dumps(agents_list, ensure_ascii=False, indent=2)

    # ── Step A：產生 Plan 文件 ──────────────────────────────────────────
    _tr_emit(run_id, {"type": "plan_step", "phase": "plan", "status": "running"})
    plan_prompt = (
        f"請根據以下任務描述，撰寫一份簡潔的專案計畫文件（Markdown，200-400 字）：\n\n"
        f"{task}\n\n"
        "內容應包含：目標、需要完成的主要工作項目、可能的技術/流程考量。"
        "只輸出計畫文件本身，不要加開場白或結語。"
    )
    plan_doc, err = await _run_dispatcher_prompt(plan_prompt, engine_name)
    if err:
        run["status"] = "error"
        run["_finished_at"] = time.time()
        run["summary"] = f"產生 Plan 失敗：{err}"
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
        return
    run["plan_doc"] = plan_doc
    _tr_emit(run_id, {"type": "plan_step", "phase": "plan", "status": "done", "plan_doc": plan_doc})

    # ── Step B：挑 Leader ────────────────────────────────────────────────
    _tr_emit(run_id, {"type": "plan_step", "phase": "leader", "status": "running"})
    leader_prompt = (
        f"[專案計畫]\n{plan_doc}\n\n"
        f"[可用 Agent 列表]\n{registry_str}\n\n"
        "請從上述列表中，挑選一位最適合擔任這個專案 Team Leader 的 Agent。"
        "只能從列表中挑選，不要捏造不存在的 Agent id。"
        "只輸出一個純 JSON 物件：{\"leader\": \"agent id\", \"reason\": \"挑選理由\"}，"
        "不要包含 markdown 標記或其他文字。"
    )
    leader_raw, err = await _run_dispatcher_prompt(leader_prompt, engine_name)
    if err:
        run["status"] = "error"
        run["_finished_at"] = time.time()
        run["summary"] = f"挑選 Leader 失敗：{err}"
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
        global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
        return
    leader_data = _extract_json(leader_raw) or {}
    leader_id = leader_data.get("leader", "")
    valid_ids = {a["id"] for a in agents_list}
    if leader_id not in valid_ids:
        # 模型亂填或漏填：退而求其次，選清單第一位，不整個失敗。
        leader_id = agents_list[0]["id"]
    reused_team = _find_existing_led_team(TEAMS_DIR, leader_id)
    run["leader"] = leader_id
    run["reused_team_id"] = reused_team["id"] if reused_team else ""
    _tr_emit(run_id, {
        "type": "plan_step", "phase": "leader", "status": "done",
        "leader": leader_id, "reason": leader_data.get("reason", ""),
        "reused_team_id": run["reused_team_id"],
    })

    # ── Step C：Leader 決定團隊組成 ──────────────────────────────────────
    _tr_emit(run_id, {"type": "plan_step", "phase": "compose", "status": "running"})
    compose_prompt_parts = [
        f"[專案計畫]\n{plan_doc}",
        f"[可用 Agent 列表]\n{registry_str}",
    ]
    if reused_team:
        candidate_members = ", ".join(m.get("agent", "") for m in reused_team.get("members", []))
        compose_prompt_parts.append(
            f"[你既有的團隊班底，僅供參考，不強制沿用] {candidate_members}"
        )
    compose_prompt_parts.append(
        "你是這個專案的 Team Leader。請決定最終的團隊組成（可以包含或不包含你自己）。"
        "只能從可用 Agent 列表中挑選，不要捏造不存在的 Agent id。"
        "只輸出一個純 JSON 物件：\n"
        '{"members": [{"agent": "agent id", "role": "該成員的具體職責"}], "reasoning": "組隊理由"}\n'
        "不要包含 markdown 標記或其他文字。"
    )
    compose_prompt = "\n\n".join(compose_prompt_parts)
    compose_raw = await _agent_run_capture(
        run_id, -1, leader_id, compose_prompt, model, cwd, permission_mode, agent_engine_default,
    )
    compose_data = _extract_json(compose_raw) or {}
    members = []
    seen_agents: set[str] = set()
    for m in (compose_data.get("members") or []):
        if not isinstance(m, dict):
            continue
        agent_id = m.get("agent", "")
        # 模型偶爾會把同一個 agent 列兩次（例如給了兩個不同角色描述）——
        # Step E 之後會用 agent id 當檔名/YAML member key，重複的話後面
        # 會悄悄蓋掉前面協商定案的 Task，這裡先去重，只保留第一次出現的。
        if agent_id not in valid_ids or not _is_safe_id(agent_id) or agent_id in seen_agents:
            continue
        seen_agents.add(agent_id)
        members.append(m)
    if not members:
        members = [{"agent": leader_id, "role": "獨立完成整個任務"}]
    _tr_emit(run_id, {
        "type": "plan_step", "phase": "compose", "status": "done",
        "members": members, "reasoning": compose_data.get("reasoning", ""),
    })

    # ── Step D：Leader 與每個成員逐一協商 Task（平行、各自獨立） ─────────
    _tr_emit(run_id, {"type": "plan_step", "phase": "negotiate", "status": "running"})

    async def _negotiate_one(idx: int, m: dict):
        _tr_emit(run_id, {"type": "negotiate_start", "agent": m["agent"], "role": m["role"]})
        result = await _negotiate_task(
            run_id, idx, leader_id, m["agent"], m["role"], plan_doc,
            model, cwd, permission_mode, agent_engine_default,
        )
        _tr_emit(run_id, {"type": "negotiate_done", **result})
        return result

    negotiated = await asyncio.gather(*(_negotiate_one(i, m) for i, m in enumerate(members)))
    run["members"] = negotiated
    _tr_emit(run_id, {"type": "plan_step", "phase": "negotiate", "status": "done", "members": negotiated})

    # ── Step E：寫入 Project 資料夾 + 視需要建立新 Team ──────────────────
    team_id = run["reused_team_id"]
    if not team_id:
        raw_name = f"auto-{leader_id}-team"
        team_id = _re.sub(r"[^\w-]", "-", raw_name).lower().strip("-") or "auto-team"
        base_id, n = team_id, 2
        while (TEAMS_DIR / f"{team_id}.yaml").exists():
            team_id = f"{base_id}-{n}"
            n += 1
        _write_team_yaml(TEAMS_DIR / f"{team_id}.yaml", {
            "name": f"{leader_id} 的團隊",
            "description": compose_data.get("reasoning", "") or "自動深度組隊產生",
            "leader": leader_id,
            "members": [{"agent": m["agent"], "role": m["role"]} for m in negotiated],
            "execution_mode": "sequential",
            "favorite": False,
        })
    run["team_id"] = team_id

    if cwd:
        slug = _encode_slug(cwd)
        project_dir = _team_memory_dir(team_id) / "projects" / slug
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        _write_md(project_dir / "plan.md", f"# Plan — {timestamp}\n\n## 任務\n\n{task}\n\n## 計畫\n\n{plan_doc}")
        for m in negotiated:
            consensus_label = "✅ 已達成共識" if m["consensus"] else f"⚠ 未達成共識（談了 {m['rounds']} 輪）"
            _write_md(
                project_dir / "tasks" / f"{m['agent']}.md",
                f"# Task — {m['agent']}\n\n{consensus_label}\n\n## 職責\n\n{m['role']}\n\n## Task 內容\n\n{m['task_doc']}",
            )
        run["project_path"] = str(project_dir)
    else:
        run["project_path"] = ""

    run["status"] = "done"
    run["_finished_at"] = time.time()
    run["summary"] = f"已規劃完成：Leader={leader_id}，{len(negotiated)} 位成員"
    _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
    global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
    _cleanup_old_runs()


async def _execute_plan_team_run_guarded(run_id: str, task: str, model: str, cwd: str,
                                          engine_name: str, permission_mode: str, agent_engine_default: str) -> None:
    """比照 teams.py::_execute_team_run 的逾時＋例外防護，直接呼叫
    _execute_plan_team_run 沒有這層保護的話，任何未預期例外（subprocess
    啟動失敗、Project 資料夾寫入權限錯誤等）都會讓 run 卡在 "running" 狀態
    永遠出不來，SSE 也不會收到終止事件——見 teams.py 367-374 行對同一個
    問題的說明（那邊已經修過一次）。"""
    try:
        await asyncio.wait_for(
            _execute_plan_team_run(run_id, task, model, cwd, engine_name, permission_mode, agent_engine_default),
            timeout=PLAN_TEAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        run = _team_runs.get(run_id)
        if run and run.get("status") == "running":
            run["status"] = "cancelled"
            run["_finished_at"] = time.time()
            run["summary"] = f"### [系統熔斷] 規劃時間超過 {PLAN_TEAM_TIMEOUT} 秒，已自動超時中斷。"
            _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
            from message_bus import global_bus
            global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
            _kill_team_run_processes(run_id)
    except Exception as e:
        _log(f"[PlanTeamRun {run_id}] 規劃時發生未預期例外：{e!r}")
        run = _team_runs.get(run_id)
        if run and run.get("status") == "running":
            run["status"] = "error"
            run["_finished_at"] = time.time()
            run["summary"] = f"### [規劃錯誤] {e}"
            _tr_emit(run_id, {"type": "done", "summary": run["summary"]})
            from message_bus import global_bus
            global_bus.publish("team:run_done", {"run_id": run_id, "summary": run["summary"]})
            _kill_team_run_processes(run_id)


async def handle_plan_team_post(request: web.Request) -> web.Response:
    data = await request.json()
    task = data.get("task", "").strip()
    if not task:
        return web.json_response({"error": "task required"}, status=400)
    cwd = data.get("cwd", "")
    model = data.get("model", "")
    engine_name = data.get("engine", "")
    permission_mode = data.get("permission_mode", "acceptEdits")
    # 健檢：Step A/B（_run_dispatcher_prompt）用 engine_name；Step C/D（真的
    # 走 agent persona 的 _agent_run_capture）原本各自獨立收 agent_engine，
    # 沒指定時會落到 resolve_engine_name_gated() 的 DEFAULT_ENGINE_NAME
    # （目前是 codex），即使使用者在 Step A/B 明確指定要用 claude，Step C/D
    # 還是靜默切去 codex——如果那個環境沒裝/沒登入 codex，會直接卡在
    # subprocess 讀不到任何輸出，整個規劃流程看起來像卡死。沒有明確指定
    # agent_engine 時，預設沿用 engine_name，讓整個規劃流程用同一個引擎。
    agent_engine_default = data.get("agent_engine", "") or engine_name

    run_id = uuid.uuid4().hex[:8]
    _team_runs[run_id] = {
        "id": run_id, "kind": "planning", "task": task, "cwd": cwd,
        "status": "running", "steps": [], "summary": "",
        "leader": "", "reused_team_id": "", "team_id": "",
        "plan_doc": "", "members": [], "project_path": "",
    }
    _team_events[run_id] = []
    _team_queues[run_id] = []

    asyncio.create_task(_execute_plan_team_run_guarded(
        run_id, task, model, cwd, engine_name, permission_mode, agent_engine_default,
    ))
    return web.json_response({"ok": True, "run_id": run_id})


def register_team_planning_routes(app: web.Application, cors_add) -> None:
    cors_add(app.router.add_post("/api/hr/plan-team", handle_plan_team_post))
    cors_add(app.router.add_get("/api/hr/plan-team/{run_id}", handle_team_run_get))
    cors_add(app.router.add_get("/api/hr/plan-team/{run_id}/stream", handle_team_run_stream))
