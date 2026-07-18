"""2026-07-11：handle_team_execute（team run 的另一條執行路徑，走即時
stdin y/n 或 can_use_tool 權限核准流程）之前完全沒有讀取 agent 的
engine: frontmatter 欄位，寫死呼叫 Claude。這裡驗證 agent 宣告
engine: codex 時，run_agent_executor() 真的會呼叫 codex_engine.run_turn()，
完全不觸碰 SessionPool/claude_engine。Codex-routed 的團隊成員會跳過
handle_team_execute 的即時權限核准 UI（pending_permissions/can_use_tool），
這是既有、已接受的權衡，不在這裡驗證。
"""
import json

import pytest

from engines.base import RunResult



def _write_agent(agents_dir, agent_id: str, engine: str = "") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    engine_line = f"engine: {engine}\n" if engine else ""
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\n{engine_line}---\n\nagent body\n",
        encoding="utf-8",
    )


def _write_team(teams_dir, team_id: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nexecution_mode: sequential\nmembers:\n{members_yaml}\n",
        encoding="utf-8",
    )


async def _read_sse_events(resp) -> list:
    body = (await resp.content.read()).decode("utf-8")
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[len("data: "):]))
            except json.JSONDecodeError:
                pass
    return events


async def test_team_execute_member_routes_to_codex(client, monkeypatch, app, tmp_path):
    import main
    _write_agent(main.AGENTS_DIR, "codex-executor", engine="codex")
    _write_team(main.TEAMS_DIR, "codex-exec-team", ["codex-executor"])

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    from engines import codex_engine, claude_engine
    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("Codex 執行完成")
        return RunResult(output="Codex 執行完成", session_id="sid-exec-codex")

    async def fake_claude_run_turn(**kwargs):
        raise AssertionError("claude_engine.run_turn should not be called for a codex-declared executor agent")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)
    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-client-exec-codex",
        "team_id": "codex-exec-team",
        "project_path": str(project_dir),
        "task": "完成這個任務",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)

    assert len(codex_calls) == 1
    # attachments 對這條路徑本來就不存在（跟 handle_chat/handle_team_chat 不同），
    # 這裡確認沒有把不存在的變數傳進去造成 NameError（若有會直接 500/連線中斷）。
    assert "attachments" not in codex_calls[0]

    text_events = [e for e in events if e.get("type") == "exec_text" and e.get("agent") == "codex-executor"]
    assert any("Codex 執行完成" in e.get("text", "") for e in text_events)
    assert any(e.get("type") == "exec_start" and e.get("agent") == "codex-executor" for e in events)
    assert any(e.get("type") == "exec_done" and e.get("agent") == "codex-executor" for e in events)


async def test_team_execute_member_without_engine_defaults_to_codex(client, monkeypatch, app, tmp_path):
    """2026-07-13 起沒有宣告 engine: 時預設引擎是 Codex（不再是 Claude）——
    使用者確認：兩邊 CLI 都能用時，預設選 Codex。"""
    import main
    _write_agent(main.AGENTS_DIR, "default-engine-executor", engine="")
    _write_team(main.TEAMS_DIR, "default-engine-exec-team", ["default-engine-executor"])

    project_dir = tmp_path / "project2"
    project_dir.mkdir()

    from engines import claude_engine, codex_engine
    claude_called = False

    async def fake_claude_run_turn(**kwargs):
        nonlocal claude_called
        claude_called = True
        return RunResult(output="should not happen")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("預設引擎執行完成")
        return RunResult(output="預設引擎執行完成", session_id="sid-default-codex-exec")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-client-exec-default",
        "team_id": "default-engine-exec-team",
        "project_path": str(project_dir),
        "task": "完成這個任務",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert len(codex_calls) == 1
    assert not claude_called


async def test_team_execute_strips_sonnet_model_alias_for_non_claude_engine(client, monkeypatch, app, tmp_path):
    """2026-07-18：同一個 bug 的第三條路徑——見
    test_handle_chat_engine_routing.py / test_handle_team_chat_engine_routing.py
    的同名測試。"sonnet" 是 Claude-only 的 UI 佔位值，engine.run_turn() 這
    條路徑漏了過濾，選了 Sonnet 4.6 當全域模型、成員又宣告 engine: codex
    時，"sonnet" 字面值直接傳給 Codex CLI 當 --model 參數。"""
    import main
    _write_agent(main.AGENTS_DIR, "codex-executor-2", engine="codex")
    _write_team(main.TEAMS_DIR, "sonnet-strip-exec-team", ["codex-executor-2"])

    project_dir = tmp_path / "project3"
    project_dir.mkdir()

    from engines import codex_engine
    captured = {}

    async def fake_codex_run_turn(**kwargs):
        captured["model"] = kwargs.get("model")
        await kwargs["on_text"]("ok")
        return RunResult(output="ok", session_id="sid-exec-sonnet-strip")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-client-exec-sonnet-strip",
        "team_id": "sonnet-strip-exec-team",
        "project_path": str(project_dir),
        "task": "完成這個任務",
        "model": "sonnet",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert captured["model"] is None
