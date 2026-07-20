"""2026-07-12：Settings 新增可鎖定的「執行引擎範圍」（只用 Claude／只用
Codex／兩者都開放，database.get_engine_mode()）。使用者的核心要求是：鎖定
成單一引擎時，個別 Agent 的 engine: frontmatter 覆寫要「完全不生效」，不是
只在 UI 上不能選——這裡針對 3 個既有的引擎解析呼叫點，逐一驗證鎖定真的
在執行期擋下了 agent 自己的指定，不是只改了畫面。

conftest.py 的 autouse fixture 已經把可用性偵測鎖定成「兩邊都可用」，這裡
不用再處理 installed/loggedIn 的問題，純粹驗證 engineMode 這一層的收斂
邏輯。
"""
import json

import pytest



def _set_engine_mode(tmp_path, mode: str):
    import main
    main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
    main.CONFIG_FILE.write_text(json.dumps({"engineMode": mode}), encoding="utf-8")


def _write_agent(agents_dir, agent_id: str, engine: str = "") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    engine_line = f"engine: {engine}\n" if engine else ""
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\n{engine_line}---\n\nagent body\n",
        encoding="utf-8",
    )


def _write_chat_team(teams_dir, team_id: str, leader: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nleader: {leader}\nmembers:\n{members_yaml}\n",
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


class _FakeClaudeStdout:
    def __init__(self, sid="sid-lockdown"):
        self._lines = [f'{{"type":"result","session_id":"{sid}"}}\n'.encode()]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeClaudeStdin:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def _make_fake_claude_subprocess():
    async def fake_create_subprocess_exec(*args, **kwargs):
        class _FakeProc:
            def __init__(self):
                self.stdout = _FakeClaudeStdout()
                self.stdin = _FakeClaudeStdin()
                self.returncode = 0

            async def wait(self):
                return 0

        return _FakeProc()

    return fake_create_subprocess_exec


# ── handle_chat ──────────────────────────────────────────────────────────────

async def test_handle_chat_locked_to_claude_ignores_agent_codex_override(client, monkeypatch, app, tmp_path):
    import main
    _set_engine_mode(tmp_path, "claude")
    _write_agent(main.AGENTS_DIR, "locked-agent", engine="codex")

    from engines import codex_engine
    codex_called = False

    async def fake_codex_run_turn(**kwargs):
        nonlocal codex_called
        codex_called = True
        return None

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _make_fake_claude_subprocess())

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-locked-chat", "agent": "locked-agent",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert not codex_called, "agent 的 engine: codex 在鎖定 'claude' 時不應該生效"


async def test_handle_chat_both_mode_still_lets_agent_override(client, monkeypatch, app, tmp_path):
    """對照組：mode='both' 時既有行為（agent 覆寫生效）要維持不變。"""
    import main
    _set_engine_mode(tmp_path, "both")
    _write_agent(main.AGENTS_DIR, "unlocked-agent", engine="codex")

    from engines.base import RunResult
    from engines import codex_engine
    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("codex replied")
        return RunResult(output="codex replied", session_id="sid-unlocked")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-unlocked-chat", "agent": "unlocked-agent",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert len(codex_calls) == 1


# ── handle_team_chat ─────────────────────────────────────────────────────────

async def test_handle_team_chat_locked_to_codex_ignores_agent_claude_override(client, monkeypatch, app, tmp_path):
    import main
    _set_engine_mode(tmp_path, "codex")
    _write_agent(main.AGENTS_DIR, "team-chat-locked", engine="claude")
    _write_chat_team(main.TEAMS_DIR, "locked-chat-team", "team-chat-locked", ["team-chat-locked"])

    from engines.base import RunResult
    from engines import codex_engine
    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("身為組長，我用 Codex 回覆。")
        return RunResult(output="身為組長，我用 Codex 回覆。", session_id="sid-team-locked")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/team/chat", json={
        "message": "hi", "client_id": "test-team-chat-locked", "team_id": "locked-chat-team",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert len(codex_calls) == 1, "agent 宣告 engine: claude，但鎖定 'codex' 時應該還是被 Codex 執行"


# ── handle_team_execute ──────────────────────────────────────────────────────

def _write_exec_team(teams_dir, team_id: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nexecution_mode: sequential\nmembers:\n{members_yaml}\n",
        encoding="utf-8",
    )


async def test_team_execute_locked_to_claude_ignores_agent_codex_override(client, monkeypatch, app, tmp_path):
    import main
    _set_engine_mode(tmp_path, "claude")
    _write_agent(main.AGENTS_DIR, "exec-locked-agent", engine="codex")
    _write_exec_team(main.TEAMS_DIR, "locked-exec-team", ["exec-locked-agent"])

    project_dir = tmp_path / "project-locked"
    project_dir.mkdir()

    from engines import codex_engine
    codex_called = False

    async def fake_codex_run_turn(**kwargs):
        nonlocal codex_called
        codex_called = True
        return None

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _make_fake_claude_subprocess())

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-exec-locked", "team_id": "locked-exec-team",
        "project_path": str(project_dir), "task": "任務",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert not codex_called, "agent 的 engine: codex 在鎖定 'claude' 時不應該生效"


# ── _agent_run_capture (Team Run) ────────────────────────────────────────────

async def test_agent_run_capture_locked_ignores_frontmatter_and_request(monkeypatch, app, tmp_path):
    import main
    _set_engine_mode(tmp_path, "claude")
    _write_agent(main.AGENTS_DIR, "run-capture-locked", engine="codex")

    import routes.teams as teams_module
    from engines import claude_engine, codex_engine
    from engines.base import RunResult

    async def fake_claude_run_turn(**kwargs):
        await kwargs["on_text"]("claude ran it")
        return RunResult(output="claude ran it", session_id="sid-capture-locked")

    async def fake_codex_run_turn(**kwargs):
        raise AssertionError("codex_engine.run_turn should not be called when locked to claude")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    emitted = []
    monkeypatch.setattr(teams_module, "_tr_emit", lambda run_id, event: emitted.append(event))
    monkeypatch.setattr(teams_module, "_register_team_proc", lambda run_id, proc: None)
    monkeypatch.setattr(teams_module, "_unregister_team_proc", lambda run_id, proc: None)
    monkeypatch.setattr(teams_module, "_team_runs", {"run-locked": {"status": "running"}})

    # request 層級也故意傳 "codex"，一樣要被忽略
    result = await teams_module._agent_run_capture(
        run_id="run-locked", step_idx=0, agent_id="run-capture-locked",
        prompt="do it", model="", cwd=str(main.CLAUDE_HOME),
        default_engine="codex",
    )
    assert "claude ran it" in result
