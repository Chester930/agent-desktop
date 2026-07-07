import unittest
import json
import asyncio
from pathlib import Path
import tempfile
import shutil
from memory_agent import MemoryAgent
from message_bus import global_bus

class TestMemoryAgent(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.global_dir = self.test_dir / "global"
        self.agent_dir = self.test_dir / "agent"
        
        # Create folder structures
        (self.global_dir / "user").mkdir(parents=True, exist_ok=True)
        (self.global_dir / "system").mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "projects").mkdir(parents=True, exist_ok=True)
        
        # Write dummy memories
        (self.global_dir / "user" / "profile.md").write_text("User Profile Content", encoding="utf-8")
        (self.global_dir / "system" / "state.md").write_text("System State Content", encoding="utf-8")
        (self.agent_dir / "identity.md").write_text("Agent Identity Content", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_core_memory_retrieval(self):
        agent = MemoryAgent(global_mem_dir=self.global_dir, agent_mem_dir=self.agent_dir)
        core = agent.get_core_memory()
        self.assertEqual(core.get("user"), "User Profile Content")
        self.assertEqual(core.get("system"), "System State Content")
        self.assertEqual(core.get("identity"), "Agent Identity Content")

    def test_smart_context_truncation(self):
        # Create a huge project experience file
        large_exp = "A" * 10000
        (self.agent_dir / "projects" / "test-project.md").write_text(large_exp, encoding="utf-8")
        
        agent = MemoryAgent(
            global_mem_dir=self.global_dir,
            agent_mem_dir=self.agent_dir,
            cwd_slug="test-project"
        )
        
        # Limit character count to 2000 (shorter than large_exp)
        ctx = agent.build_smart_context(agent_id="TestAgent", max_chars=2000)
        
        # Verify core memories are still present
        self.assertIn("User Profile Content", ctx)
        self.assertIn("Agent Identity Content", ctx)
        # Verify the large experience was truncated
        self.assertIn("[truncated to fit memory context]", ctx)

    def test_semantic_recall(self):
        # Create experience file with distinct paragraph themes
        exp_content = (
            "### Git Commands\n" + "We run git status and git log to review codebase changes. " * 20 + "\n\n"
            "### Docker Setup\n" + "We use docker-compose up to boot up database containers. " * 20 + "\n\n"
            "### Python Scripting\n" + "We write python unit tests to verify system safety. " * 20 + "\n"
        )
        (self.agent_dir / "projects" / "test-project.md").write_text(exp_content, encoding="utf-8")
        
        agent = MemoryAgent(
            global_mem_dir=self.global_dir,
            agent_mem_dir=self.agent_dir,
            cwd_slug="test-project"
        )
        
        # Search specifically for Git related topics
        ctx = agent.build_smart_context(agent_id="TestAgent", max_chars=16000, query="git status log command")
        
        # Verify that Git paragraph is recalled (relevance score shown in title)
        self.assertIn("Git Commands", ctx)
        self.assertIn("Relevance:", ctx)
        # Verify that Docker paragraph is NOT recalled because it's irrelevant
        self.assertNotIn("Docker Setup", ctx)


class TestMessageBus(unittest.TestCase):
    def test_publish_subscribe(self):
        events = []
        
        def on_event(msg):
            events.append(msg)
            
        global_bus.subscribe("test:topic", on_event)
        global_bus.publish("test:topic", {"data": "hello"})
        
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("data"), "hello")
        
        # Cleanup
        global_bus.unsubscribe("test:topic", on_event)

from unittest.mock import patch, AsyncMock, MagicMock

class TestAdversarialDebate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        import database
        self.old_teams_dir = database.TEAMS_DIR
        database.TEAMS_DIR = self.test_dir
        
        team_content = (
            "name: Test Team\n"
            "execution_mode: consensus\n"
            "leader: LeaderAgent\n"
            "members:\n"
            "  - agent: CoderAgent\n"
            "    role: Coder\n"
            "  - agent: AuditorAgent\n"
            "    role: Auditor\n"
        )
        (self.test_dir / "test-team.yaml").write_text(team_content, encoding="utf-8")

    def tearDown(self):
        import database
        database.TEAMS_DIR = self.old_teams_dir
        shutil.rmtree(self.test_dir)

    @patch("routes.teams._agent_run_capture")
    async def test_consensus_debate_flow(self, mock_capture):
        mock_capture.side_effect = [
            "This is the initial draft code.",
            "Review feedback: Found safety bugs.",
            "This is the revised code with bugs fixed.",
            "Leader Summary: Final consensual design summary."
        ]
        
        import routes.teams
        run_id = "test-run-123"
        routes.teams._team_runs[run_id] = {
            "team_id": "test-team",
            "status": "running",
            "steps": [
                {"agent": "CoderAgent", "role": "Coder", "status": "pending", "output": ""},
                {"agent": "AuditorAgent", "role": "Auditor", "status": "pending", "output": ""}
            ]
        }
        
        await routes.teams._execute_team_run(run_id, "Build a secure DB sync script", "haiku", "/tmp")
            
        run_data = routes.teams._team_runs[run_id]
        self.assertEqual(len(run_data["steps"]), 4)
        self.assertEqual(run_data["status"], "done")
        self.assertEqual(run_data["summary"], "Leader Summary: Final consensual design summary.")
        self.assertEqual(mock_capture.call_count, 4)

from aiohttp.test_utils import make_mocked_request

class TestMcpDebugger(unittest.IsolatedAsyncioTestCase):
    @patch("routes.mcp_debugger.asyncio.create_subprocess_exec")
    @patch("routes.mcp_debugger._analyze_mcp_entry")
    async def test_mcp_rpc_success(self, mock_analyze, mock_create_proc):
        mock_analyze.return_value = {
            "command": "node",
            "args": ["mock_mcp.js"]
        }

        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline.return_value = b'{"jsonrpc": "2.0", "id": 999, "result": {"tools": []}}\n'
        mock_proc.wait = AsyncMock()
        
        mock_create_proc.return_value = mock_proc

        from routes.mcp_debugger import handle_mcp_rpc
        
        req_data = {
            "mcp_name": "mock-sqlite",
            "method": "tools/list",
            "params": {}
        }
        
        request = make_mocked_request("POST", "/api/mcp/rpc")
        request.json = AsyncMock(return_value=req_data)
        
        response = await handle_mcp_rpc(request)
        self.assertEqual(response.status, 200)
        
        resp_json = json.loads(response.body.decode("utf-8"))
        self.assertEqual(resp_json["result"]["tools"], [])
        mock_proc.terminate.assert_called_once()

class TestRunArtifactsTracer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    async def test_run_artifacts_retrieval(self):
        # 1. 寫入成果檔案至臨時工作區
        (self.test_dir / "result_chart.png").write_bytes(b"mock_png_data")
        (self.test_dir / "data.json").write_text('{"status": "ok"}', encoding="utf-8")
        
        # 2. 設置 mock 數據結構
        import routes.teams
        run_id = "mock-run-456"
        routes.teams._team_runs[run_id] = {
            "id": run_id,
            "cwd": str(self.test_dir),
            "artifacts": ["result_chart.png", "data.json"]
        }
        
        # 3. 呼叫端點
        from routes.run_artifacts import handle_run_artifacts
        
        request = make_mocked_request("GET", f"/api/team/run/{run_id}/artifacts")
        request.match_info["run_id"] = run_id
        
        response = await handle_run_artifacts(request)
        self.assertEqual(response.status, 200)
        
        resp_json = json.loads(response.body.decode("utf-8"))
        self.assertEqual(resp_json["run_id"], run_id)
        
        artifacts = resp_json["artifacts"]
        self.assertEqual(len(artifacts), 2)
        
        # 驗證類別對齊
        art_map = {a["filename"]: a for a in artifacts}
        self.assertEqual(art_map["result_chart.png"]["preview_type"], "image")
        self.assertEqual(art_map["data.json"]["preview_type"], "data")

class TestTeamFailsafe(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        import database
        self.old_teams_dir = database.TEAMS_DIR
        database.TEAMS_DIR = self.test_dir

    def tearDown(self):
        import database
        database.TEAMS_DIR = self.old_teams_dir
        shutil.rmtree(self.test_dir)

    async def test_steps_limit_exceeded(self):
        import routes.teams
        run_id = "test-failsafe-1"
        routes.teams._team_runs[run_id] = {
            "id": run_id,
            "status": "running",
            "steps": [{"agent": f"Agent{i}", "role": "Role", "status": "pending"} for i in range(20)]
        }
        
        await routes.teams._execute_team_run(run_id, "looping task", "haiku", "/tmp")
        
        run_data = routes.teams._team_runs[run_id]
        self.assertEqual(run_data["status"], "cancelled")
        self.assertIn("步驟數超過最大極限", run_data["summary"])

    @patch("routes.teams._agent_run_capture")
    @patch("routes.teams.safe_kill_process")
    async def test_timeout_failsafe_cleanup(self, mock_kill, mock_capture):
        async def slow_run(*args, **kwargs):
            await asyncio.sleep(2.0)
            return "done"
        mock_capture.side_effect = slow_run
        
        import routes.teams
        run_id = "test-failsafe-2"
        mock_proc = MagicMock()
        # 健檢第二輪修復：_team_run_processes 現在是 dict[str, set]（同一個
        # run_id 底下可同時追蹤多個 process，parallel 模式不再互相覆蓋）。
        routes.teams._register_team_proc(run_id, mock_proc)
        routes.teams._team_runs[run_id] = {
            "id": run_id,
            "status": "running",
            "_test_timeout": 0.1,
            "steps": [{"agent": "SlowAgent", "role": "Worker", "status": "pending", "output": ""}]
        }
        
        await routes.teams._execute_team_run(run_id, "slow task", "haiku", "/tmp")
        
        run_data = routes.teams._team_runs[run_id]
        self.assertEqual(run_data["status"], "cancelled")
        self.assertIn("執行時間超過", run_data["summary"])
        mock_kill.assert_called_once_with(mock_proc)

class TestSensitiveToolGatekeeper(unittest.IsolatedAsyncioTestCase):
    @patch("routes.mcp_debugger._analyze_mcp_entry")
    async def test_sensitive_tool_intercept_and_bypass(self, mock_analyze):
        mock_analyze.return_value = {"command": "sqlite3", "args": []}
        from routes.mcp_debugger import handle_mcp_rpc

        params = {"name": "write_file", "arguments": {"path": "a.txt", "content": "123"}}

        # 1. 測試無授權調用敏感工具 (write_file) -> 應該攔截 (403)，並取得伺服器核發的 pending_id
        req_data_unauth = {
            "mcp_name": "mock-sqlite",
            "method": "tools/call",
            "params": params
        }
        request = make_mocked_request("POST", "/api/mcp/rpc")
        request.json = AsyncMock(return_value=req_data_unauth)

        response = await handle_mcp_rpc(request)
        self.assertEqual(response.status, 403)
        resp_json = json.loads(response.body.decode("utf-8"))
        self.assertEqual(resp_json["status"], "pending_authorization")
        self.assertIn("敏感操作攔截", resp_json["error"])
        pending_id = resp_json["pending_id"]
        self.assertTrue(pending_id)

        # 2. 光靠呼叫方自報 authorized=True（沒有伺服器核發的 pending_id）不應該放行
        req_data_fake_auth = {
            "mcp_name": "mock-sqlite",
            "method": "tools/call",
            "params": params,
            "authorized": True
        }
        request_fake_auth = make_mocked_request("POST", "/api/mcp/rpc")
        request_fake_auth.json = AsyncMock(return_value=req_data_fake_auth)

        response_fake_auth = await handle_mcp_rpc(request_fake_auth)
        self.assertEqual(response_fake_auth.status, 403)

        # 3. 帶上伺服器核發、且對應同一筆請求內容的 pending_id -> 應該放行
        req_data_auth = {
            "mcp_name": "mock-sqlite",
            "method": "tools/call",
            "params": params,
            "pending_id": pending_id
        }
        request_auth = make_mocked_request("POST", "/api/mcp/rpc")
        request_auth.json = AsyncMock(return_value=req_data_auth)

        # 為了不真正啟動子進程，我們 patch asyncio.create_subprocess_exec
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_spawn:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=b'{"jsonrpc":"2.0","result":{}}\n')
            mock_spawn.return_value = mock_proc

            response_auth = await handle_mcp_rpc(request_auth)
            self.assertEqual(response_auth.status, 200)
            mock_spawn.assert_called_once()

        # 4. 同一個 pending_id 用過一次即失效（單次使用），重放應再次被攔截
        request_replay = make_mocked_request("POST", "/api/mcp/rpc")
        request_replay.json = AsyncMock(return_value=req_data_auth)
        response_replay = await handle_mcp_rpc(request_replay)
        self.assertEqual(response_replay.status, 403)

if __name__ == "__main__":
    unittest.main()
