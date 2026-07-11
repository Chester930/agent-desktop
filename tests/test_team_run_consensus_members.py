"""2026-07-10 team 協作優化健檢：consensus 執行模式在成員數 >2 時會錯亂。

_execute_team_run_core() 的 consensus 分支原本只處理前 2 位成員
（agent_a = steps[0], agent_b = steps[1]），第 3、4 步驟用
`if len(steps) < 3/4: steps.append(...)` 才新增。team 本來就有 >=3 位
成員時（handle_team_run_post 已照 member 數量建好對應筆數的 steps），
第 3 位成員原本的 steps[2] 會被直接覆寫成 agent_a 的 revision 結果，
第 4 位以後的成員永遠停在 "pending"，即使整個 run 已經 "done"。

修法：consensus 分支一開始就把 run["steps"] 換成專用的固定 4 步驟結構
（Coder 草稿 / Auditor 審查 / Coder 修正 / Leader 總結），不再挪用其他
成員原本的 step slot。
"""
import pytest

import routes.teams as teams_module

pytestmark = pytest.mark.asyncio


async def test_consensus_with_four_members_produces_exactly_four_correct_steps(monkeypatch):
    call_log = []

    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits"):
        call_log.append((step_idx, agent_id))
        return f"output-{step_idx}-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    run_id = "run-consensus-4-members"
    # team has 4 real members — previously steps[2]/steps[3] belonged to
    # ThirdAgent/FourthAgent, not to the consensus Coder/Leader roles
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "team_id": "",
        "status": "running",
        "execution_mode": "consensus",
        "leader": "LeaderAgent",
        "steps": [
            {"agent": "CoderAgent",   "role": "Coder",   "status": "pending", "output": ""},
            {"agent": "AuditorAgent", "role": "Auditor", "status": "pending", "output": ""},
            {"agent": "ThirdAgent",   "role": "Extra",   "status": "pending", "output": ""},
            {"agent": "FourthAgent",  "role": "Extra",   "status": "pending", "output": ""},
        ],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run_core(run_id, "task", "haiku", "/tmp")

    run = teams_module._team_runs[run_id]
    steps = run["steps"]

    # exactly 4 steps — no leftover "pending" slot for members 3/4
    assert len(steps) == 4
    assert all(s["status"] == "done" for s in steps)

    # step 2 must belong to CoderAgent's revision, not to ThirdAgent
    assert steps[2]["agent"] == "CoderAgent"
    # step 3 must belong to the real leader, not to FourthAgent
    assert steps[3]["agent"] == "LeaderAgent"

    assert run["status"] == "done"

    # agent_run_capture must never have been called for the extra members —
    # they were never part of the consensus debate
    called_agents = {agent for _, agent in call_log}
    assert "ThirdAgent" not in called_agents
    assert "FourthAgent" not in called_agents
