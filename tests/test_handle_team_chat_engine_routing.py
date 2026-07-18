"""2026-07-11：handle_team_chat（「💬 團隊對話」，跟一個已存檔的 Team 即時
聊天）之前完全沒有讀取 agent 的 engine: frontmatter 欄位，寫死呼叫 Claude。
同一次團隊對話裡，組長跟每個成員可能各自宣告不同引擎，所以判斷邏輯要放在
run_single_agent(agent_id) 內部，不能在 handle_team_chat 最外層判斷一次
就好——這裡驗證的正是這件事：團隊組長宣告 engine: codex 時，第一輪對話
真的會呼叫 codex_engine.run_turn()，完全不觸碰 SessionPool/claude_engine。
"""
import json

import pytest

from engines.base import RunResult



def _write_agent(agents_dir, agent_id: str, engine: str = "") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    engine_line = f"engine: {engine}\n" if engine else ""
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\n{engine_line}---\n\nagent body\n",
        encoding="utf-8",
    )


def _write_team(teams_dir, team_id: str, leader: str, members: list) -> None:
    teams_dir.mkdir(parents=True, exist_ok=True)
    members_yaml = "\n".join(f"  - agent: {m}\n    role: 測試角色" for m in members)
    (teams_dir / f"{team_id}.yaml").write_text(
        f"name: {team_id}\ndescription: test team\nleader: {leader}\nmembers:\n{members_yaml}\n",
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


async def test_team_chat_leader_routes_to_codex(client, monkeypatch, app):
    import main
    _write_agent(main.AGENTS_DIR, "codex-leader", engine="codex")
    _write_agent(main.AGENTS_DIR, "claude-member", engine="")
    _write_team(main.TEAMS_DIR, "mixed-chat-team", "codex-leader", ["codex-leader", "claude-member"])

    from engines import codex_engine, claude_engine
    codex_calls = []

    async def fake_codex_run_turn(**kwargs):
        codex_calls.append(kwargs)
        await kwargs["on_text"]("身為組長，我用 Codex 回覆。")  # 沒有 @mention，討論迴圈跑完一輪就結束
        return RunResult(output="身為組長，我用 Codex 回覆。", session_id="sid-team-codex")

    async def fake_claude_run_turn(**kwargs):
        raise AssertionError("claude_engine.run_turn should not be called when the leader is engine: codex")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)
    monkeypatch.setattr(claude_engine, "run_turn", fake_claude_run_turn)

    resp = await client.post("/api/team/chat", json={
        "message": "請開始討論", "client_id": "test-client-team-codex", "team_id": "mixed-chat-team",
    })
    assert resp.status == 200
    events = await _read_sse_events(resp)

    assert len(codex_calls) == 1
    text_events = [e for e in events if e.get("type") == "text" and e.get("agent") == "codex-leader"]
    assert any("身為組長，我用 Codex 回覆。" in e.get("text", "") for e in text_events)
    assert any(e.get("type") == "agent_start" and e.get("agent") == "codex-leader" for e in events)
    assert any(e.get("type") == "agent_done" and e.get("agent") == "codex-leader" for e in events)


async def test_team_chat_strips_sonnet_model_alias_for_non_claude_engine(client, monkeypatch, app):
    """2026-07-18：跟 test_handle_chat_engine_routing.py 的同名測試同一個
    bug——"sonnet" 是 Claude-only 的 UI 佔位值，engine.run_turn() 這條路徑
    漏了過濾，導致選了 Sonnet 4.6 當全域模型、組長又宣告 engine: codex 時，
    "sonnet" 字面值直接傳給 Codex CLI 當 --model 參數，實測重現過
    「Model metadata for sonnet not found」錯誤。"""
    import main
    _write_agent(main.AGENTS_DIR, "codex-leader-2", engine="codex")
    _write_team(main.TEAMS_DIR, "sonnet-strip-team", "codex-leader-2", ["codex-leader-2"])

    from engines import codex_engine
    captured = {}

    async def fake_codex_run_turn(**kwargs):
        captured["model"] = kwargs.get("model")
        await kwargs["on_text"]("ok")
        return RunResult(output="ok", session_id="sid-team-sonnet-strip")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/team/chat", json={
        "message": "請開始討論", "client_id": "test-client-team-sonnet-strip", "team_id": "sonnet-strip-team",
        "model": "sonnet",
    })
    assert resp.status == 200
    await _read_sse_events(resp)

    assert captured["model"] is None
