"""Deterministic contract tests for engines/acp_engine.py.

Rather than a hand-rolled duck-typed StreamReader/StreamWriter fake, these
tests spawn a small, spec-shaped fake ACP agent
(tests/fixtures/fake_acp_agent.py) as a *real* subprocess, so acp_engine's
own session-driving and event-conversion logic runs against the real
`acp` library's real asyncio pipe transport — not a mock of it. See that
fixture's docstring for the JSON-RPC message sequences it plays back.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from engines import acp_engine
from engines.base import RunResult

FIXTURE = str(Path(__file__).parent / "fixtures" / "fake_acp_agent.py")

# Captured before any test monkeypatches acp_engine.asyncio.create_subprocess_exec
# — `asyncio` is one shared module object, so patching that attribute would
# otherwise make this helper recursively call its own patched replacement.
_real_create_subprocess_exec = asyncio.create_subprocess_exec


def _fake_spawn(scenario: str = "normal"):
    """Build a create_subprocess_exec replacement that always launches the
    fake ACP agent fixture (ignoring whatever binary acp_engine asked for),
    over real OS pipes."""

    async def _spawn(*args, **kwargs):
        return await _real_create_subprocess_exec(
            sys.executable, FIXTURE, "--acp", scenario,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    return _spawn


class TestPermissionModeGate:
    async def test_disallowed_permission_mode_rejected_without_spawning(self, monkeypatch):
        spawned = []
        monkeypatch.setattr(
            acp_engine.asyncio, "create_subprocess_exec",
            lambda *a, **kw: spawned.append((a, kw)) or pytest.fail("must not spawn a subprocess"),
        )

        result = await acp_engine.run_turn(
            prompt="hi", cwd=".", model="", permission_mode="read-only",
            resume_session_id=None, api_key="", on_text=None,
        )

        assert isinstance(result, RunResult)
        assert result.error is not None
        assert "permission_mode" in result.error
        assert spawned == []

    @pytest.mark.parametrize("mode", sorted(acp_engine.VALID_PERMISSION_MODES))
    async def test_every_allowed_mode_is_actually_allowed(self, monkeypatch, mode):
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("normal"))
        result = await acp_engine.run_turn(
            prompt="hi", cwd=".", model="", permission_mode=mode,
            resume_session_id=None, api_key="", on_text=None,
        )
        assert result.error is None

    @pytest.mark.parametrize("mode", ["", "default"])
    async def test_unspecified_permission_mode_falls_back_to_default(self, monkeypatch, mode):
        """/api/chat 多數呼叫根本不帶 permission_mode（main.py 的
        `data.get("permission_mode", "")`）——比照 codex_engine.
        _normalize_sandbox_mode() 的既有慣例，「沒選」要悄悄退回引擎自己的
        預設值，不能當成「明確選了不允許的值」被拒絕。"""
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("normal"))
        result = await acp_engine.run_turn(
            prompt="hi", cwd=".", model="", permission_mode=mode,
            resume_session_id=None, api_key="", on_text=None,
        )
        assert result.error is None


class TestRunTurnHappyPath:
    async def test_new_session_prompt_and_event_conversion(self, monkeypatch, tmp_path):
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("normal"))

        text_chunks = []
        tool_events = []
        processes = []

        async def on_text(chunk):
            text_chunks.append(chunk)

        async def on_tool_event(event):
            tool_events.append(event)

        result = await acp_engine.run_turn(
            prompt="do the thing", cwd=str(tmp_path), model="", permission_mode="acceptEdits",
            resume_session_id=None, api_key="",
            on_text=on_text, on_tool_event=on_tool_event,
            on_process=processes.append,
        )

        assert result.error is None
        assert result.session_id == "sid-fake-acp-1"
        assert result.output == "hello from fake acp agent"
        assert text_chunks == ["hello from fake acp agent"]
        assert len(processes) == 1

        # tool_call -> legacy "tool_use" envelope (engines/base.py contract).
        assert tool_events[0] == {
            "type": "tool_use", "id": "tc-1", "name": "Bash", "input": {"command": "echo hi"},
        }
        # tool_call_update(status=completed) -> legacy "user"/tool_result envelope.
        assert tool_events[1] == {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result", "tool_use_id": "tc-1", "content": "hi\n", "is_error": False,
            }]},
        }

    async def test_resume_session_id_is_reused_without_new_session(self, monkeypatch, tmp_path):
        """SDD explicit exclusion: no session/load full-history replay — just
        reuse the id for the next session/prompt call."""
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("normal"))

        result = await acp_engine.run_turn(
            prompt="continue", cwd=str(tmp_path), model="", permission_mode="acceptEdits",
            resume_session_id="sid-fake-acp-1", api_key="", on_text=None,
        )

        assert result.error is None
        assert result.session_id == "sid-fake-acp-1"

    async def test_spawn_failure_returns_error_result_not_exception(self, monkeypatch):
        async def _boom(*args, **kwargs):
            raise FileNotFoundError("gemini not found")

        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _boom)

        result = await acp_engine.run_turn(
            prompt="hi", cwd=".", model="", permission_mode="acceptEdits",
            resume_session_id=None, api_key="", on_text=None,
        )
        assert result.error is not None
        assert "gemini" in result.error


class TestPermissionRequestHandling:
    async def test_agent_permission_request_is_auto_allowed(self, monkeypatch, tmp_path):
        """The fake agent's "permission" scenario sends a real
        session/request_permission mid-turn; run_turn() must complete
        normally (auto-allow), not hang or error."""
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("permission"))

        result = await acp_engine.run_turn(
            prompt="do it", cwd=str(tmp_path), model="", permission_mode="acceptEdits",
            resume_session_id=None, api_key="", on_text=None,
        )

        assert result.error is None
        assert result.output == "hello from fake acp agent"

    async def test_request_permission_prefers_allow_always(self):
        client = acp_engine._Client(on_text=None, on_tool_event=None)

        class _Opt:
            def __init__(self, option_id, kind):
                self.option_id = option_id
                self.kind = kind

        options = [_Opt("reject-1", "reject_once"), _Opt("allow-once-1", "allow_once"), _Opt("allow-always-1", "allow_always")]
        response = await client.request_permission("sid", tool_call=None, options=options)

        assert response.outcome.outcome == "selected"
        assert response.outcome.option_id == "allow-always-1"

    async def test_request_permission_falls_back_to_allow_once(self):
        client = acp_engine._Client(on_text=None, on_tool_event=None)

        class _Opt:
            def __init__(self, option_id, kind):
                self.option_id = option_id
                self.kind = kind

        options = [_Opt("reject-1", "reject_once"), _Opt("allow-once-1", "allow_once")]
        response = await client.request_permission("sid", tool_call=None, options=options)

        assert response.outcome.option_id == "allow-once-1"

    async def test_request_permission_denies_when_only_reject_options_exist(self):
        client = acp_engine._Client(on_text=None, on_tool_event=None)

        class _Opt:
            def __init__(self, option_id, kind):
                self.option_id = option_id
                self.kind = kind

        options = [_Opt("reject-1", "reject_once"), _Opt("reject-2", "reject_always")]
        response = await client.request_permission("sid", tool_call=None, options=options)

        assert response.outcome.outcome == "cancelled"


class TestCancellation:
    async def test_is_cancelled_sends_session_cancel_and_returns_cleanly(self, monkeypatch, tmp_path):
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("cancel"))

        calls = {"n": 0}

        def is_cancelled():
            calls["n"] += 1
            return calls["n"] > 2  # let a couple of polls pass before cancelling

        result = await asyncio.wait_for(
            acp_engine.run_turn(
                prompt="long task", cwd=str(tmp_path), model="", permission_mode="acceptEdits",
                resume_session_id=None, api_key="", on_text=None, is_cancelled=is_cancelled,
            ),
            timeout=10,
        )

        assert result.error is None
        assert calls["n"] > 0


class TestChatEndpointIntegration:
    """SDD「測試」小節要求的 /api/chat 層級整合測試（比照
    tests/test_tool_event_streaming.py::TestHandleChatForwardsToolEvents）：
    用 fake ACP 連線驗證 SSE 端到端輸出，而不只是單元測試 run_turn() 本身
    ——這裡額外覆蓋了 main.py::_resolve_agent_engine_and_key() 的引擎選擇
    路徑（agent frontmatter `engine: acp` → apply_availability_fallback()
    → 真的落到 acp_engine.run_turn()，不會被悄悄切回 Claude/Codex）。"""

    async def test_acp_agent_chat_streams_text_and_tool_events(self, client, monkeypatch, app):
        import main
        from engines import availability

        async def _available(force: bool = False) -> dict:
            return {
                "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
                "codex": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
                "acp": {"installed": True, "loggedIn": False, "available": True, "reason": ""},
            }
        monkeypatch.setattr(availability, "get_status", _available)
        monkeypatch.setattr(acp_engine.asyncio, "create_subprocess_exec", _fake_spawn("normal"))

        main.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        (main.AGENTS_DIR / "acp-chat-agent.md").write_text(
            "---\nname: acp-chat-agent\ndescription: test\nengine: acp\n---\n\nbody\n",
            encoding="utf-8",
        )

        resp = await client.post("/api/chat", json={
            "message": "hi", "client_id": "test-acp-chat-client", "agent": "acp-chat-agent",
        })
        assert resp.status == 200
        body = (await resp.content.read()).decode("utf-8")
        events = []
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[len("data: "):]))
                except json.JSONDecodeError:
                    pass

        # agent_message_chunk -> plain text SSE (whatever event type
        # handle_chat normally uses for streamed assistant text).
        assert any("hello from fake acp agent" in json.dumps(e) for e in events)

        tool_use_events = [e for e in events if e.get("type") == "tool_use"]
        assert len(tool_use_events) == 1
        assert tool_use_events[0]["name"] == "Bash"
        assert tool_use_events[0]["input"]["command"] == "echo hi"

        tool_result_events = [
            e for e in events
            if e.get("type") == "user" and e.get("message", {}).get("content", [{}])[0].get("type") == "tool_result"
        ]
        assert len(tool_result_events) == 1
        assert tool_result_events[0]["message"]["content"][0]["tool_use_id"] == "tc-1"
        assert tool_result_events[0]["message"]["content"][0]["is_error"] is False
