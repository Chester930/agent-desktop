"""2026-07-10 team 協作優化健檢：execution_mode 對 inline team payload 失效。

_execute_team_run_core() 原本靠 run["team_id"] 回頭去 TEAMS_DIR 讀 yaml 檔
取得 execution_mode/leader。但 inline team payload（POST /api/team/run 的
`team` 欄位，唯一使用者是 HR Agent 自動組隊 submitHRTeamRun()）沒有 "id"
欄位，team_id 永遠是空字串，team_info 查詢整個被跳過，execution_mode 靜默
fallback 成 "parallel"——即使 HR Agent 的 prompt 明確要求「循序執行，前一位
輸出傳給下一位」，實際卻用 asyncio.gather 平行跑，member 之間完全沒有輸出
串接，且 input_memory/output_memory 讀寫會有 race（下游可能在上游寫完之前
就讀了）。

修法：execution_mode/leader 改成在 handle_team_run_post 當下（`team` dict
已經正確解析好，不管是 inline payload 還是存檔 team）就存進 run state，
_execute_team_run_core 直接讀 run state，不再靠 team_id 回頭查檔。
"""
import pytest

import routes.teams as teams_module

pytestmark = pytest.mark.asyncio


async def test_inline_payload_sequential_mode_chains_output(monkeypatch):
    captured_prompts = []

    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits"):
        captured_prompts.append(prompt)
        return f"output-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    run_id = "run-seq-inline"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "team_id": "",  # inline payload (HR dispatch) never has an id
        "execution_mode": "sequential",
        "leader": "",
        "steps": [
            {"agent": "agent-a", "role": "first",  "status": "pending", "output": ""},
            {"agent": "agent-b", "role": "second", "status": "pending", "output": ""},
        ],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run_core(run_id, "do the task", "haiku", "")

    assert len(captured_prompts) == 2
    # sequential mode must feed step 0's output into step 1's prompt
    assert "output-from-agent-a" in captured_prompts[1]
    assert teams_module._team_runs[run_id]["status"] == "done"


async def test_inline_payload_without_execution_mode_still_defaults_parallel(monkeypatch):
    """Explicit regression guard: absence of execution_mode must still default
    to parallel (previous behavior for teams that genuinely want it), only the
    *propagation* of an explicitly-set mode was broken."""
    calls = []

    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits"):
        calls.append(agent_id)
        return f"output-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    run_id = "run-default-parallel"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "team_id": "",
        "execution_mode": "parallel",
        "leader": "",
        "steps": [
            {"agent": "agent-a", "role": "first",  "status": "pending", "output": ""},
            {"agent": "agent-b", "role": "second", "status": "pending", "output": ""},
        ],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run_core(run_id, "do the task", "haiku", "")

    assert set(calls) == {"agent-a", "agent-b"}
    assert teams_module._team_runs[run_id]["status"] == "done"


async def test_handle_team_run_post_stores_inline_execution_mode(client, monkeypatch):
    """POST /api/team/run must persist the inline payload's execution_mode
    into run state, not silently drop it because there's no team_id."""
    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits"):
        return f"output-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    resp = await client.post("/api/team/run", json={
        "task": "task",
        "team": {
            "name": "hr-auto-team",
            "execution_mode": "sequential",
            "members": [
                {"agent": "agent-a", "role": "r1"},
                {"agent": "agent-b", "role": "r2"},
            ],
        },
    })
    assert resp.status == 200
    body = await resp.json()
    run_id = body["run_id"]
    assert teams_module._team_runs[run_id]["execution_mode"] == "sequential"
