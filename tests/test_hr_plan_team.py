"""
tests/test_hr_plan_team.py — routes/team_planning.py 的「深度組隊」規劃流程。

比照 tests/test_hr_dispatch_execution_mode.py 的手法：monkeypatch
claude_engine.run_turn，讓假引擎依 prompt 內容回傳對應的假輸出，不打真實
API。

注意：team_planning._dirs() 讀的是 database.TEAMS_DIR /
database.REGISTRY_AGENTS_DIR（不是 database.AGENTS_DIR）——這裡直接
monkeypatch 正確的屬性，不透過 conftest.py 的 app/client fixture（那個
會啟動完整 aiohttp TestServer，對這裡純函式層級的測試太重）。
"""
import json
import tempfile
from pathlib import Path

import pytest

import database
import routes.team_planning as tp
from engines import claude_engine
from engines.base import RunResult


def _make_dirs():
    base = Path(tempfile.mkdtemp())
    agents_dir = base / "agents"
    agents_dir.mkdir()
    teams_dir = base / "teams"
    teams_dir.mkdir()
    (agents_dir / "leader-agent.md").write_text(
        "---\nname: leader-agent\ndescription: 專案負責人\n---\n\n你是專案負責人。\n", encoding="utf-8"
    )
    (agents_dir / "member-agent.md").write_text(
        "---\nname: member-agent\ndescription: 執行成員\n---\n\n你是執行成員。\n", encoding="utf-8"
    )
    return agents_dir, teams_dir


@pytest.fixture
def isolated_dirs(monkeypatch):
    agents_dir, teams_dir = _make_dirs()
    monkeypatch.setattr(database, "REGISTRY_AGENTS_DIR", agents_dir)
    monkeypatch.setattr(database, "TEAMS_DIR", teams_dir)
    return agents_dir, teams_dir


def _fake_run_turn_factory(responses_by_marker: dict[str, "str | list[str]"]):
    """依 prompt 內容找第一個符合的 marker，回傳對應的假輸出。value 可以是
    單一字串（每次都回傳同一個）或字串列表（依呼叫次數依序消耗，最後一個
    值之後重複使用最後一個）。"""
    call_counts: dict[str, int] = {}

    async def _fake_run_turn(**kwargs):
        prompt = kwargs.get("prompt", "")
        for marker, resp in responses_by_marker.items():
            if marker in prompt:
                if isinstance(resp, list):
                    idx = call_counts.get(marker, 0)
                    call_counts[marker] = idx + 1
                    value = resp[min(idx, len(resp) - 1)]
                else:
                    value = resp
                return RunResult(output=value)
        raise AssertionError(f"no fake response configured for prompt: {prompt[:200]}")

    return _fake_run_turn


PLAN_MARKER = "請根據以下任務描述，撰寫一份簡潔的專案計畫文件"
LEADER_MARKER = "請從上述列表中，挑選一位最適合擔任這個專案 Team Leader"
COMPOSE_MARKER = "你是這個專案的 Team Leader。請決定最終的團隊組成"
PROPOSE_MARKER = "請針對這位成員產出具體的 Task"
REPLY_MARKER = "Team Leader 交給你以下 Task"


async def _run_and_wait(run_id: str, task: str, model: str, cwd: str,
                         engine_name: str = "claude", permission_mode: str = "acceptEdits",
                         agent_engine_default: str | None = None):
    # 比照 handle_plan_team_post 的健檢邏輯：沒有明確指定 agent_engine 時
    # 沿用 engine_name，避免 Step C/D 靜默落到 resolve_engine_name_gated()
    # 的全域預設引擎（目前是 codex），在沒裝/沒登入 codex 的測試環境裡卡死。
    if agent_engine_default is None:
        agent_engine_default = engine_name
    tp._team_runs[run_id] = {
        "id": run_id, "kind": "planning", "task": task, "cwd": cwd,
        "status": "running", "steps": [], "summary": "",
        "leader": "", "reused_team_id": "", "team_id": "",
        "plan_doc": "", "members": [], "project_path": "",
    }
    tp._team_events[run_id] = []
    tp._team_queues[run_id] = []
    await tp._execute_plan_team_run(run_id, task, model, cwd, engine_name, permission_mode, agent_engine_default)
    return tp._team_runs[run_id]


async def test_full_flow_reaches_consensus_on_first_round(isolated_dirs, monkeypatch, tmp_path):
    fake_run_turn = _fake_run_turn_factory({
        PLAN_MARKER: "# Plan\n\n目標是做點什麼。",
        LEADER_MARKER: json.dumps({"leader": "leader-agent", "reason": "最適合"}, ensure_ascii=False),
        COMPOSE_MARKER: json.dumps({
            "members": [{"agent": "member-agent", "role": "執行細節"}],
            "reasoning": "分工理由",
        }, ensure_ascii=False),
        PROPOSE_MARKER: "## Task\n\n請完成 X。",
        REPLY_MARKER: "✅ 確認理解，同意這個 Task。",
    })
    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    run = await _run_and_wait("run1", "做一件事", "", str(tmp_path))

    assert run["status"] == "done"
    assert run["leader"] == "leader-agent"
    assert run["reused_team_id"] == ""
    assert run["team_id"]
    assert len(run["members"]) == 1
    m = run["members"][0]
    assert m["agent"] == "member-agent"
    assert m["consensus"] is True
    assert m["rounds"] == 1

    project_path = Path(run["project_path"])
    assert (project_path / "plan.md").exists()
    assert (project_path / "tasks" / "member-agent.md").exists()
    task_content = (project_path / "tasks" / "member-agent.md").read_text(encoding="utf-8")
    assert "已達成共識" in task_content

    _, teams_dir = isolated_dirs
    team_files = list(teams_dir.glob("*.yaml"))
    assert len(team_files) == 1


async def test_negotiation_stops_at_max_rounds_without_consensus(isolated_dirs, monkeypatch, tmp_path):
    fake_run_turn = _fake_run_turn_factory({
        PLAN_MARKER: "# Plan\n\n目標。",
        LEADER_MARKER: json.dumps({"leader": "leader-agent", "reason": "r"}, ensure_ascii=False),
        COMPOSE_MARKER: json.dumps({
            "members": [{"agent": "member-agent", "role": "執行"}], "reasoning": "r",
        }, ensure_ascii=False),
        PROPOSE_MARKER: ["## Task v1", "## Task v2"],
        REPLY_MARKER: "❓ 需要調整，範圍不清楚。",
    })
    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    run = await _run_and_wait("run2", "做一件事", "", str(tmp_path))

    assert run["status"] == "done"
    m = run["members"][0]
    assert m["consensus"] is False
    assert m["rounds"] == tp.MAX_NEGOTIATION_ROUNDS
    assert m["task_doc"] == "## Task v2"  # 用了最後一輪修正過的版本

    project_path = Path(run["project_path"])
    task_content = (project_path / "tasks" / "member-agent.md").read_text(encoding="utf-8")
    assert "未達成共識" in task_content


async def test_reuses_existing_team_led_by_same_agent(isolated_dirs, monkeypatch, tmp_path):
    agents_dir, teams_dir = isolated_dirs
    from helpers import _write_team_yaml
    _write_team_yaml(teams_dir / "existing-team.yaml", {
        "name": "既有團隊", "description": "d", "leader": "leader-agent",
        "members": [{"agent": "member-agent", "role": "舊角色"}],
        "execution_mode": "sequential", "favorite": False,
    })

    fake_run_turn = _fake_run_turn_factory({
        PLAN_MARKER: "# Plan",
        LEADER_MARKER: json.dumps({"leader": "leader-agent", "reason": "r"}, ensure_ascii=False),
        COMPOSE_MARKER: json.dumps({
            "members": [{"agent": "member-agent", "role": "新角色"}], "reasoning": "r",
        }, ensure_ascii=False),
        PROPOSE_MARKER: "## Task",
        REPLY_MARKER: "✅ 確認理解",
    })
    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    run = await _run_and_wait("run3", "做一件事", "", str(tmp_path))

    assert run["reused_team_id"] == "existing-team"
    assert run["team_id"] == "existing-team"
    # 沒有另外新建一份 team yaml
    assert sorted(p.name for p in teams_dir.glob("*.yaml")) == ["existing-team.yaml"]


async def test_invalid_leader_falls_back_to_first_agent(isolated_dirs, monkeypatch, tmp_path):
    """模型亂填一個不存在的 agent id 當 leader 時，不整個失敗，退而求其次
    選清單第一位（依檔名排序，leader-agent.md < member-agent.md）。compose
    沒給任何合法成員時，退而求其次讓 leader 自己獨立完成，此時 leader 會
    跟自己協商（member_id == leader_id），一樣走完整個協商流程。"""
    fake_run_turn = _fake_run_turn_factory({
        PLAN_MARKER: "# Plan",
        LEADER_MARKER: json.dumps({"leader": "no-such-agent", "reason": "r"}, ensure_ascii=False),
        COMPOSE_MARKER: json.dumps({"members": [], "reasoning": "r"}, ensure_ascii=False),
        PROPOSE_MARKER: "## Task",
        REPLY_MARKER: "✅ 確認理解",
    })
    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    run = await _run_and_wait("run4", "做一件事", "", str(tmp_path))

    assert run["status"] == "done"
    assert run["leader"] == "leader-agent"  # 清單第一位（依檔名排序）
    # compose 沒給任何合法成員時，退而求其次讓 leader 自己獨立完成
    assert len(run["members"]) == 1
    assert run["members"][0]["agent"] == run["leader"]


async def test_no_agents_registered_errors_cleanly(monkeypatch, tmp_path):
    empty_agents = Path(tempfile.mkdtemp())
    empty_teams = Path(tempfile.mkdtemp())
    monkeypatch.setattr(database, "REGISTRY_AGENTS_DIR", empty_agents)
    monkeypatch.setattr(database, "TEAMS_DIR", empty_teams)

    run = await _run_and_wait("run5", "做一件事", "", str(tmp_path))

    assert run["status"] == "error"
    assert "尚未建立任何 Agent" in run["summary"]


async def test_duplicate_compose_members_are_deduped(isolated_dirs, monkeypatch, tmp_path):
    """Leader 的 compose 回應把同一個 agent 列兩次時（不同角色描述），只
    保留第一次出現的——否則 Step E 寫 tasks/<agent>.md 時後面會悄悄蓋掉
    前面協商定案的 Task。"""
    fake_run_turn = _fake_run_turn_factory({
        PLAN_MARKER: "# Plan",
        LEADER_MARKER: json.dumps({"leader": "leader-agent", "reason": "r"}, ensure_ascii=False),
        COMPOSE_MARKER: json.dumps({
            "members": [
                {"agent": "member-agent", "role": "角色一"},
                {"agent": "member-agent", "role": "角色二"},
            ],
            "reasoning": "r",
        }, ensure_ascii=False),
        PROPOSE_MARKER: "## Task",
        REPLY_MARKER: "✅ 確認理解",
    })
    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    run = await _run_and_wait("run6", "做一件事", "", str(tmp_path))

    assert run["status"] == "done"
    assert len(run["members"]) == 1
    assert run["members"][0]["agent"] == "member-agent"


async def test_unhandled_exception_marks_run_error_not_stuck_running(isolated_dirs, monkeypatch, tmp_path):
    """比照 teams.py::_execute_team_run 已經修過一次的同一種 bug：Step
    C/D（_agent_run_capture）拋出的未預期例外，如果沒有外層防護，會讓 run
    卡在 status="running" 永遠出不來，SSE 也不會收到終止事件。這裡驗證
    _execute_plan_team_run_guarded 有把它接住、轉成 status="error"。"""
    async def _fake_run_turn_boom_at_compose(**kwargs):
        prompt = kwargs.get("prompt", "")
        if PLAN_MARKER in prompt:
            return RunResult(output="# Plan")
        if LEADER_MARKER in prompt:
            return RunResult(output=json.dumps({"leader": "leader-agent", "reason": "r"}, ensure_ascii=False))
        if COMPOSE_MARKER in prompt:
            raise RuntimeError("boom-at-compose")
        raise AssertionError(f"no fake response configured for prompt: {prompt[:200]}")

    monkeypatch.setattr(claude_engine, "run_turn", _fake_run_turn_boom_at_compose)

    run_id = "run7"
    tp._team_runs[run_id] = {
        "id": run_id, "kind": "planning", "task": "做一件事", "cwd": str(tmp_path),
        "status": "running", "steps": [], "summary": "",
        "leader": "", "reused_team_id": "", "team_id": "",
        "plan_doc": "", "members": [], "project_path": "",
    }
    tp._team_events[run_id] = []
    tp._team_queues[run_id] = []

    await tp._execute_plan_team_run_guarded(run_id, "做一件事", "", str(tmp_path), "claude", "acceptEdits", "claude")

    run = tp._team_runs[run_id]
    assert run["status"] == "error"
    assert "boom-at-compose" in run["summary"]
    assert run.get("_finished_at")
