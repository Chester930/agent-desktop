"""2026-07-10：使用者要求「同一個 team 裡混用 Claude/Codex 成員」要能真的
運作，這是可插拔 agent engine 架構存在的核心理由之一。用真實帳號（Claude
CLI + 已登入的真實 Codex CLI 帳號）在 parallel 模式跑過一次端對端驗證：
兩個 agent，一個 frontmatter 宣告 `engine: codex`、一個宣告
`engine: claude`，同一個 team run 裡各自正確路由到對應的 CLI（codex 側的
回覆帶著只有 codex_engine.py 會產生的 `[codex: ...]` 提示字樣，claude 側
乾淨沒有這個字樣，兩邊都答對了各自被問到的「你是哪個引擎」）。

這個測試把同一個場景用 mock 固定下來，不需要真的呼叫任何 CLI 就能在 CI
裡驗證「per-agent engine 路由在同一個 team run 裡正確運作」這個行為，
成本低、跑得快，永久保護這個行為不會回歸。
"""
import pytest

from engines.base import RunResult

import routes.teams as teams_module
from engines import claude_engine, codex_engine

pytestmark = pytest.mark.asyncio


def _write_agent(agents_dir, agent_id: str, engine: str) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\nengine: {engine}\n---\n\nbody\n",
        encoding="utf-8",
    )


async def test_parallel_team_routes_each_member_to_its_own_engine(monkeypatch, tmp_path):
    import database
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "mixed-codex-agent", "codex")
    _write_agent(agents_dir, "mixed-claude-agent", "claude")
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    calls = []

    async def fake_claude_run_turn(**kwargs):
        calls.append("claude")
        return RunResult(output="claude", session_id="sid-claude")

    async def fake_codex_run_turn(**kwargs):
        calls.append("codex")
        return RunResult(output="codex", session_id="sid-codex")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    run_id = "mixed-engine-mocked-1"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "status": "running",
        "team_id": "",
        "execution_mode": "parallel",
        "leader": "",
        "permission_mode": "workspace-write",
        "agent_engine": "",
        "steps": [
            {"agent": "mixed-codex-agent", "role": "r1", "input_memory": [], "output_memory": [], "status": "pending", "output": ""},
            {"agent": "mixed-claude-agent", "role": "r2", "input_memory": [], "output_memory": [], "status": "pending", "output": ""},
        ],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run_core(run_id, "which engine are you?", "", str(tmp_path))

    run = teams_module._team_runs[run_id]
    steps_by_agent = {s["agent"]: s for s in run["steps"]}

    assert steps_by_agent["mixed-codex-agent"]["output"] == "codex"
    assert steps_by_agent["mixed-claude-agent"]["output"] == "claude"
    assert set(calls) == {"claude", "codex"}
    assert run["status"] == "done"


async def test_agent_frontmatter_engine_overrides_run_level_default(monkeypatch, tmp_path):
    """反過來驗證優先序：run 層級的 agent_engine 預設是 codex，但這個 agent
    自己的 frontmatter 宣告 engine: claude——應該以 agent 自己的宣告為準
    （見 engines/registry.py::resolve_engine_name 的優先序說明）。"""
    import database
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "override-agent", "claude")
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    calls = []

    async def fake_claude_run_turn(**kwargs):
        calls.append("claude")
        return RunResult(output="claude", session_id="sid-claude")

    async def fake_codex_run_turn(**kwargs):
        calls.append("codex")
        return RunResult(output="codex", session_id="sid-codex")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    run_id = "mixed-engine-mocked-2"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "status": "running",
        "team_id": "",
        "execution_mode": "parallel",
        "leader": "",
        "permission_mode": "workspace-write",
        "agent_engine": "codex",  # run 層級預設是 codex
        "steps": [
            {"agent": "override-agent", "role": "r1", "input_memory": [], "output_memory": [], "status": "pending", "output": ""},
        ],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run_core(run_id, "which engine are you?", "", str(tmp_path))

    assert calls == ["claude"]
    assert teams_module._team_runs[run_id]["steps"][0]["output"] == "claude"


async def test_agent_run_capture_does_not_leak_anthropic_key_into_codex_env(monkeypatch, tmp_path):
    """2026-07-11：resolve_key()（main._resolve_api_key()）只解析 Anthropic
    key。之前不分引擎一律傳給 engine.run_turn() 的 api_key 參數——如果使用者
    設定了 Anthropic key、又讓某個 agent 用 engine: codex，會把 Anthropic
    key 誤植進 codex_engine.py 的 CODEX_API_KEY 環境變數，蓋掉正常運作的
    codex login 憑證。這裡驗證即使 main._resolve_api_key() 回傳非空字串，
    codex_engine.run_turn() 收到的 api_key 仍然是空字串；claude 引擎則照常
    收到那把 key。"""
    import database
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "codex-agent", "codex")
    _write_agent(agents_dir, "claude-agent", "claude")
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    import sys
    fake_main = type(sys)("fake_main_for_key_leak_test")
    fake_main.CLAUDE_BIN = "claude"
    fake_main._resolve_api_key = lambda: "sk-ant-should-not-leak"
    monkeypatch.setitem(sys.modules, "main", fake_main)

    captured = {}

    async def fake_claude_run_turn(**kwargs):
        captured["claude_api_key"] = kwargs.get("api_key")
        return RunResult(output="claude", session_id="sid-claude")

    async def fake_codex_run_turn(**kwargs):
        captured["codex_api_key"] = kwargs.get("api_key")
        return RunResult(output="codex", session_id="sid-codex")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    teams_module._team_runs["key-leak-run"] = {"status": "running"}
    await teams_module._agent_run_capture("key-leak-run", 0, "codex-agent", "hi", "", str(tmp_path))
    await teams_module._agent_run_capture("key-leak-run", 1, "claude-agent", "hi", "", str(tmp_path))

    assert captured["codex_api_key"] == ""
    assert captured["claude_api_key"] == "sk-ant-should-not-leak"
