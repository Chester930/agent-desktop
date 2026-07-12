"""2026-07-10 team 協作優化健檢：HR Agent 自動組隊產生的 plan 從未帶
execution_mode 欄位，即使 routes/teams.py 已經修好「execution_mode 會被
正確套用」的傳遞機制（見 test_team_run_execution_mode.py），HR 派發出來的
team 實際上還是永遠平行跑——因為 _run_hr_agent() 的 prompt/JSON schema
根本沒要求模型輸出這個欄位，套用端自然拿不到值，只能 fallback 成預設的
"parallel"。

修法：① prompt/schema 明確要求固定輸出 "execution_mode": "sequential"
（這個 team 本來就是設計成前一位輸出傳給下一位）；② 解析完 JSON 後再補一層
防呆，模型漏填時由後端直接補上 "sequential"，不依賴模型 100% 照 schema 輸出。

2026-07-11：_run_hr_agent() 改走 engines/ 抽象（見 routes/agents.py），底層
不再直接呼叫 routes.agents.asyncio.create_subprocess_exec，改成透過
engines/claude_engine.py 的 run_turn()（stream-json 事件解析）。這裡的
monkeypatch 目標跟著改成 claude_engine.asyncio.create_subprocess_exec，
fake proc 也改成吐 stream-json 格式的事件，跟 tests/test_engine_registry.py
用的手法一致。
"""
import tempfile
from pathlib import Path

import pytest

import routes.agents as agents_module
from engines import claude_engine, codex_engine

pytestmark = pytest.mark.asyncio


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

    async def wait(self):
        return 0


def _stream_json_lines(text: str) -> list:
    import json as _json
    return [
        (_json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n").encode("utf-8"),
        (_json.dumps({"type": "result", "session_id": "sid-hr-test"}) + "\n").encode("utf-8"),
    ]


def _make_agents_dir():
    d = Path(tempfile.mkdtemp())
    (d / "coder.md").write_text(
        "---\nname: coder\ndescription: 寫代碼\n---\n\n身體\n", encoding="utf-8"
    )
    return d


async def test_model_omits_execution_mode_gets_defaulted_to_sequential(monkeypatch):
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    # simulate the model forgetting to include execution_mode despite the prompt
    fake_json = '{"name": "auto-team", "description": "d", "members": [{"agent": "coder", "role": "r", "input_memory": [], "output_memory": []}]}'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(_stream_json_lines(fake_json))

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    # 2026-07-13 起預設引擎是 Codex——這幾則測試在意的是 execution_mode
    # 解析/補預設值邏輯，不是預設引擎本身，明確指定 engine_name="claude"
    # 讓測試意圖不隨預設值變動而跟著壞掉。
    plan = await agents_module._run_hr_agent("build something", engine_name="claude")

    assert "error" not in plan
    assert plan["execution_mode"] == "sequential"


async def test_model_explicit_execution_mode_is_preserved(monkeypatch):
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    fake_json = '{"name": "auto-team", "description": "d", "execution_mode": "sequential", "members": [{"agent": "coder", "role": "r"}]}'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(_stream_json_lines(fake_json))

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    # 2026-07-13 起預設引擎是 Codex——這幾則測試在意的是 execution_mode
    # 解析/補預設值邏輯，不是預設引擎本身，明確指定 engine_name="claude"
    # 讓測試意圖不隨預設值變動而跟著壞掉。
    plan = await agents_module._run_hr_agent("build something", engine_name="claude")

    assert plan["execution_mode"] == "sequential"


async def test_markdown_fenced_response_still_gets_default(monkeypatch):
    """Model wraps the JSON in ```json fences AND forgets execution_mode —
    both recovery paths (fence stripping, substring extraction) must still
    apply the sequential default."""
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    fake_json = '```json\n{"name": "auto-team", "description": "d", "members": [{"agent": "coder", "role": "r"}]}\n```'

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(_stream_json_lines(fake_json))

    monkeypatch.setattr(claude_engine.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    # 2026-07-13 起預設引擎是 Codex——這幾則測試在意的是 execution_mode
    # 解析/補預設值邏輯，不是預設引擎本身，明確指定 engine_name="claude"
    # 讓測試意圖不隨預設值變動而跟著壞掉。
    plan = await agents_module._run_hr_agent("build something", engine_name="claude")

    assert "error" not in plan
    assert plan["execution_mode"] == "sequential"


async def test_hr_agent_routes_to_codex_when_requested(monkeypatch):
    """2026-07-11：_run_hr_agent() 新增 engine_name 參數，讓 HR 派發本身
    （挑選 Agent 組隊的那次文字補全）也能選 Codex 執行，不再永遠寫死呼叫
    Claude CLI。這裡驗證 engine_name="codex" 真的會路由到 codex_engine，
    而不是預設的 claude_engine。"""
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    fake_json = '{"name": "auto-team", "description": "d", "execution_mode": "sequential", "members": [{"agent": "coder", "role": "r"}]}'

    claude_called = False

    async def fake_claude_run_turn(**kwargs):
        nonlocal claude_called
        claude_called = True
        raise AssertionError("claude_engine.run_turn should not be called when engine_name='codex'")

    async def fake_codex_run_turn(**kwargs):
        from engines.base import RunResult
        return RunResult(output=fake_json, session_id="sid-codex-hr")

    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    plan = await agents_module._run_hr_agent("build something", engine_name="codex")

    assert not claude_called
    assert "error" not in plan
    assert plan["execution_mode"] == "sequential"


async def test_hr_agent_does_not_leak_anthropic_key_into_codex_env(monkeypatch):
    """2026-07-11：resolve_key()（_resolve_api_key()）只解析 Anthropic key。
    之前不分引擎一律傳給 engine.run_turn() 的 api_key 參數——如果使用者設定
    了 Anthropic key、又選 Codex 執行 HR 派發，會把 Anthropic key 誤植進
    codex_engine.py 的 CODEX_API_KEY 環境變數，蓋掉正常運作的 codex login
    憑證（見 routes/agents.py::_run_hr_agent() 與 routes/teams.py::
    _agent_run_capture() 的同一段註解）。這裡驗證即使 main._resolve_api_key()
    回傳非空字串，codex_engine.run_turn() 收到的 api_key 仍然是空字串。"""
    agents_dir = _make_agents_dir()
    import database
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    import sys
    fake_main = type(sys)("fake_main_for_hr_key_test")
    fake_main.CLAUDE_BIN = "claude"
    fake_main._resolve_api_key = lambda: "sk-ant-should-not-leak"
    monkeypatch.setitem(sys.modules, "main", fake_main)

    captured = {}

    async def fake_codex_run_turn(**kwargs):
        from engines.base import RunResult
        captured["api_key"] = kwargs.get("api_key")
        return RunResult(output='{"name": "t", "description": "d", "execution_mode": "sequential", "members": []}')

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    await agents_module._run_hr_agent("build something", engine_name="codex")

    assert captured["api_key"] == ""
