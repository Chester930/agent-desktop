"""2026-07-10 team 協作優化健檢：handle_team_chat() 的 _build_full_prompt()
呼叫 build_team_memory_context() 時傳入 `all_members_list`，但這個名字在
整個 handle_team_chat() 裡從未被定義過——唯一存在的是 `member_agent_ids`
（line 693）。_build_full_prompt() 會在每一次「第一輪對話」或「還沒有
persisted session」時被呼叫，也就是「💬 團隊對話」這個功能幾乎每次使用都
會先撞到 NameError，整條功能等於是壞的（跟 T19 wrap_cmd 從未 import 是
同一類「看起來已經上線、實際上一叫就炸」的問題）。

之所以先前的整合測試（tests/test_backend.py::test_team_chat_endpoint）沒有
抓到，是因為那個測試只斷言 `resp.status == 200` 和 `"data:" in body`——
handle_team_chat() 對所有例外都用 `except Exception as e: ... {"type":
"error", ...}` 包成一個「正常的」SSE data: 事件回傳，所以就算內部整個
NameError 炸掉，HTTP 層看起來還是 200 + 有 "data:"，測試因此誤判成功。

這個測試改成明確斷言回應內容不含錯誤事件、且真的執行到 agent 呼叫。
"""
import pytest

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
        self.returncode = 0

    async def wait(self):
        return 0


async def test_first_turn_team_chat_does_not_raise_nameerror(
    client, monkeypatch, tmp_claude_home, sample_team, sample_agent,
):
    import main
    main.CLAUDE_HOME = tmp_claude_home
    main.TEAMS_DIR = tmp_claude_home / "teams"
    main.AGENTS_DIR = tmp_claude_home / "agents"

    lines = [
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"hello from leader"}]}}\n',
        b'{"type":"result","session_id":"sid-1"}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(lines)

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(main, "HAS_AGENT_SDK", False)  # force the legacy subprocess path deterministically
    # 2026-07-13 起預設引擎是 Codex；這則測試在意的是 Claude legacy subprocess
    # 路徑本身的 NameError 回歸，不是預設引擎，用共用的 session-scoped
    # sample_agent/sample_team fixture 又不該直接改檔案（會汙染其他測試），
    # 改成鎖定 get_engine_mode() 回傳 'claude'，測試本體乾淨、不留副作用。
    import database
    monkeypatch.setattr(database, "get_engine_mode", lambda: "claude")

    payload = {
        "message": "第一次打招呼",
        "team_id": "test-team",
        "client_id": "test-client-first-turn",
        "cwd": str(tmp_claude_home),
    }
    resp = await client.post("/api/team/chat", json=payload)
    assert resp.status == 200
    body = await resp.text()

    assert "NameError" not in body
    assert "all_members_list" not in body
    assert '"type": "error"' not in body
    assert "hello from leader" in body
