"""2026-07-10 team 協作優化健檢：發現 2 — /api/team/run 完全沒有工具權限
核准機制。已用真實 claude CLI（2.1.206）驗證過兩件事：

1. headless -p 模式下即使 stdin=PIPE，遇到需要核准的工具呼叫也只會等待
   3 秒後自動判斷（對外部/敏感路徑自動拒絕），不會產生可偵測、可回應的
   互動式權限提示——`_legacy_exec`（main.py handle_team_execute）那套
   「偵測 raw text prompt 寫 y/n 到 stdin」的假設，在這個 CLI 版本下對
   headless -p 模式並不成立，代表要做到真正互動式核准，唯一可行路徑是
   pooled SDK 的 can_use_tool callback，工程量遠大於原本評估的「中等」。
2. `--permission-mode acceptEdits` 底下，同樣的 Write/Bash 操作在非敏感
   路徑會直接成功（zero permission_denials）；Claude Code 自身對 .claude/
   等敏感路徑的硬性保護不受這個 flag 影響、依然生效。

使用者確認方向：開放 acceptEdits 作為 _agent_run_capture 的預設
permission_mode，讓 team member 真正能協作完成任務。這個測試驗證：
- _agent_run_capture 預設會把 --permission-mode acceptEdits 加進 CLI 指令。
- POST /api/team/run 接受、驗證、並把 permission_mode 存進 run state。
- 不合法的 permission_mode 值會被擋下（比照 _is_safe_id 一類的既有輸入
  驗證慣例）。
"""
import pytest

import routes.teams as teams_module

pytestmark = pytest.mark.asyncio


async def test_agent_run_capture_defaults_to_accept_edits(monkeypatch):
    captured_cmd = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmd["args"] = args
        raise FileNotFoundError("forced: no real subprocess spawned in this test")

    monkeypatch.setattr(teams_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    # 2026-07-13 起預設引擎是 Codex（不會組出 --permission-mode，那是 Claude
    # CLI 專屬的旗標）；這則測試在意的是 Claude 側 acceptEdits 預設值本身，
    # 明確指定 default_engine="claude"。
    await teams_module._agent_run_capture(
        "run-x", 0, "nonexistent-agent", "prompt", "haiku", "/tmp",
        default_engine="claude",
    )

    cmd = captured_cmd["args"]
    assert "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"


async def test_agent_run_capture_respects_explicit_permission_mode(monkeypatch):
    captured_cmd = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmd["args"] = args
        raise FileNotFoundError("forced: no real subprocess spawned in this test")

    monkeypatch.setattr(teams_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await teams_module._agent_run_capture(
        "run-x", 0, "nonexistent-agent", "prompt", "haiku", "/tmp", "plan",
        default_engine="claude",
    )

    cmd = captured_cmd["args"]
    assert cmd[cmd.index("--permission-mode") + 1] == "plan"


async def test_team_run_post_defaults_permission_mode_to_accept_edits(client, monkeypatch):
    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits", default_engine=""):
        return f"output-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    resp = await client.post("/api/team/run", json={
        "task": "task",
        "team": {"name": "t", "members": [{"agent": "ok-agent", "role": "r"}]},
    })
    assert resp.status == 200
    body = await resp.json()
    assert teams_module._team_runs[body["run_id"]]["permission_mode"] == "acceptEdits"


async def test_team_run_post_rejects_invalid_permission_mode(client):
    resp = await client.post("/api/team/run", json={
        "task": "task",
        "team": {"name": "t", "members": [{"agent": "ok-agent", "role": "r"}]},
        "permission_mode": "rm -rf /; --dangerous",
    })
    assert resp.status == 400
