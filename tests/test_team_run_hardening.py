"""健檢第二輪：team run 相關修復
- wrap_cmd 在 routes/teams.py 從未被 import，導致每個 team run step 都 NameError（100% 壞掉）。
- inline team payload（POST /api/team/run 的 `team` 欄位）對 agent id 與
  input_memory/output_memory key 完全沒驗證，可用於路徑穿越讀寫任意 .md 檔。
"""
import asyncio

import pytest

import routes.teams as teams_module

pytestmark = pytest.mark.asyncio


class TestIsSafeId:
    pytestmark = []

    def test_empty_rejected(self):
        assert teams_module._is_safe_id("") is False

    def test_simple_name_allowed(self):
        assert teams_module._is_safe_id("my-agent_1") is True

    def test_path_traversal_rejected(self):
        assert teams_module._is_safe_id("../../etc/passwd") is False
        assert teams_module._is_safe_id("a/b") is False
        assert teams_module._is_safe_id("a\\b") is False
        assert teams_module._is_safe_id("..") is False


class TestTeamRunPathTraversalRejected:
    async def test_rejects_unsafe_agent_id(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.post("/api/team/run", json={
            "task": "task",
            "team": {"name": "t", "members": [{"agent": "../../evil", "role": "r"}]},
        })
        assert resp.status == 400

    async def test_rejects_unsafe_output_memory_key(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.post("/api/team/run", json={
            "task": "task",
            "team": {"name": "t", "members": [
                {"agent": "ok-agent", "role": "r", "output_memory": ["../../../escape"]}
            ]},
        })
        assert resp.status == 400

    async def test_rejects_unsafe_input_memory_key(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.post("/api/team/run", json={
            "task": "task",
            "team": {"name": "t", "members": [
                {"agent": "ok-agent", "role": "r", "input_memory": ["..\\escape"]}
            ]},
        })
        assert resp.status == 400

    async def test_valid_payload_still_accepted(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.post("/api/team/run", json={
            "task": "task",
            "team": {"name": "t", "members": [
                {"agent": "ok-agent", "role": "r", "output_memory": ["safe-key"]}
            ]},
        })
        assert resp.status == 200


class TestWrapCmdFixedInTeamRun:
    pytestmark = [pytest.mark.asyncio]

    async def test_agent_run_capture_calls_wrap_cmd_without_nameerror(self, monkeypatch):
        """
        Regression test for wrap_cmd NameError: routes/teams.py previously never
        imported wrap_cmd, so _agent_run_capture's `cmd = wrap_cmd(cmd[0], cmd[1:])`
        raised NameError on every single call, regardless of whether the `claude`
        CLI was installed. Call the real function directly (no HTTP round-trip /
        background-task polling needed) and force the subsequent subprocess spawn
        to fail deterministically — the failure must come from that forced spawn
        error, never from "name 'wrap_cmd' is not defined".
        """
        async def fake_create_subprocess_exec(*args, **kwargs):
            raise FileNotFoundError("forced: no real subprocess spawned in this test")

        monkeypatch.setattr(teams_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        output = await teams_module._agent_run_capture(
            "run-x", 0, "nonexistent-agent", "prompt", "haiku", "/tmp"
        )

        assert "wrap_cmd" not in output
        assert "forced: no real subprocess spawned" in output
