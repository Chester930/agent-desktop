"""團隊對話（handle_team_chat）的 Skills 注入健檢：過去每位發言的 Agent
（不管是 Leader 還是被 @ 的成員）都只拿得到自己 frontmatter 宣告的
Skills，看不到隊友的——導致 Leader 明明知道隊友是誰，卻沒辦法真的運用
隊友的專長。main.py::handle_team_chat 現在改成把整個團隊宣告過的 skill
id 聯集起來，不管誰發言，都能看到整個團隊的技能清單。

驗證方式跟 test_team_chat_first_turn_nameerror.py 同一套手法：mock
asyncio.create_subprocess_exec，捕捉實際傳給 claude CLI 的完整 prompt
（-p 參數），斷言 Leader 第一輪發言時，prompt 裡同時包含自己跟隊友宣告
的 Skill 內容。
"""
import pytest


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


def _write_agent(agents_dir, agent_id, skill_id):
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: 測試用\nskills:\n  - {skill_id}\n---\n\n測試 Agent。\n",
        encoding="utf-8",
    )


def _write_skill(skills_dir, skill_id, marker):
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"{skill_id}.md").write_text(
        f"---\nname: {skill_id}\ndescription: 測試用技能\n---\n\n{marker}\n",
        encoding="utf-8",
    )


def _write_team(teams_dir, team_id, leader, members):
    teams_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {team_id}", "description: 測試用團隊", f"leader: {leader}", "members:"]
    for m in members:
        lines.append(f"  - agent: {m}")
        lines.append(f"    role: 測試角色")
    (teams_dir / f"{team_id}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_leader_first_turn_sees_team_wide_skills_not_just_own(
    client, monkeypatch, tmp_claude_home,
):
    import main
    main.CLAUDE_HOME = tmp_claude_home
    main.TEAMS_DIR = tmp_claude_home / "teams-skillagg"
    main.AGENTS_DIR = tmp_claude_home / "agents-skillagg"

    import database
    database.REGISTRY_AGENTS_DIR = main.AGENTS_DIR
    database.REGISTRY_SKILLS_DIR = tmp_claude_home / "skills-skillagg"

    _write_agent(main.AGENTS_DIR, "leader-agent", "leader-skill")
    _write_agent(main.AGENTS_DIR, "member-agent", "member-skill")
    _write_skill(database.REGISTRY_SKILLS_DIR, "leader-skill", "LEADER_SKILL_MARKER_CONTENT")
    _write_skill(database.REGISTRY_SKILLS_DIR, "member-skill", "MEMBER_SKILL_MARKER_CONTENT")
    _write_team(main.TEAMS_DIR, "skillagg-team", "leader-agent", ["leader-agent", "member-agent"])

    captured_cmds = []
    lines = [
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"hello from leader"}]}}\n',
        b'{"type":"result","session_id":"sid-skillagg"}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_cmds.append(args)
        return _FakeProc(list(lines))

    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(main, "HAS_AGENT_SDK", False)
    monkeypatch.setattr(database, "get_engine_mode", lambda: "claude")

    payload = {
        "message": "第一次打招呼",
        "team_id": "skillagg-team",
        "client_id": "test-client-skillagg",
        "cwd": str(tmp_claude_home),
    }
    resp = await client.post("/api/team/chat", json=payload)
    assert resp.status == 200
    body = await resp.text()
    assert '"type": "error"' not in body

    assert len(captured_cmds) >= 1
    full_prompt = " ".join(str(a) for cmd_args in captured_cmds for a in cmd_args)
    assert "LEADER_SKILL_MARKER_CONTENT" in full_prompt
    assert "MEMBER_SKILL_MARKER_CONTENT" in full_prompt, (
        "Leader 發言時的 prompt 應該包含隊友（member-agent）宣告的 Skill 內容，"
        "不能只看到自己宣告的那份"
    )
