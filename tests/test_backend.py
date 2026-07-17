"""
後端 API 整合測試 — 對應 ROADMAP.md 各 Phase 的核心端點

執行方式：
    cd agent-desktop
    pytest tests/ -v
"""
import json
import re
from pathlib import Path

from aiohttp import FormData
import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 工具函數單元測試（不需要 HTTP server）
# ══════════════════════════════════════════════════════════════════════════════

class TestHelperFunctions:
    """ROADMAP Phase 1 — 工具函數測試（sync）"""

    def test_encode_slug_windows_path(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
        import main
        slug = main._encode_slug("C:\\Users\\test\\project")
        assert "\\" not in slug
        assert ":" not in slug

    def test_encode_slug_unix_path(self):
        import main
        slug = main._encode_slug("/home/user/my-project")
        assert "/" not in slug

    def test_encode_slug_empty(self):
        import main
        assert main._encode_slug("") == ""

    def test_parse_yaml_simple_basic(self):
        import main
        result = main._parse_yaml_simple(
            "---\nname: test\ndescription: hello world\nskills: []\n---\n\nbody"
        )
        assert result.get("name") == "test"
        assert result.get("description") == "hello world"

    def test_parse_yaml_simple_list_values(self):
        import main
        result = main._parse_yaml_simple(
            "---\nskills:\n  - skill-a\n  - skill-b\nmemory: []\n---\n"
        )
        assert "skill-a" in result.get("skills", [])
        assert "skill-b" in result.get("skills", [])

    def test_parse_yaml_simple_no_frontmatter(self):
        import main
        result = main._parse_yaml_simple("just plain text without frontmatter")
        assert isinstance(result, dict)

    def test_agent_dict_parses_sample(self, tmp_claude_home, sample_agent):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        d = main._agent_dict(sample_agent)
        assert d["name"] == "test-agent"
        assert "test-skill" in d.get("skills", [])
        assert "test-memory-key" in d.get("memory", [])
        assert "test-output-key" in d.get("output_memory", [])

    def test_team_dict_parses_sample(self, tmp_claude_home, sample_team):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        d = main._team_dict(sample_team)
        assert d["name"] == "test-team"
        members = d.get("members", [])
        assert len(members) == 1
        assert members[0]["agent"] == "test-agent"

    def test_team_dict_leader_defaults_to_first_member(self, tmp_claude_home):
        """team.yaml 沒有 leader 欄位時，應自動 fallback 到第一個成員"""
        import main
        team_file = tmp_claude_home / "teams" / "no-leader-team.yaml"
        team_file.write_text(
            "name: no-leader-team\nmembers:\n  - agent: alpha-agent\n    role: 主導\n",
            encoding="utf-8",
        )
        d = main._team_dict(team_file)
        # leader 應為 members[0].agent
        assert d.get("leader") == "alpha-agent"
        team_file.unlink(missing_ok=True)

    def test_team_dict_leader_explicit(self, tmp_claude_home):
        """team.yaml 有明確 leader 欄位時，應使用指定的 leader"""
        import main
        team_file = tmp_claude_home / "teams" / "with-leader-team.yaml"
        team_file.write_text(
            "name: with-leader-team\nleader: beta-agent\nmembers:\n"
            "  - agent: alpha-agent\n    role: 第一\n"
            "  - agent: beta-agent\n    role: 組長\n",
            encoding="utf-8",
        )
        d = main._team_dict(team_file)
        assert d.get("leader") == "beta-agent"
        team_file.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 — 基礎健康檢查
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """GET /api/status"""

    async def test_status_ok(self, client):
        resp = await client.get("/api/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert "claude_bin" in body
        assert "active_sessions" in body

    async def test_status_method_not_allowed(self, client):
        resp = await client.post("/api/status")
        assert resp.status == 405

    async def test_config_get(self, client):
        resp = await client.get("/api/config")
        assert resp.status == 200
        body = await resp.json()
        assert "_resolvedClaudeHome" in body


class TestAudioTranscription:
    """POST /api/audio/transcriptions"""

    async def test_transcription_requires_audio_file(self, client):
        form = FormData()
        form.add_field("apiKey", "test-key", content_type="text/plain")

        resp = await client.post("/api/audio/transcriptions", data=form)
        assert resp.status == 400
        body = await resp.json()
        assert body["error"] == "missing audio file"

    async def test_transcription_requires_provider_api_key(self, client):
        form = FormData()
        form.add_field("mode", "cloud")
        form.add_field(
            "file",
            b"mock-audio",
            filename="recording.webm",
            content_type="audio/webm",
        )

        resp = await client.post("/api/audio/transcriptions", data=form)
        assert resp.status == 400
        body = await resp.json()
        assert body["error"] == "missing provider API key"

    async def test_transcription_defaults_to_local_mode(self, client, monkeypatch):
        import main

        async def fake_transcribe_local(audio_bytes, language=""):
            assert audio_bytes == b"mock-audio"
            return "本機辨識結果"

        monkeypatch.setattr(main.stt, "transcribe_local", fake_transcribe_local)

        form = FormData()
        form.add_field(
            "file",
            b"mock-audio",
            filename="recording.webm",
            content_type="audio/webm",
        )
        # 沒帶 mode 欄位 —— 預設要走本機，不需要任何 apiKey
        resp = await client.post("/api/audio/transcriptions", data=form)
        assert resp.status == 200
        body = await resp.json()
        assert body["text"] == "本機辨識結果"

    async def test_transcription_local_mode_unavailable(self, client, monkeypatch):
        import main

        async def fake_transcribe_local(audio_bytes, language=""):
            raise main.stt.LocalSttUnavailable("faster-whisper 未安裝")

        monkeypatch.setattr(main.stt, "transcribe_local", fake_transcribe_local)

        form = FormData()
        form.add_field("mode", "local")
        form.add_field(
            "file",
            b"mock-audio",
            filename="recording.webm",
            content_type="audio/webm",
        )
        resp = await client.post("/api/audio/transcriptions", data=form)
        assert resp.status == 503
        body = await resp.json()
        assert "faster-whisper" in body["error"]


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Agent Mapping
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentCRUD:
    """ROADMAP Phase 1 — Agent CRUD（P1-B1 ~ P1-B3, P1-M1 ~ P1-M2）"""

    async def test_list_agents_returns_list(self, client, sample_agent):
        resp = await client.get("/api/agents")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
        # 應包含我們建立的 test-agent
        ids = [a["id"] for a in body]
        assert "test-agent" in ids

    async def test_get_single_agent(self, client, sample_agent):
        resp = await client.get("/api/agents/test-agent")
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "test-agent"
        assert body["description"] == "用於測試的代理人"

    async def test_get_nonexistent_agent_404(self, client):
        resp = await client.get("/api/agents/does-not-exist-xyz")
        assert resp.status == 404

    async def test_create_agent(self, client, tmp_claude_home):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        payload = {"name": "new-test-agent", "description": "動態建立的代理人"}
        resp = await client.post("/api/agents", json=payload)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["id"] == "new-test-agent"
        # 確認檔案實際存在
        assert (tmp_claude_home / "agents" / "new-test-agent.md").exists()

    async def test_create_agent_duplicate_409(self, client, tmp_claude_home):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        payload = {"name": "new-test-agent", "description": "重複建立"}
        resp = await client.post("/api/agents", json=payload)
        assert resp.status == 409

    async def test_update_agent_description(self, client, sample_agent):
        resp = await client.put(
            "/api/agents/test-agent",
            json={"description": "更新後的描述"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        # 確認描述已更新
        resp2 = await client.get("/api/agents/test-agent")
        body2 = await resp2.json()
        assert body2["description"] == "更新後的描述"

    async def test_update_agent_favorite_round_trip(self, client, sample_agent, tmp_claude_home, monkeypatch):
        """健檢：favorite 用 bool(fm.get('favorite', False)) 判斷，但舊版
        _write_frontmatter 是用 Python str(False) 寫回 frontmatter（"False"
        大寫），下次讀回來變成非空字串 "False"，bool("False") 在 Python 裡
        還是 True——等於「取消收藏」永遠寫不進去，重整後又跑回來。"""
        import database
        monkeypatch.setattr(database, "CLAUDE_HOME", tmp_claude_home)
        try:
            resp = await client.put("/api/agents/test-agent", json={"favorite": True})
            assert resp.status == 200
            resp2 = await client.get("/api/agents/test-agent")
            body2 = await resp2.json()
            assert body2["favorite"] is True

            resp3 = await client.put("/api/agents/test-agent", json={"favorite": False})
            assert resp3.status == 200
            resp4 = await client.get("/api/agents/test-agent")
            body4 = await resp4.json()
            assert body4["favorite"] is False
        finally:
            await client.put("/api/agents/test-agent", json={"favorite": False})

    async def test_update_agent_skills(self, client, sample_agent):
        """sample_agent 是 session-scoped fixture，共用同一份 test-agent.md
        物理檔案——同樣的理由見 test_update_agent_engine 的說明，用
        try/finally 把 skills 還原成 fixture 原本的 [test-skill]，避免
        污染後面共用 test-agent 的測試。"""
        try:
            resp = await client.put(
                "/api/agents/test-agent",
                json={"skills": ["skill-a", "skill-b"]},
            )
            assert resp.status == 200
            # 讀回確認
            resp2 = await client.get("/api/agents/test-agent")
            body2 = await resp2.json()
            assert "skill-a" in body2.get("skills", [])
        finally:
            await client.put("/api/agents/test-agent", json={"skills": ["test-skill"]})

    async def test_update_agent_engine(self, client, sample_agent):
        """2026-07-11：Agent 編輯器 UI 加了引擎選擇下拉選單，engine 欄位要
        能透過 PUT /api/agents/{id} 寫進 frontmatter。

        sample_agent 是 session-scoped fixture，物理檔案 test-agent.md 整個
        測試 session 只建立一次、後面所有測試共用同一份。這裡的 PUT 會真的
        把 engine: codex 寫進那份共用檔案——如果不還原，後面任何假設
        test-agent 沒有宣告 engine（預期走 Claude）的測試（例如
        test_team_chat_first_turn_nameerror.py／
        test_team_chat_no_llm_auto_approve.py）會被這裡留下的髒狀態影響，
        意外路由到 Codex 導致失敗。用 try/finally 確保不管斷言成不成功，
        都會把 engine 欄位還原成空字串。"""
        try:
            resp = await client.put(
                "/api/agents/test-agent",
                json={"engine": "codex"},
            )
            assert resp.status == 200
            resp2 = await client.get("/api/agents/test-agent")
            body2 = await resp2.json()
            assert body2.get("engine") == "codex"
        finally:
            await client.put("/api/agents/test-agent", json={"engine": ""})

    async def test_update_agent_invalid_engine_400(self, client, sample_agent):
        resp = await client.put(
            "/api/agents/test-agent",
            json={"engine": "not-a-real-engine"},
        )
        assert resp.status == 400

    async def test_create_agent_with_engine(self, client, tmp_claude_home):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        payload = {"name": "codex-only-agent", "description": "用 Codex 執行", "engine": "codex"}
        resp = await client.post("/api/agents", json=payload)
        assert resp.status == 200
        resp2 = await client.get("/api/agents/codex-only-agent")
        body2 = await resp2.json()
        assert body2.get("engine") == "codex"

    async def test_create_agent_with_invalid_engine_400(self, client, tmp_claude_home):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        payload = {"name": "bad-engine-agent", "description": "d", "engine": "not-a-real-engine"}
        resp = await client.post("/api/agents", json=payload)
        assert resp.status == 400

    async def test_delete_agent(self, client, tmp_claude_home):
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        # 先建立一個要刪除的 agent
        (tmp_claude_home / "agents" / "to-delete.md").write_text(
            "---\nname: to-delete\ndescription: 要刪除\n---\n", encoding="utf-8"
        )
        resp = await client.delete("/api/agents/to-delete")
        assert resp.status == 200
        assert not (tmp_claude_home / "agents" / "to-delete.md").exists()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Skill CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillCRUD:
    """ROADMAP Phase 1 — P1-S1 ~ P1-S8"""

    async def test_list_skills_returns_list(self, client, sample_skill):
        resp = await client.get("/api/skills")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_get_single_skill(self, client, sample_skill):
        resp = await client.get("/api/skills/test-skill")
        assert resp.status == 200
        body = await resp.json()
        assert body.get("name") == "test-skill"

    async def test_get_nonexistent_skill_404(self, client):
        resp = await client.get("/api/skills/ghost-skill-xyz")
        assert resp.status == 404

    async def test_update_skill(self, client, sample_skill, tmp_claude_home):
        import main
        main.SKILLS_DIR = tmp_claude_home / "skills"
        resp = await client.put(
            "/api/skills/test-skill",
            json={"description": "技能說明更新"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    async def test_skill_has_output_memory_field(self, client, sample_skill):
        resp = await client.get("/api/skills/test-skill")
        body = await resp.json()
        assert "output_memory" in body


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Memory CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryCRUD:
    """ROADMAP Phase 1 — Memory 讀寫，及 Phase 3.C Memory Relay"""

    async def test_list_memory_returns_list(self, client, sample_memory):
        resp = await client.get("/api/memory")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_write_memory_key(self, client, tmp_claude_home):
        import main
        main.CLAUDE_HOME = tmp_claude_home

        resp = await client.put(
            "/api/memory/relay-test-key",
            json={"content": "# Relay Content\n\n這是 memory relay 測試內容"},
        )
        assert resp.status == 200
        # 確認檔案存在
        key_file = tmp_claude_home / "memory" / "relay-test-key.md"
        assert key_file.exists()
        assert "relay" in key_file.read_text(encoding="utf-8").lower()

    async def test_delete_memory_key(self, client, tmp_claude_home):
        import main
        main.CLAUDE_HOME = tmp_claude_home
        # 先建立
        mem_file = tmp_claude_home / "memory" / "del-test.md"
        mem_file.write_text("content", encoding="utf-8")
        resp = await client.delete("/api/memory/del-test")
        assert resp.status == 200
        assert not mem_file.exists()

    async def test_memory_relay_file_created_after_write(self, client, tmp_claude_home):
        """Phase 3.C：Memory 中繼驗證 — 寫入後可讀回相同內容"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        content = "Agent A 的輸出結果"
        await client.put("/api/memory/step1-result", json={"content": content})
        mem_file = tmp_claude_home / "memory" / "step1-result.md"
        assert mem_file.exists()
        assert content in mem_file.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Teams CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamsCRUD:
    """ROADMAP Phase 2 — P2-B1, P2-B2"""

    async def test_list_teams_returns_list(self, client, sample_team):
        resp = await client.get("/api/teams")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_get_single_team(self, client, sample_team):
        resp = await client.get("/api/teams/test-team")
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "test-team"
        assert isinstance(body["members"], list)

    async def test_get_nonexistent_team_404(self, client):
        resp = await client.get("/api/teams/no-such-team")
        assert resp.status == 404

    async def test_create_team(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        payload = {
            "name": "dyn-team",
            "description": "動態建立的團隊",
            "members": [{"agent": "test-agent", "role": "主要執行者"}],
        }
        resp = await client.post("/api/teams", json=payload)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert (tmp_claude_home / "teams" / "dyn-team.yaml").exists()

    async def test_create_team_duplicate_409(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        payload = {"name": "dyn-team", "description": "重複"}
        resp = await client.post("/api/teams", json=payload)
        assert resp.status == 409

    async def test_update_team(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.put(
            "/api/teams/test-team",
            json={"description": "更新後的團隊描述"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    async def test_update_team_favorite_round_trip(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"

        resp = await client.put("/api/teams/test-team", json={"favorite": True})
        assert resp.status == 200

        got = await client.get("/api/teams/test-team")
        assert got.status == 200
        body = await got.json()
        assert body["favorite"] is True

    async def test_delete_team(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        (tmp_claude_home / "teams" / "to-del-team.yaml").write_text(
            "name: to-del-team\ndescription: del\nmembers: []\n",
            encoding="utf-8",
        )
        resp = await client.delete("/api/teams/to-del-team")
        assert resp.status == 200
        assert not (tmp_claude_home / "teams" / "to-del-team.yaml").exists()

    async def test_team_members_structure(self, client, sample_team):
        resp = await client.get("/api/teams/test-team")
        body = await resp.json()
        for member in body["members"]:
            assert "agent" in member
            assert "role" in member


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Team Run（序列流水線）
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamRun:
    """ROADMAP Phase 3 — P3-B1 ~ P3-B5"""

    async def test_team_run_requires_task(self, client):
        resp = await client.post("/api/team/run", json={"team_id": "test-team"})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    async def test_team_run_nonexistent_team_404(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.post(
            "/api/team/run",
            json={"team_id": "ghost-team", "task": "some task"},
        )
        assert resp.status == 404

    async def test_team_run_with_inline_team_payload(self, client, tmp_claude_home):
        """使用 team payload 而非 team_id，驗證 run_id 生成"""
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        # 使用 inline team payload（無須 claude 實際執行，因為 agent 不存在）
        payload = {
            "task": "測試任務描述",
            "team": {
                "name": "inline-team",
                "members": [
                    {"agent": "fake-agent-x", "role": "測試角色"},
                ],
            },
        }
        resp = await client.post("/api/team/run", json=payload)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert "run_id" in body
        assert len(body["run_id"]) == 8  # hex[:8]

    async def test_team_run_get_status(self, client, tmp_claude_home):
        """建立 run 後立即查詢狀態"""
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        # 建立 run
        payload = {
            "task": "查詢狀態測試",
            "team": {
                "name": "status-team",
                "members": [{"agent": "fake-agent-y", "role": "狀態查詢"}],
            },
        }
        resp = await client.post("/api/team/run", json=payload)
        run_id = (await resp.json())["run_id"]

        # 查詢狀態
        resp2 = await client.get(f"/api/team/run/{run_id}")
        assert resp2.status == 200
        body2 = await resp2.json()
        assert body2["id"] == run_id
        assert "status" in body2
        assert "steps" in body2
        assert isinstance(body2["steps"], list)

    async def test_team_run_invalid_id_404(self, client):
        resp = await client.get("/api/team/run/nonexistent-run-id")
        assert resp.status == 404

    async def test_team_run_cancel(self, client, tmp_claude_home):
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        # 建立一個 run
        payload = {
            "task": "取消測試",
            "team": {
                "name": "cancel-team",
                "members": [{"agent": "fake-agent-z", "role": "取消"}],
            },
        }
        resp = await client.post("/api/team/run", json=payload)
        run_id = (await resp.json())["run_id"]
        # 取消
        resp2 = await client.delete(f"/api/team/run/{run_id}")
        assert resp2.status == 200


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — HR Agent（自動組隊）
# ══════════════════════════════════════════════════════════════════════════════

class TestHRAgent:
    """ROADMAP Phase 4 — P4-B1 ~ P4-B3"""

    async def test_agents_registry_returns_list(self, client, sample_agent, tmp_claude_home):
        """GET /api/agents/registry 應回傳帶有 description 與 skills 的列表"""
        import main
        main.AGENTS_DIR = tmp_claude_home / "agents"
        resp = await client.get("/api/agents/registry")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)
        # 找到我們的 test-agent
        agent_ids = [a["id"] for a in body]
        assert "test-agent" in agent_ids
        # 每筆 registry 項目必須有 name / description / skills
        for item in body:
            assert "id" in item
            assert "name" in item
            assert "description" in item
            assert "skills" in item
            assert isinstance(item["skills"], list)

    async def test_agents_registry_empty_when_no_agents(self, client, tmp_claude_home):
        """如果 agents 目錄下沒有 .md 檔，回傳空列表"""
        import database
        # 暫時指向空目錄（CRUD/registry 讀寫已改走 REGISTRY_AGENTS_DIR，見 ADR-003）
        empty_dir = tmp_claude_home / "agents_empty"
        empty_dir.mkdir(exist_ok=True)
        original = database.REGISTRY_AGENTS_DIR
        database.REGISTRY_AGENTS_DIR = empty_dir
        resp = await client.get("/api/agents/registry")
        body = await resp.json()
        assert body == []
        database.REGISTRY_AGENTS_DIR = original

    async def test_hr_dispatch_requires_task(self, client):
        """POST /api/hr/dispatch 不帶 task 應回傳 400"""
        resp = await client.post("/api/hr/dispatch", json={})
        assert resp.status == 400
        body = await resp.json()
        assert "error" in body

    async def test_hr_dispatch_no_agents_500(self, client, tmp_claude_home):
        """沒有任何 agent 時 HR dispatch 應回傳錯誤（不需要呼叫 Claude CLI）"""
        import database
        empty_dir = tmp_claude_home / "agents_empty"
        empty_dir.mkdir(exist_ok=True)
        original = database.REGISTRY_AGENTS_DIR
        database.REGISTRY_AGENTS_DIR = empty_dir
        resp = await client.post("/api/hr/dispatch", json={"task": "任意任務"})
        body = await resp.json()
        # 預期 500 + error 含有「尚未建立」
        assert resp.status == 500
        assert "error" in body
        assert "Agent" in body["error"] or "agent" in body["error"].lower()
        database.REGISTRY_AGENTS_DIR = original


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — Sessions & FTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSessions:
    """Session 列表 + FTS 全文搜尋"""

    async def test_list_sessions_ok(self, client):
        resp = await client.get("/api/sessions")
        assert resp.status == 200
        body = await resp.json()
        assert "sessions" in body
        assert "total" in body

    async def test_sessions_search_returns_valid_structure(self, client):
        resp = await client.get("/api/sessions?q=test")
        assert resp.status == 200
        body = await resp.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)

    async def test_sessions_pagination_offset(self, client):
        resp = await client.get("/api/sessions?offset=0")
        assert resp.status == 200

    async def test_delete_nonexistent_session(self, client):
        resp = await client.delete("/api/sessions/nonexistent-session-id-xyz")
        # 刪除不存在的 session 應不 crash（graceful）
        assert resp.status in (200, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Souls
# ══════════════════════════════════════════════════════════════════════════════

class TestSouls:
    """Soul 靈魂人格讀寫"""

    async def test_list_souls(self, client, tmp_claude_home):
        import main
        main.SOULS_DIR = tmp_claude_home / "souls"
        resp = await client.get("/api/souls")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_get_soul_content(self, client, tmp_claude_home):
        import main
        main.SOULS_DIR = tmp_claude_home / "souls"
        # 建立一個 soul 檔
        (tmp_claude_home / "souls" / "test-soul.md").write_text(
            "---\nname: test-soul\n---\n\n靈魂內容", encoding="utf-8"
        )
        resp = await client.get("/api/souls")
        body = await resp.json()
        # 至少有一個 soul
        assert len(body) >= 1

    async def test_save_soul(self, client, tmp_claude_home):
        import main
        main.SOULS_DIR = tmp_claude_home / "souls"
        resp = await client.put(
            "/api/souls/test-soul",
            json={"content": "# 更新的靈魂\n\n更新後的靈魂描述"},
        )
        assert resp.status == 200


# ══════════════════════════════════════════════════════════════════════════════
# Config / Schedules
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigAndSchedules:
    """設定檔與排程讀寫"""

    async def test_config_get_has_required_fields(self, client):
        resp = await client.get("/api/config")
        body = await resp.json()
        assert "_resolvedClaudeHome" in body

    async def test_config_put_projectDir(self, client, tmp_claude_home):
        import main
        main.CONFIG_FILE = tmp_claude_home / "claude-desktop-config.json"
        resp = await client.put("/api/config", json={"projectDir": "/tmp/test-project"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    # ── engineMode（2026-07-12 引擎範圍鎖定）─────────────────────────────
    async def test_config_get_defaults_engineMode_to_both(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        resp = await client.get("/api/config")
        body = await resp.json()
        assert body["engineMode"] == "both"

    async def test_config_put_engineMode_rejects_invalid_value(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        resp = await client.put("/api/config", json={"engineMode": "not-a-real-mode"})
        assert resp.status == 400

    async def test_config_put_engineMode_round_trip(self, client, tmp_path):
        import main
        main.CONFIG_FILE = tmp_path / "claude-desktop-config.json"
        put_resp = await client.put("/api/config", json={"engineMode": "claude"})
        assert put_resp.status == 200

        get_resp = await client.get("/api/config")
        body = await get_resp.json()
        assert body["engineMode"] == "claude"

        from database import get_engine_mode
        assert get_engine_mode() == "claude"

    async def test_schedules_get(self, client):
        resp = await client.get("/api/schedules")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_schedules_post(self, client):
        resp = await client.post(
            "/api/schedules",
            json={
                "name": "Test Schedule",
                "cron": "0 9 * * *",
                "prompt": "每日早上的測試提示",
                "enabled": False,
                "delivery": {"channel": "line", "to": "U123456"}
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert "id" in body
        assert body.get("delivery", {}).get("channel") == "line"
        assert body.get("delivery", {}).get("to") == "U123456"

    async def test_schedules_delete(self, client):
        # 先建立再刪除
        resp = await client.post(
            "/api/schedules",
            json={"name": "To Delete", "cron": "* * * * *", "prompt": "del", "enabled": False},
        )
        sid = (await resp.json())["id"]
        resp2 = await client.delete(f"/api/schedules/{sid}")
        assert resp2.status == 200


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Debug & Stats
# ══════════════════════════════════════════════════════════════════════════════

class TestDebugAndStats:
    """診斷 / 統計端點"""

    async def test_debug_dump_structure(self, client):
        """debug-dump 使用 Content-Disposition:attachment，用 text() 解析避免 content-type 問題"""
        import json as _json
        resp = await client.get("/api/debug-dump")
        assert resp.status == 200
        text = await resp.text()
        body = _json.loads(text)
        assert "timestamp" in body
        assert "platform" in body
        assert "sqlite" in body

    async def test_stats_endpoint(self, client):
        resp = await client.get("/api/stats")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, dict)
        # 核心統計欄位
        assert "sessions" in body
        assert "total_tokens" in body
        assert "heatmap" in body

    async def test_logs_endpoint(self, client):
        resp = await client.get("/api/logs")
        assert resp.status == 200
        body = await resp.json()
        assert "logs" in body
        assert isinstance(body["logs"], list)

    async def test_profiles_endpoint(self, client):
        resp = await client.get("/api/profiles")
        assert resp.status == 200
        body = await resp.json()
        assert "profiles" in body
        assert isinstance(body["profiles"], list)
        # 可能有 current 欄位
        assert "current" in body

    async def test_telegram_get_endpoint(self, client):
        resp = await client.get("/api/telegram")
        assert resp.status == 200
        body = await resp.json()
        assert "enabled" in body
        assert "token" in body
        assert "running" in body


# ══════════════════════════════════════════════════════════════════════════════
# ROADMAP 8.2 — 流水線完整性測試（無 Claude CLI）
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamPipelineIntegrity:
    """ROADMAP 8.2 — Team 流水線完整性（不依賴 Claude CLI）"""

    async def test_team_run_step_structure(self, client, tmp_claude_home):
        """建立後立即查詢，steps 結構應正確"""
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        payload = {
            "task": "流水線結構測試",
            "team": {
                "name": "pipeline-test-team",
                "members": [
                    {"agent": "agent-a", "role": "第一步"},
                    {"agent": "agent-b", "role": "第二步"},
                ],
            },
        }
        resp = await client.post("/api/team/run", json=payload)
        assert resp.status == 200
        run_id = (await resp.json())["run_id"]

        resp2 = await client.get(f"/api/team/run/{run_id}")
        body = await resp2.json()
        assert len(body["steps"]) == 2
        assert body["steps"][0]["agent"] == "agent-a"
        assert body["steps"][1]["agent"] == "agent-b"
        # 每個 step 必須有 status 欄位
        for step in body["steps"]:
            assert "status" in step
            assert "role" in step

    async def test_team_run_task_stored(self, client, tmp_claude_home):
        """task 描述應儲存在 run 狀態裡"""
        import main
        main.TEAMS_DIR = tmp_claude_home / "teams"
        task_text = "驗證任務儲存的測試描述 UNIQUE_MARKER"
        payload = {
            "task": task_text,
            "team": {
                "name": "task-storage-team",
                "members": [{"agent": "agent-c", "role": "儲存驗證"}],
            },
        }
        resp = await client.post("/api/team/run", json=payload)
        run_id = (await resp.json())["run_id"]
        resp2 = await client.get(f"/api/team/run/{run_id}")
        body = await resp2.json()
        assert body["task"] == task_text



# ══════════════════════════════════════════════════════════════════════════════
# Schedule PATCH（啟用/停用）
# ══════════════════════════════════════════════════════════════════════════════

class TestSchedulePatch:
    """PATCH /api/schedules/{id} — 啟用/停用排程"""

    async def test_toggle_schedule_to_disabled(self, client):
        """建立排程 → PATCH 停用 → 確認 enabled=False"""
        # 建立
        resp = await client.post(
            "/api/schedules",
            json={"name": "patch-test", "cron": "0 8 * * *", "prompt": "早安", "enabled": True},
        )
        assert resp.status == 200
        sid = (await resp.json())["id"]

        # PATCH 停用
        resp2 = await client.patch(f"/api/schedules/{sid}", json={"enabled": False})
        assert resp2.status == 200
        body2 = await resp2.json()
        assert body2["ok"] is True

        # 確認
        resp3 = await client.get("/api/schedules")
        schedules = await resp3.json()
        target = next((s for s in schedules if s["id"] == sid), None)
        assert target is not None
        assert target["enabled"] is False

        # 清理
        await client.delete(f"/api/schedules/{sid}")

    async def test_toggle_schedule_back_to_enabled(self, client):
        """建立停用排程 → PATCH 啟用"""
        resp = await client.post(
            "/api/schedules",
            json={"name": "re-enable-test", "cron": "0 9 * * *", "prompt": "test", "enabled": False},
        )
        sid = (await resp.json())["id"]

        resp2 = await client.patch(f"/api/schedules/{sid}", json={"enabled": True})
        assert resp2.status == 200

        resp3 = await client.get("/api/schedules")
        schedules = await resp3.json()
        target = next((s for s in schedules if s["id"] == sid), None)
        assert target is not None
        assert target["enabled"] is True

        await client.delete(f"/api/schedules/{sid}")

    async def test_schedules_run_nonexistent_404(self, client):
        """對不存在的 schedule ID 執行 run 應回傳 404"""
        resp = await client.post("/api/schedules/nonexistent-id-xyz/run")
        assert resp.status == 404


# ══════════════════════════════════════════════════════════════════════════════
# Soul 進階操作（rename / delete）
# ══════════════════════════════════════════════════════════════════════════════

class TestSoulAdvanced:
    """Soul rename / delete 端點測試"""

    async def test_soul_save_and_delete(self, client, tmp_claude_home):
        """建立 soul → 確認存在 → 刪除"""
        import main
        main.SOULS_DIR = tmp_claude_home / "souls"

        # 先寫入
        resp = await client.put(
            "/api/souls/temp-soul-del",
            json={"content": "---\nname: temp-soul-del\n---\n\n臨時靈魂"},
        )
        assert resp.status == 200

        # 確認存在
        assert (tmp_claude_home / "souls" / "temp-soul-del.md").exists()

        # 刪除
        resp2 = await client.delete("/api/souls/temp-soul-del")
        assert resp2.status == 200

        # 確認已刪除
        assert not (tmp_claude_home / "souls" / "temp-soul-del.md").exists()

    async def test_soul_rename(self, client, tmp_claude_home):
        """Soul rename 端點（POST /api/souls/{id}/rename）"""
        import main
        main.SOULS_DIR = tmp_claude_home / "souls"

        # 先建立 soul
        (tmp_claude_home / "souls" / "old-soul-name.md").write_text(
            "---\nname: old-soul-name\n---\n\n舊靈魂", encoding="utf-8"
        )

        resp = await client.post(
            "/api/souls/old-soul-name/rename",
            json={"new_name": "new-soul-name"},
        )
        # rename 成功或 Soul 不支援 rename 都是可接受的（確認不 crash）
        assert resp.status in (200, 404, 405)


# ══════════════════════════════════════════════════════════════════════════════
# Session 進階操作（rename / messages）
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionAdvanced:
    """Session rename、messages 查詢端點"""

    async def test_session_messages_nonexistent(self, client):
        """不存在 session 的 messages 查詢應優雅回應（不 crash）"""
        resp = await client.get("/api/sessions/nonexistent-id-xyz/messages")
        # 404 或空 list 都可接受
        assert resp.status in (200, 404)

    async def test_session_rename_nonexistent_graceful(self, client):
        """對不存在的 session 做 rename 應優雅回應（不 crash 成 500）"""
        resp = await client.post(
            "/api/sessions/ghost-session-id/rename",
            json={"title": "新標題"},
        )
        assert resp.status in (200, 404)

    async def test_session_auto_title_nonexistent(self, client):
        """對不存在的 session 做 auto-title 應優雅回應"""
        resp = await client.post("/api/sessions/ghost-session-id/auto-title")
        assert resp.status in (200, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Memory 新架構（memview API）
# ══════════════════════════════════════════════════════════════════════════════

class TestMemviewAPI:
    """新架構記憶體 API（/api/mem/user, system, agents, teams）
    注意：路由前綴是 /api/mem/ 而非 /api/memory/
    """

    async def test_user_memory_get(self, client, tmp_claude_home):
        """GET /api/mem/user 應回傳內容（可能是空字串）"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        resp = await client.get("/api/mem/user")
        assert resp.status == 200
        body = await resp.json()
        assert "content" in body

    async def test_user_memory_put(self, client, tmp_claude_home):
        """PUT /api/mem/user 應可以寫入並讀回"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        content = "# 用戶記憶\n\n測試內容 UNIQUE_MARKER"

        resp = await client.put("/api/mem/user", json={"content": content})
        assert resp.status == 200

        resp2 = await client.get("/api/mem/user")
        body2 = await resp2.json()
        assert content in body2.get("content", "")

    async def test_system_memory_get(self, client, tmp_claude_home):
        """GET /api/mem/system 應回傳內容"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        resp = await client.get("/api/mem/system")
        assert resp.status == 200
        body = await resp.json()
        assert "content" in body

    async def test_agents_memory_list(self, client, tmp_claude_home, sample_agent):
        """GET /api/mem/agents 應回傳 list（後端直接回傳陣列）"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.AGENTS_DIR = tmp_claude_home / "agents"
        resp = await client.get("/api/mem/agents")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_memory_overview(self, client, tmp_claude_home):
        """GET /api/mem/overview 應回傳結構化摘要"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        resp = await client.get("/api/mem/overview")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, dict)

    async def test_teams_memory_list(self, client, tmp_claude_home, sample_team):
        """GET /api/mem/teams 應回傳 list（後端直接回傳陣列）"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.get("/api/mem/teams")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, list)

    async def test_agent_memory_get(self, client, tmp_claude_home, sample_agent):
        """GET /api/mem/agents/{id} 應回傳該 agent 的記憶內容"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.AGENTS_DIR = tmp_claude_home / "agents"
        resp = await client.get("/api/mem/agents/test-agent")
        assert resp.status == 200
        body = await resp.json()
        assert "content" in body

    async def test_agent_memory_put(self, client, tmp_claude_home, sample_agent):
        """PUT /api/mem/agents/{id} 應可以寫入"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        resp = await client.put(
            "/api/mem/agents/test-agent",
            json={"content": "# Agent 記憶\n\n測試寫入 UNIQUE_MARKER"},
        )
        assert resp.status == 200

    async def test_team_memory_get(self, client, tmp_claude_home, sample_team):
        """GET /api/mem/teams/{id} 應回傳該 team 的共享記憶"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.TEAMS_DIR = tmp_claude_home / "teams"
        resp = await client.get("/api/mem/teams/test-team")
        assert resp.status == 200
        body = await resp.json()
        assert "content" in body

    async def test_mem_preview(self, client, tmp_claude_home):
        """GET /api/mem/preview 應回傳預覽摘要"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        resp = await client.get("/api/mem/preview")
        assert resp.status == 200
        body = await resp.json()
        assert isinstance(body, dict)

    async def test_team_chat_endpoint(self, client, tmp_claude_home, sample_team, sample_agent):
        """POST /api/team/chat 應能正常呼叫並回傳串流資料"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.TEAMS_DIR = tmp_claude_home / "teams"
        main.AGENTS_DIR = tmp_claude_home / "agents"
        
        payload = {
            "message": "哈囉團隊，請幫我分析專案",
            "team_id": "test-team",
            "client_id": "test-client-id",
            "cwd": str(tmp_claude_home)
        }
        resp = await client.post("/api/team/chat", json=payload)
        assert resp.status == 200
        body = await resp.text()
        assert "data:" in body
        # 2026-07-10 修復：handle_team_chat 把所有例外都包成一個「正常的」
        # SSE data: 事件回傳，光靠 "data:" in body 連 NameError 都測不出來
        # （曾經整條 team chat 因為 all_members_list 未定義而 100% 炸掉，
        # 這個測試卻一直顯示綠燈）。補上明確斷言排除錯誤事件。
        assert '"type": "error"' not in body
        assert "NameError" not in body

    async def test_team_execute_endpoint(self, client, tmp_claude_home, sample_team):
        """POST /api/team/execute 對無效路徑應報錯"""
        import main
        main.CLAUDE_HOME = tmp_claude_home
        main.TEAMS_DIR = tmp_claude_home / "teams"
        
        payload = {
            "team_id": "test-team",
            "project_path": "invalid_path_123",
            "task": "測試實作"
        }
        resp = await client.post("/api/team/execute", json=payload)
        assert resp.status == 200
        body = await resp.text()
        assert "invalid project path" in body

    async def test_team_authorize_endpoint(self, client):
        """POST /api/team/authorize 對無效的 request_id 應回傳 404"""
        payload = {
            "request_id": "nonexistent_req",
            "decision": "approve"
        }
        resp = await client.post("/api/team/authorize", json=payload)
        assert resp.status == 404

