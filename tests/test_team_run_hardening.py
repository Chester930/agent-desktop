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
    async def test_step_failure_is_not_a_wrap_cmd_nameerror(self, client, tmp_claude_home):
        """
        Regression test for wrap_cmd NameError: routes/teams.py previously never
        imported wrap_cmd, so every team-run step failed with
        "name 'wrap_cmd' is not defined" regardless of whether the `claude` CLI
        was installed. In this test environment there's no real `claude` binary,
        so the step is still expected to fail — but it must fail at the actual
        subprocess-spawn stage (FileNotFoundError), never at wrap_cmd itself.
        """
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"

        resp = await client.post("/api/team/run", json={
            "task": "smoke test",
            "team": {"name": "t", "members": [{"agent": "nonexistent-agent", "role": "r"}]},
        })
        assert resp.status == 200
        run_id = (await resp.json())["run_id"]

        # Poll for the background task to finish the (failing) step. Generous
        # timeout since this spawns a real subprocess attempt under the hood.
        step = None
        for _ in range(200):
            r = await client.get(f"/api/team/run/{run_id}")
            body = await r.json()
            step = body["steps"][0]
            if step["status"] == "done":
                break
            await asyncio.sleep(0.1)

        assert step["status"] == "done", f"step never finished: {step}"
        assert "wrap_cmd" not in step["output"]
