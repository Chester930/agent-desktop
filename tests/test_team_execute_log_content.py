"""User report: the Windows Terminal "Project" monitor pane for a member
only showed "status notifications", not the agent's actual dialogue.

Root cause: `handle_team_execute`'s `_legacy_exec()` (the Claude fallback
path used when the Agent SDK / session pool is unavailable) wrote every
*raw, unparsed* stream-json line to `.agent_<id>.log` — a wall of
`{"type":"assistant",...}` / `{"type":"result",...}` protocol blobs, not
the readable text a human tailing the file would recognize as a
conversation. `_pooled_exec()` and `_exec_engine_turn()` (the SDK and
non-Claude-engine paths) already extracted just the text before writing
to the log; `_legacy_exec()` was the one inconsistent path.
"""
import json


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.returncode = 0

    async def wait(self):
        return 0


def _write_agent(agents_dir, agent_id: str) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\nengine: claude\n---\n\nagent body\n",
        encoding="utf-8",
    )


def _write_team(teams_dir, team_id: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nexecution_mode: sequential\nmembers:\n{members_yaml}\n",
        encoding="utf-8",
    )


async def test_legacy_exec_writes_clean_text_to_log_not_raw_json(client, monkeypatch, app, tmp_path):
    import main
    import database

    _write_agent(main.AGENTS_DIR, "claude-legacy-executor")
    _write_team(main.TEAMS_DIR, "claude-legacy-team", ["claude-legacy-executor"])

    # Force the _legacy_exec fallback path deterministically (no SDK / pool).
    monkeypatch.setattr(main, "HAS_AGENT_SDK", False)
    monkeypatch.setattr(database, "get_engine_mode", lambda: "claude")

    lines = [
        b'{"type":"system","subtype":"init"}\n',
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"hello from agent"}]}}\n',
        b'{"type":"result","session_id":"sid-legacy-exec"}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    resp = await client.post("/api/team/execute", json={
        "client_id": "test-client-legacy-log",
        "team_id": "claude-legacy-team",
        "project_path": str(project_dir),
        "task": "完成這個任務",
    })
    assert resp.status == 200
    await resp.content.read()

    log_content = (project_dir / ".agent_claude-legacy-executor.log").read_text(encoding="utf-8")

    # The readable text must be there...
    assert "hello from agent" in log_content
    # ...but none of the raw protocol envelopes/keys should leak through.
    assert '"type":"assistant"' not in log_content
    assert '"type":"system"' not in log_content
    assert '"type":"result"' not in log_content
    assert "session_id" not in log_content
