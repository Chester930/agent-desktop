"""2026-07-11：Agent 的 skills: [...] 欄位之前只是 metadata 標籤，從沒有人
把 skill 檔案的實際內容讀出來塞進 prompt——真正生效與否完全依賴底層 CLI
自己原生的 slash-skill 機制（Claude/Codex 讀的路徑還不一樣，Codex 那條
已知目前是壞的）。這裡驗證 _agent_run_capture()（Team Run 執行引擎）真的
會把 agent 引用的 skill 實際內容折進送給 engine.run_turn() 的 prompt，
對 Claude／Codex 兩邊都要生效，不再依賴任何一邊 CLI 的原生載入機制。
"""
import pytest

from engines.base import RunResult
import routes.teams as teams_module
from engines import claude_engine, codex_engine

pytestmark = pytest.mark.asyncio


def _write_agent(agents_dir, agent_id: str, skills: list, engine: str = "") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    skills_yaml = "[" + ", ".join(skills) + "]"
    engine_line = f"engine: {engine}\n" if engine else ""
    (agents_dir / f"{agent_id}.md").write_text(
        f"---\nname: {agent_id}\ndescription: test\nskills: {skills_yaml}\n{engine_line}---\n\nbody\n",
        encoding="utf-8",
    )


def _write_skill(skills_dir, skill_id: str, body: str) -> None:
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / f"{skill_id}.md").write_text(
        f"---\ndescription: test skill\n---\n\n{body}\n", encoding="utf-8",
    )


async def test_agent_run_capture_folds_skill_content_into_prompt_for_claude(monkeypatch, tmp_path):
    import database
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills"
    # 2026-07-13 起預設引擎是 Codex——這則測試在意的是 Claude 路徑的 skill
    # 注入邏輯，不是預設引擎本身，明確指定 engine: claude 讓測試意圖不隨
    # 預設值變動而跟著壞掉。
    _write_agent(agents_dir, "skilled-agent", ["tdd"], engine="claude")
    _write_skill(skills_dir, "tdd", "永遠先寫測試，紅燈-綠燈-重構。")
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(database, "SKILLS_DIR", skills_dir)

    captured = {}

    async def fake_run_turn(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        return RunResult(output="ok", session_id="sid")

    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    teams_module._team_runs["skills-run-claude"] = {"status": "running"}
    await teams_module._agent_run_capture("skills-run-claude", 0, "skilled-agent", "任務內容", "", str(tmp_path))

    assert "[Skills]" in captured["prompt"]
    assert "永遠先寫測試，紅燈-綠燈-重構。" in captured["prompt"]
    assert "[Skill: tdd]" in captured["prompt"]


async def test_agent_run_capture_folds_skill_content_into_prompt_for_codex(monkeypatch, tmp_path):
    """同一份 skill 內容注入邏輯要對 Codex 引擎一樣生效——這正是這次要修的
    問題本身：skill 內容以前完全依賴 Claude CLI 自己的原生載入機制，對
    Codex 從來沒生效過。"""
    import database
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills"
    _write_agent(agents_dir, "codex-skilled-agent", ["web-design"], engine="codex")
    _write_skill(skills_dir, "web-design", "注重可用性與對比度。")
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(database, "SKILLS_DIR", skills_dir)

    captured = {}

    async def fake_codex_run_turn(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        return RunResult(output="ok", session_id="sid")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    teams_module._team_runs["skills-run-codex"] = {"status": "running"}
    await teams_module._agent_run_capture("skills-run-codex", 0, "codex-skilled-agent", "任務內容", "", str(tmp_path))

    assert "注重可用性與對比度。" in captured["prompt"]


async def test_agent_run_capture_without_skills_has_no_skills_section(monkeypatch, tmp_path):
    import database
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    # 同上：明確指定 engine: claude，跟預設引擎是誰無關。
    (agents_dir / "plain-agent.md").write_text(
        "---\nname: plain-agent\ndescription: test\nengine: claude\n---\n\nbody\n", encoding="utf-8",
    )
    monkeypatch.setattr(database, "AGENTS_DIR", agents_dir)

    captured = {}

    async def fake_run_turn(**kwargs):
        captured["prompt"] = kwargs.get("prompt", "")
        return RunResult(output="ok", session_id="sid")

    monkeypatch.setattr(claude_engine, "run_turn", fake_run_turn)

    teams_module._team_runs["skills-run-none"] = {"status": "running"}
    await teams_module._agent_run_capture("skills-run-none", 0, "plain-agent", "任務內容", "", str(tmp_path))

    assert "[Skills]" not in captured["prompt"]
