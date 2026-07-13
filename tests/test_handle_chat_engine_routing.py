"""2026-07-11：handle_chat（主聊天室，選了某個 agent 之後打字聊天）之前
完全沒有讀取 agent 的 engine: frontmatter 欄位，寫死呼叫 Claude——就算把
某個 agent 設成 engine: codex，透過主聊天室互動時這個設定會被完全忽略。

這裡驗證：agent 宣告 engine: codex 時，handle_chat 真的會呼叫
codex_engine.run_turn()，完全不觸碰 SessionPool/ClaudeSDKClient 或
claude_engine；agent 宣告 engine: claude（或沒宣告）時，維持現有行為
100% 不變（走 legacy subprocess，因為測試環境沒有 HAS_AGENT_SDK）。
"""
import json

import pytest

from engines.base import RunResult

pytestmark = pytest.mark.asyncio


def _write_agent(agents_dir, agent_id: str, engine: str = "", skills: list = None) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    skills_yaml = f"skills: [{', '.join(skills)}]\n" if skills else ""
    engine_line = f"engine: {engine}\n" if engine else ""
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\n{skills_yaml}{engine_line}---\n\nagent body 內容\n",
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


async def test_handle_chat_routes_to_codex_when_agent_declares_it(client, monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "codex-chat-agent", engine="codex")

    captured = {}

    async def fake_codex_run_turn(**kwargs):
        captured["kwargs"] = kwargs
        await kwargs["on_text"]("Hello from codex")
        return RunResult(output="Hello from codex", session_id="sid-codex-chat")

    async def fake_claude_run_turn(**kwargs):
        raise AssertionError("claude_engine.run_turn should not be called for a codex-declared agent")

    from engines import codex_engine, claude_engine
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)
    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    resp = await client.post("/api/chat", json={
        "message": "你是哪個引擎？", "client_id": "test-client-codex", "agent": "codex-chat-agent",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)

    assert "kwargs" in captured
    assert captured["kwargs"]["prompt"]  # full_message 有被組出來傳進去
    assistant_events = [e for e in events if e.get("type") == "assistant"]
    assert any("Hello from codex" in b.get("text", "") for e in assistant_events for b in e["message"]["content"])


async def test_handle_chat_defaults_to_codex_when_no_engine_declared(client, monkeypatch, app):
    """沒有宣告 engine: 時，2026-07-13 起預設引擎是 Codex（不再是 Claude）——
    使用者確認：兩邊 CLI 都能用時，預設選 Codex。"""
    import main
    _write_agent(main.AGENTS_DIR, "default-engine-chat-agent", engine="")

    claude_called = False

    async def fake_claude_run_turn(**kwargs):
        nonlocal claude_called
        claude_called = True
        return RunResult(output="should not happen")

    from engines import claude_engine, codex_engine
    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("預設引擎回覆")
        return RunResult(output="預設引擎回覆", session_id="sid-default-codex")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-client-default-engine", "agent": "default-engine-chat-agent",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert len(codex_calls) == 1
    assert not claude_called


async def test_handle_chat_passes_resolved_codex_api_key(client, monkeypatch, app, tmp_path):
    """2026-07-13：_resolve_agent_engine_and_key() 的三選一分支——agent 宣告
    engine: codex 時，codex_engine.run_turn() 應該收到
    main._resolve_codex_api_key() 解析出來的值（讀 codexApiKeyCmd 這個獨立
    的 config key），而不是繼續傳空字串。"""
    import main
    _write_agent(main.AGENTS_DIR, "codex-key-agent", engine="codex")

    main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
    main.CONFIG_FILE.write_text(
        json.dumps({"projectDir": "", "claudeHome": str(tmp_path), "codexApiKeyCmd": "echo resolved-codex-key"}),
        encoding="utf-8",
    )

    captured = {}

    async def fake_codex_run_turn(**kwargs):
        captured["api_key"] = kwargs.get("api_key")
        await kwargs["on_text"]("ok")
        return RunResult(output="ok", session_id="sid-codex-key-test")

    from engines import codex_engine
    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/chat", json={
        "message": "hi", "client_id": "test-client-codex-key", "agent": "codex-key-agent",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert captured["api_key"] == "resolved-codex-key"
