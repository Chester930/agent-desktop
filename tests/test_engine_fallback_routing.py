"""2026-07-11：引擎可用性偵測疊加到既有的 3 個 resolve_engine_name 呼叫點
之後，驗證「偏好引擎不可用時自動切換到另一個」跟「兩邊都不可用時清楚報錯」
這兩條路徑，分別透過各自入口既有的錯誤/文字通道正確浮現。conftest.py 的
autouse fixture 預設兩邊都可用，這裡每個測試自己 monkeypatch
engines.availability.get_status 蓋掉那個預設值。
"""
import json

import pytest

from engines import availability
from engines.base import RunResult

pytestmark = pytest.mark.asyncio


def _only_claude_available():
    async def _fake(force: bool = False) -> dict:
        return {
            "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            "codex": {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"},
        }
    return _fake


def _neither_available():
    async def _fake(force: bool = False) -> dict:
        return {
            "claude": {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"},
            "codex": {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"},
        }
    return _fake


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


# ── handle_chat ──────────────────────────────────────────────────────────────

async def test_handle_chat_falls_back_to_claude_when_codex_unavailable(client, monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "codex-fallback-agent", engine="codex")
    monkeypatch.setattr(availability, "get_status", _only_claude_available())

    async def fake_create_subprocess_exec(*args, **kwargs):
        class _FakeStdout:
            def __init__(self):
                self._lines = [b'{"type":"result","session_id":"sid-fb"}\n']

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._lines:
                    raise StopAsyncIteration
                return self._lines.pop(0)

        class _FakeProc:
            def __init__(self):
                self.stdout = _FakeStdout()
                self.returncode = 0

            async def wait(self):
                return 0

        return _FakeProc()

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-client-fallback", "agent": "codex-fallback-agent",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)

    assistant_texts = [
        b.get("text", "")
        for e in events if e.get("type") == "assistant"
        for b in e["message"]["content"]
    ]
    assert any("已自動切換為 Claude" in t for t in assistant_texts)


async def test_handle_chat_reports_error_when_neither_available(client, monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "no-engine-agent", engine="codex")
    monkeypatch.setattr(availability, "get_status", _neither_available())

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-client-none", "agent": "no-engine-agent",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) == 1
    assert "都無法使用" in error_events[0]["text"]


# ── handle_team_chat ─────────────────────────────────────────────────────────

async def test_handle_team_chat_falls_back_and_emits_notice(client, monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "team-chat-codex", engine="codex")
    _write_chat_team(main.TEAMS_DIR, "fallback-chat-team", "team-chat-codex", ["team-chat-codex"])
    monkeypatch.setattr(availability, "get_status", _only_claude_available())

    async def fake_create_subprocess_exec(*args, **kwargs):
        class _FakeStdout:
            def __init__(self):
                self._lines = [b'{"type":"result","session_id":"sid-tc-fb"}\n']

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._lines:
                    raise StopAsyncIteration
                return self._lines.pop(0)

        class _FakeProc:
            def __init__(self):
                self.stdout = _FakeStdout()
                self.returncode = 0

            async def wait(self):
                return 0

        return _FakeProc()

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    resp = await client.post("/api/team/chat", json={
        "message": "hi", "client_id": "test-team-chat-fb", "team_id": "fallback-chat-team",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)
    text_events = [e for e in events if e.get("type") == "text"]
    assert any("已自動切換為 Claude" in e.get("text", "") for e in text_events)


# ── handle_team_execute ──────────────────────────────────────────────────────

def _write_team(teams_dir, team_id: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nexecution_mode: sequential\nmembers:\n{members_yaml}\n",
        encoding="utf-8",
    )


async def test_team_execute_falls_back_and_emits_notice(client, monkeypatch, app, tmp_path):
    import main
    _write_agent(main.AGENTS_DIR, "exec-codex-fb", engine="codex")
    _write_team(main.TEAMS_DIR, "exec-fb-team", ["exec-codex-fb"])
    monkeypatch.setattr(availability, "get_status", _only_claude_available())

    project_dir = tmp_path / "project-fb"
    project_dir.mkdir()

    async def fake_create_subprocess_exec(*args, **kwargs):
        class _FakeStdout:
            def __init__(self):
                self._lines = [b'{"type":"result","session_id":"sid-exec-fb"}\n']

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._lines:
                    raise StopAsyncIteration
                return self._lines.pop(0)

        class _FakeStdin:
            def write(self, data):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

        class _FakeProc:
            def __init__(self):
                self.stdout = _FakeStdout()
                self.stdin = _FakeStdin()
                self.returncode = 0

            async def wait(self):
                return 0

        return _FakeProc()

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-exec-fb", "team_id": "exec-fb-team",
        "project_path": str(project_dir), "task": "任務",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)
    exec_text_events = [e for e in events if e.get("type") == "exec_text"]
    assert any("已自動切換為 Claude" in e.get("text", "") for e in exec_text_events)


async def test_team_execute_reports_error_triplet_when_neither_available(client, monkeypatch, app, tmp_path):
    import main
    _write_agent(main.AGENTS_DIR, "exec-none", engine="codex")
    _write_team(main.TEAMS_DIR, "exec-none-team", ["exec-none"])
    monkeypatch.setattr(availability, "get_status", _neither_available())

    project_dir = tmp_path / "project-none"
    project_dir.mkdir()

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-exec-none", "team_id": "exec-none-team",
        "project_path": str(project_dir), "task": "任務",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)

    agent_events = [e for e in events if e.get("agent") == "exec-none"]
    types = [e.get("type") for e in agent_events]
    assert "exec_start" in types
    assert "exec_done" in types
    assert any(e.get("type") == "exec_text" and "都無法使用" in e.get("text", "") for e in agent_events)


# ── _run_hr_agent ────────────────────────────────────────────────────────────

async def test_hr_dispatch_reports_error_when_neither_available(client, monkeypatch, app):
    monkeypatch.setattr(availability, "get_status", _neither_available())

    resp = await client.post("/api/hr/dispatch", json={"task": "幫我組一個團隊"})
    assert resp.status == 500
    body = await resp.json()
    assert "都無法使用" in body["error"]


# ── _agent_run_capture (Team Run) ────────────────────────────────────────────

async def test_agent_run_capture_falls_back_and_never_raises(monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "run-capture-codex", engine="codex")
    monkeypatch.setattr(availability, "get_status", _only_claude_available())

    import routes.teams as teams_module
    from engines import claude_engine

    async def fake_claude_run_turn(**kwargs):
        await kwargs["on_text"]("claude ran it")
        return RunResult(output="claude ran it", session_id="sid-capture-fb")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    emitted = []
    monkeypatch.setattr(teams_module, "_tr_emit", lambda run_id, event: emitted.append(event))
    monkeypatch.setattr(teams_module, "_register_team_proc", lambda run_id, proc: None)
    monkeypatch.setattr(teams_module, "_unregister_team_proc", lambda run_id, proc: None)
    monkeypatch.setattr(teams_module, "_team_runs", {"run-fb": {"status": "running"}})

    result = await teams_module._agent_run_capture(
        run_id="run-fb", step_idx=0, agent_id="run-capture-codex",
        prompt="do it", model="", cwd=str(main.CLAUDE_HOME),
    )
    assert "claude ran it" in result
    assert any("已自動切換為 Claude" in e.get("text", "") for e in emitted)


async def test_agent_run_capture_neither_available_returns_error_string(monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "run-capture-none", engine="codex")
    monkeypatch.setattr(availability, "get_status", _neither_available())

    import routes.teams as teams_module

    emitted = []
    monkeypatch.setattr(teams_module, "_tr_emit", lambda run_id, event: emitted.append(event))
    monkeypatch.setattr(teams_module, "_team_runs", {"run-none": {"status": "running"}})

    result = await teams_module._agent_run_capture(
        run_id="run-none", step_idx=0, agent_id="run-capture-none",
        prompt="do it", model="", cwd=str(main.CLAUDE_HOME),
    )
    # 既有 contract：永遠不 raise，永遠回傳字串。
    assert isinstance(result, str)
    assert "都無法使用" in result
    assert any("都無法使用" in e.get("text", "") for e in emitted)
