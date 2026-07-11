"""2026-07-11：MCP server 定義同步到 Claude／Codex 兩邊 CLI 的核心邏輯
（backend/mcp_sync.py）。這裡驗證：
1. 兩邊 CLI 的指令參數組裝正確（stdio/http 兩種 schema）。
2. 某一邊 CLI 呼叫失敗（含 CLI 不存在）不影響另一邊、不拋例外。
3. asyncio.Lock 確實序列化 sync_add/sync_remove。

不呼叫真實 CLI——真實驗證見對話紀錄裡另外跑過的隔離環境端對端測試。
"""
import asyncio

import pytest

import mcp_sync

pytestmark = pytest.mark.asyncio


def test_claude_add_args_stdio():
    args = mcp_sync._claude_add_args("my-server", {
        "type": "stdio", "command": "npx", "args": ["my-mcp-server"], "env": {"API_KEY": "xxx"},
    })
    assert args[:2] == ["mcp", "add"]
    assert "-s" in args and args[args.index("-s") + 1] == "user"
    assert "-e" in args and args[args.index("-e") + 1] == "API_KEY=xxx"
    assert "my-server" in args
    dash_idx = args.index("--")
    assert args[dash_idx + 1:] == ["npx", "my-mcp-server"]


def test_claude_add_args_http():
    args = mcp_sync._claude_add_args("sentry", {
        "type": "http", "url": "https://mcp.sentry.dev/mcp", "headers": {"Authorization": "Bearer xyz"},
    })
    assert "--transport" in args and args[args.index("--transport") + 1] == "http"
    assert "sentry" in args
    assert "https://mcp.sentry.dev/mcp" in args
    assert "--header" in args and args[args.index("--header") + 1] == "Authorization: Bearer xyz"


def test_codex_add_args_stdio():
    args = mcp_sync._codex_add_args("my-server", {
        "type": "stdio", "command": "npx", "args": ["my-mcp-server"], "env": {"API_KEY": "xxx"},
    })
    assert args[:3] == ["mcp", "add", "my-server"]
    assert "--env" in args and args[args.index("--env") + 1] == "API_KEY=xxx"
    dash_idx = args.index("--")
    assert args[dash_idx + 1:] == ["npx", "my-mcp-server"]


def test_codex_add_args_http_ignores_headers():
    """已確認：Codex 的 HTTP MCP 沒有任意 header 機制（只有
    --bearer-token-env-var／OAuth），headers 欄位在 Codex 這邊無法翻譯，
    靜默略過，不是 bug。"""
    args = mcp_sync._codex_add_args("sentry", {
        "type": "http", "url": "https://mcp.sentry.dev/mcp", "headers": {"Authorization": "Bearer xyz"},
    })
    assert args == ["mcp", "add", "sentry", "--url", "https://mcp.sentry.dev/mcp"]
    assert "--header" not in args


async def test_sync_add_one_cli_failure_does_not_block_the_other(monkeypatch):
    async def fake_run_cli(bin_path, args, timeout=30.0):
        return bin_path == "claude"  # codex 端模擬失敗（例如沒裝）

    monkeypatch.setattr(mcp_sync, "_run_cli", fake_run_cli)
    monkeypatch.setattr(mcp_sync, "_claude_bin", lambda: "claude")
    monkeypatch.setattr(mcp_sync, "_codex_bin", lambda: "codex")

    result = await mcp_sync.sync_add("my-server", {"type": "stdio", "command": "echo", "args": ["hi"]})

    assert result == {"claude": True, "codex": False}


async def test_sync_add_cli_not_found_returns_false_not_exception(monkeypatch):
    """_run_cli 本身遇到 FileNotFoundError（binary 不存在）要吞掉、回傳 False，
    不能讓整個 sync_add 拋例外。"""
    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(mcp_sync.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await mcp_sync.sync_add("my-server", {"type": "stdio", "command": "echo", "args": []})

    assert result == {"claude": False, "codex": False}


async def test_sync_remove_calls_both_clis(monkeypatch):
    captured = []

    async def fake_run_cli(bin_path, args, timeout=30.0):
        captured.append((bin_path, args))
        return True

    monkeypatch.setattr(mcp_sync, "_run_cli", fake_run_cli)
    monkeypatch.setattr(mcp_sync, "_claude_bin", lambda: "claude")
    monkeypatch.setattr(mcp_sync, "_codex_bin", lambda: "codex")

    result = await mcp_sync.sync_remove("my-server")

    assert result == {"claude": True, "codex": True}
    assert ("claude", ["mcp", "remove", "-s", "user", "my-server"]) in captured
    assert ("codex", ["mcp", "remove", "my-server"]) in captured


async def test_sync_operations_are_serialized_by_lock(monkeypatch):
    """驗證 _sync_lock 真的序列化了呼叫——用一個會記錄進入/離開順序的假
    _run_cli，確認同時發起的兩次 sync_add 不會交錯執行。"""
    call_order = []

    async def fake_run_cli(bin_path, args, timeout=30.0):
        call_order.append(f"start-{bin_path}")
        await asyncio.sleep(0.01)
        call_order.append(f"end-{bin_path}")
        return True

    monkeypatch.setattr(mcp_sync, "_run_cli", fake_run_cli)
    monkeypatch.setattr(mcp_sync, "_claude_bin", lambda: "claude")
    monkeypatch.setattr(mcp_sync, "_codex_bin", lambda: "codex")

    await asyncio.gather(
        mcp_sync.sync_add("server-a", {"type": "stdio", "command": "echo", "args": []}),
        mcp_sync.sync_add("server-b", {"type": "stdio", "command": "echo", "args": []}),
    )

    # 序列化的話，第一組 (claude,codex) 的 start/end 應該完整跑完才會輪到
    # 第二組——也就是不會出現 start-claude, start-claude, end-claude... 這種
    # 交錯模式。用「每次呼叫都成對緊接出現」來驗證。
    assert len(call_order) == 8
    for i in range(0, len(call_order), 2):
        start, end = call_order[i], call_order[i + 1]
        assert start.replace("start-", "") == end.replace("end-", "")
