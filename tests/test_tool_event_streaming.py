"""2026-07-17：型別化串流事件層 Phase 1（見 docs/ENHANCEMENT-ROADMAP.md）。

背景：claude_engine.py／codex_engine.py 的 run_turn() 原本只把純文字
（assistant text／agent_message）餵給 on_text callback，工具呼叫的結構化
資訊（Claude 的 tool_use/tool_result content block、Codex 的
command_execution/file_change item）完全沒有路徑處理，直接消失——不是
簡化成文字，是連 on_text 都沒收到。main.py::handle_chat 的 _run_pooled()
（Claude Agent SDK 原生路徑）本來就有正確處理，這裡補齊的是其餘三條路徑：
handle_chat 的 _run_engine_turn()（非 Claude 引擎的單一對話）、
routes/teams.py 的 _agent_run_capture()（team run 的每個成員，不分引擎）。

新增的 on_tool_event callback（見 engines/base.py 的 OnToolEvent）：engine
組好完整的 envelope dict（跟 _run_pooled 已經在用、前端也已經在吃的
tool_use / user+tool_result 形狀一致）丟給呼叫端，呼叫端決定怎麼呈現——
handle_chat 直接轉發成 SSE，_agent_run_capture 格式化成可讀文字塞進既有
的 step_text 事件（team run 的 step 資料模型還沒有結構化的工具呼叫欄位，
見 docs/ENHANCEMENT-ROADMAP.md Phase 1 的範圍說明）。

下面測試用的 JSONL 內容是真實 CLI 呼叫（`claude -p ... --output-format
stream-json --verbose`、`codex exec --json`）實測抓下來的真實輸出格式，
不是憑空編的（codex 的 command_execution/file_change 兩個 item type 之前
完全沒有測試覆蓋，也沒有任何程式碼路徑處理過）。
"""
import json

import pytest

import routes.teams as teams_module
from engines import claude_engine, codex_engine
from engines.base import RunResult


class _Recorder:
    """on_text/on_tool_event 都是 async callback（engine 內部用
    `await on_text(...)` 呼叫）——不能直接傳 sync lambda，這裡包成
    真正回傳 coroutine 的小物件，順便記錄呼叫過的內容方便斷言。"""

    def __init__(self):
        self.items = []

    async def __call__(self, item):
        self.items.append(item)


def _fake_subprocess(lines: list[bytes]):
    async def fake_create_subprocess_exec(*args, **kwargs):
        class _FakeStdout:
            def __init__(self):
                self._lines = list(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._lines:
                    raise StopAsyncIteration
                return self._lines.pop(0)

        class _FakeProc:
            def __init__(self):
                self.stdout = _FakeStdout()
                self.stdin = None
                self.returncode = 0

            async def wait(self):
                return 0

        return _FakeProc()

    return fake_create_subprocess_exec


class TestClaudeEngineToolEvents:
    """真實 `claude -p ... --output-format stream-json --verbose` 執行一次
    `echo` shell 指令實測抓到的原始輸出（tool_use 的 id/name/input、
    tool_result 的 tool_use_id/content 都是真實欄位名稱，不是猜的）。"""

    async def test_tool_use_and_tool_result_forwarded(self, monkeypatch):
        lines = [
            (json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "text", "text": "Running it now."},
                    {"type": "tool_use", "id": "toolu_01Mn3Uhx", "name": "Bash",
                     "input": {"command": "echo hi", "description": "test"}},
                ]},
            }) + "\n").encode(),
            (json.dumps({
                "type": "user",
                "message": {"content": [
                    {"tool_use_id": "toolu_01Mn3Uhx", "type": "tool_result", "content": "hi", "is_error": False},
                ]},
            }) + "\n").encode(),
            (json.dumps({"type": "result", "session_id": "sid-1"}) + "\n").encode(),
        ]
        monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        on_text = _Recorder()
        on_tool_event = _Recorder()
        result = await claude_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="acceptEdits",
            resume_session_id=None, api_key="",
            on_text=on_text, on_tool_event=on_tool_event,
        )

        assert result.session_id == "sid-1"
        assert on_tool_event.items == [
            {"type": "tool_use", "id": "toolu_01Mn3Uhx", "name": "Bash",
             "input": {"command": "echo hi", "description": "test"}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_01Mn3Uhx", "content": "hi"},
            ]}},
        ]

    async def test_no_on_tool_event_still_works_like_before(self, monkeypatch):
        """on_tool_event 是 Optional——舊呼叫端不傳這個參數，行為要跟這次
        改動前完全一樣，不能因為多了工具呼叫解析邏輯就整個掛掉。"""
        lines = [
            (json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                ]},
            }) + "\n").encode(),
        ]
        monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        result = await claude_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="acceptEdits",
            resume_session_id=None, api_key="", on_text=_Recorder(),
        )
        assert result.output == "hello"


class TestCodexEngineToolEvents:
    """真實 `codex exec --json` 實測抓到的原始輸出（command_execution 的
    item.started/item.completed 兩階段、file_change 的 changes 陣列，
    都是真實欄位——之前完全沒有程式碼處理這兩種 item type，會被直接
    跳過）。"""

    async def test_command_execution_emits_matched_tool_use_and_result(self, monkeypatch):
        lines = [
            (json.dumps({"type": "thread.started", "thread_id": "th-1"}) + "\n").encode(),
            (json.dumps({"type": "turn.started"}) + "\n").encode(),
            (json.dumps({"type": "item.started", "item": {
                "id": "item_1", "type": "command_execution",
                "command": "echo hi", "aggregated_output": "", "exit_code": None, "status": "in_progress",
            }}) + "\n").encode(),
            (json.dumps({"type": "item.completed", "item": {
                "id": "item_1", "type": "command_execution",
                "command": "echo hi", "aggregated_output": "hi\n", "exit_code": 0, "status": "completed",
            }}) + "\n").encode(),
            (json.dumps({"type": "item.completed", "item": {
                "id": "item_2", "type": "agent_message", "text": "Done.",
            }}) + "\n").encode(),
            (json.dumps({"type": "turn.completed", "usage": {}}) + "\n").encode(),
        ]
        monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        on_tool_event = _Recorder()
        on_text = _Recorder()
        result = await codex_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="workspace-write",
            resume_session_id=None, api_key="",
            on_text=on_text, on_tool_event=on_tool_event,
        )

        assert result.session_id == "th-1"
        assert on_text.items == ["Done."]
        assert on_tool_event.items == [
            {"type": "tool_use", "id": "item_1", "name": "Bash", "input": {"command": "echo hi"}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "item_1", "content": "hi\n"},
            ]}},
        ]

    async def test_file_change_emits_matched_tool_use_and_result(self, monkeypatch):
        lines = [
            (json.dumps({"type": "thread.started", "thread_id": "th-2"}) + "\n").encode(),
            (json.dumps({"type": "item.started", "item": {
                "id": "item_1", "type": "file_change",
                "changes": [{"path": "hello.txt", "kind": "add"}], "status": "in_progress",
            }}) + "\n").encode(),
            (json.dumps({"type": "item.completed", "item": {
                "id": "item_1", "type": "file_change",
                "changes": [{"path": "hello.txt", "kind": "add"}], "status": "completed",
            }}) + "\n").encode(),
        ]
        monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        on_tool_event = _Recorder()
        await codex_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="workspace-write",
            resume_session_id=None, api_key="", on_text=_Recorder(),
            on_tool_event=on_tool_event,
        )

        assert on_tool_event.items == [
            {"type": "tool_use", "id": "item_1", "name": "Edit",
             "input": {"changes": [{"path": "hello.txt", "kind": "add"}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "item_1", "content": "add hello.txt"},
            ]}},
        ]

    async def test_unrecognized_item_type_falls_back_instead_of_being_dropped(self, monkeypatch):
        """mcp_tool_call／web_search／reasoning／plan_update，或未來版本
        新增的任何 item type：沒有專門處理邏輯，但至少要透過保底邏輯冒出
        一組 tool_use/tool_result，不能整個安靜消失（這是這次改動前的
        實際行為——item.completed 的 elif 鏈只認 agent_message/error，
        其他一律被吃掉）。"""
        lines = [
            (json.dumps({"type": "item.completed", "item": {
                "id": "item_9", "type": "some_future_item_type", "detail": "xyz",
            }}) + "\n").encode(),
        ]
        monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        on_tool_event = _Recorder()
        await codex_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="workspace-write",
            resume_session_id=None, api_key="", on_text=_Recorder(),
            on_tool_event=on_tool_event,
        )

        tool_events = on_tool_event.items
        assert len(tool_events) == 2
        assert tool_events[0]["type"] == "tool_use"
        assert tool_events[0]["id"] == "item_9"
        assert tool_events[0]["name"] == "some_future_item_type"
        assert tool_events[1]["message"]["content"][0]["tool_use_id"] == "item_9"

    async def test_item_started_missing_does_not_duplicate_tool_use(self, monkeypatch):
        """防禦性案例：如果某個 item 完全沒有 item.started（只有
        item.completed），不該因為 tool_use_sent_ids 沒記錄到就漏發
        tool_use——也不該因為保底邏輯重複發第二次。"""
        lines = [
            (json.dumps({"type": "item.completed", "item": {
                "id": "item_5", "type": "command_execution",
                "command": "echo x", "aggregated_output": "x", "exit_code": 0, "status": "completed",
            }}) + "\n").encode(),
        ]
        monkeypatch.setattr(codex_engine.asyncio, "create_subprocess_exec", _fake_subprocess(lines))

        on_tool_event = _Recorder()
        await codex_engine.run_turn(
            prompt="p", cwd=".", model="", permission_mode="workspace-write",
            resume_session_id=None, api_key="", on_text=_Recorder(),
            on_tool_event=on_tool_event,
        )
        tool_use_events = [e for e in on_tool_event.items if e["type"] == "tool_use"]
        assert len(tool_use_events) == 1


class TestFormatToolEventAsText:
    def test_tool_use_command(self):
        text = teams_module._format_tool_event_as_text(
            {"type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"}}
        )
        assert "Bash" in text and "echo hi" in text

    def test_tool_use_file_changes(self):
        text = teams_module._format_tool_event_as_text({
            "type": "tool_use", "id": "i1", "name": "Edit",
            "input": {"changes": [{"path": "a.txt", "kind": "add"}]},
        })
        assert "Edit" in text and "a.txt" in text

    def test_tool_result(self):
        text = teams_module._format_tool_event_as_text({
            "type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "i1", "content": "output here"},
            ]},
        })
        assert "output here" in text

    def test_unknown_event_type_returns_empty_not_error(self):
        assert teams_module._format_tool_event_as_text({"type": "something_else"}) == ""


class TestAgentRunCaptureToolEventWiring:
    """驗證 _agent_run_capture 真的把 on_tool_event 接上 engine.run_turn()，
    而且格式化過的文字有透過既有的 step_text 事件送出——不重新測
    codex_engine/claude_engine 的解析邏輯（上面兩個 class 已經測過），
    這裡只測「線有接上」。"""

    async def test_tool_event_reaches_step_text(self, monkeypatch, tmp_path):
        import database
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "wired-agent.md").write_text(
            "---\nname: wired-agent\ndescription: test\nengine: codex\n---\n\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(database, "REGISTRY_AGENTS_DIR", agents_dir)

        async def fake_codex_run_turn(**kwargs):
            await kwargs["on_tool_event"]({
                "type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"},
            })
            await kwargs["on_tool_event"]({
                "type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "i1", "content": "hi"},
                ]},
            })
            return RunResult(output="done", session_id="sid")

        monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

        run_id = "tool-event-wiring-1"
        teams_module._team_runs[run_id] = {"status": "running"}
        teams_module._team_events[run_id] = []
        teams_module._team_queues[run_id] = []

        await teams_module._agent_run_capture(
            run_id, 0, "wired-agent", "do it", "", str(tmp_path),
            permission_mode="workspace-write", default_engine="codex",
        )

        step_texts = [
            e["text"] for e in teams_module._team_events[run_id]
            if e.get("type") == "step_text"
        ]
        joined = "".join(step_texts)
        assert "Bash" in joined and "echo hi" in joined
        assert "hi" in joined

    async def test_tool_event_also_forwarded_in_canonical_shape_with_step(self, monkeypatch, tmp_path):
        """見 docs/SDD-2026-09-acp-aligned-event-schema.md 執行紀錄：除了
        既有的格式化 step_text，_on_tool_event 也原樣轉發 tool_use/user
        envelope（附上 step 索引）。這個 envelope 形狀跟
        frontend/src/app/agent-events.ts 的 normalizeAgentEvents() 現有的
        'tool_use'/'user' case 完全一致——是後端骨架，目前還沒有前端消費者，
        但必須確保它不會取代或破壞既有的 step_text 事件。"""
        import database
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "wired-agent.md").write_text(
            "---\nname: wired-agent\ndescription: test\nengine: codex\n---\n\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(database, "REGISTRY_AGENTS_DIR", agents_dir)

        async def fake_codex_run_turn(**kwargs):
            await kwargs["on_tool_event"]({
                "type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"},
            })
            await kwargs["on_tool_event"]({
                "type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": "i1", "content": "hi"},
                ]},
            })
            return RunResult(output="done", session_id="sid")

        monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

        run_id = "tool-event-wiring-2"
        teams_module._team_runs[run_id] = {"status": "running"}
        teams_module._team_events[run_id] = []
        teams_module._team_queues[run_id] = []

        await teams_module._agent_run_capture(
            run_id, 2, "wired-agent", "do it", "", str(tmp_path),
            permission_mode="workspace-write", default_engine="codex",
        )

        events = teams_module._team_events[run_id]
        tool_use_events = [e for e in events if e.get("type") == "tool_use"]
        tool_result_events = [e for e in events if e.get("type") == "user"]

        assert tool_use_events == [
            {"type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"}, "step": 2},
        ]
        assert tool_result_events == [
            {"type": "user", "step": 2, "message": {"content": [
                {"type": "tool_result", "tool_use_id": "i1", "content": "hi"},
            ]}},
        ]
        # The existing step_text formatting must still be present, unchanged.
        assert any(e.get("type") == "step_text" for e in events)


class TestHandleChatForwardsToolEvents:
    """驗證 handle_chat 的 _run_engine_turn（非 Claude 引擎的單一對話路徑）
    真的把 on_tool_event 收到的 envelope 原封不動轉發成 SSE——前端既有的
    'tool' bubble 不用改一行就吃得到。"""

    async def test_codex_agent_chat_streams_tool_use_event(self, client, monkeypatch, app, tmp_path):
        import main
        from engines import availability

        async def _available(force: bool = False) -> dict:
            return {
                "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
                "codex": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            }
        monkeypatch.setattr(availability, "get_status", _available)

        main.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        (main.AGENTS_DIR / "codex-tool-agent.md").write_text(
            "---\nname: codex-tool-agent\ndescription: test\nengine: codex\n---\n\nbody\n",
            encoding="utf-8",
        )

        async def fake_codex_run_turn(**kwargs):
            await kwargs["on_tool_event"]({
                "type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"},
            })
            return RunResult(output="done", session_id="sid-chat")

        monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

        resp = await client.post("/api/chat", json={
            "message": "hi", "client_id": "test-tool-event-client", "agent": "codex-tool-agent",
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
        tool_use_events = [e for e in events if e.get("type") == "tool_use"]
        assert len(tool_use_events) == 1
        assert tool_use_events[0]["name"] == "Bash"
        assert tool_use_events[0]["input"]["command"] == "echo hi"
