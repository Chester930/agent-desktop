import json
from datetime import datetime, timezone

import main


def _write_claude_session(root, session_id="claude-sess-1"):
    project = root / "projects" / "D--proj"
    project.mkdir(parents=True, exist_ok=True)
    f = project / f"{session_id}.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({
                "type": "user",
                "cwd": "D:\\proj",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": {"content": [{"type": "text", "text": "hello from claude"}]},
            }),
            json.dumps({
                "type": "assistant",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": {"content": [{"type": "text", "text": "claude answer"}]},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    return f


def _write_codex_session(root, session_id="019fabcd-0000-7000-8000-000000000001"):
    codex = root / ".codex"
    session_dir = codex / "sessions" / "2026" / "07" / "29"
    session_dir.mkdir(parents=True, exist_ok=True)
    f = session_dir / f"rollout-2026-07-29T12-00-00-{session_id}.jsonl"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    f.write_text(
        "\n".join([
            json.dumps({
                "timestamp": now,
                "type": "session_meta",
                "payload": {"session_id": session_id, "cwd": "D:\\codex-proj", "timestamp": now},
            }),
            json.dumps({
                "timestamp": now,
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello from codex"}]},
            }),
            json.dumps({
                "timestamp": now,
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "codex answer"}]},
            }),
            json.dumps({
                "timestamp": now,
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello from codex"},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    (codex / "session_index.jsonl").write_text(
        json.dumps({"id": session_id, "thread_name": "Codex indexed title", "updated_at": now}) + "\n",
        encoding="utf-8",
    )
    return f, session_id


async def test_sessions_default_is_claude_and_codex_filter_reads_codex(client, tmp_claude_home, tmp_path, monkeypatch):
    _write_claude_session(tmp_claude_home)
    _codex_file, codex_session_id = _write_codex_session(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    default_resp = await client.get("/api/sessions")
    assert default_resp.status == 200
    default_body = await default_resp.json()
    assert any(s["id"] == "claude-sess-1" and s["engine"] == "claude" for s in default_body["items"])
    assert all(s["engine"] == "claude" for s in default_body["items"])

    codex_resp = await client.get("/api/sessions?engine=codex")
    assert codex_resp.status == 200
    codex_body = await codex_resp.json()
    codex_items = [s for s in codex_body["items"] if s["id"] == codex_session_id]
    assert codex_items
    assert codex_items[0]["engine"] == "codex"
    assert codex_items[0]["title"] == "Codex indexed title"
    assert codex_items[0]["projectPath"] == "D:\\codex-proj"


async def test_codex_session_messages_use_codex_parser(client, tmp_path, monkeypatch):
    session_id = "019fabcd-0000-7000-8000-000000000002"
    _write_codex_session(tmp_path, session_id=session_id)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

    list_resp = await client.get("/api/sessions?engine=codex")
    assert list_resp.status == 200

    resp = await client.get(f"/api/sessions/{session_id}/messages")
    assert resp.status == 200
    body = await resp.json()
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("user", "hello from codex"),
        ("assistant", "codex answer"),
    ]


async def test_resume_codex_session_persists_engine_metadata(client, tmp_path, monkeypatch):
    session_id = "019fabcd-0000-7000-8000-000000000003"
    _write_codex_session(tmp_path, session_id=session_id)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    await client.get("/api/sessions?engine=codex")

    client_id = "resume-codex-session-test"
    main.active_sessions.pop(client_id, None)
    resp = await client.post("/api/sessions/resume", json={
        "client_id": client_id,
        "session_id": session_id,
        "engine": "codex",
    })

    assert resp.status == 200
    body = await resp.json()
    assert body["engine"] == "codex"
    assert main.active_sessions[client_id] == {"id": session_id, "engine": "codex"}


async def test_codex_session_auto_title_uses_codex_messages_and_engine(client, tmp_path, monkeypatch):
    session_id = "019fabcd-0000-7000-8000-000000000004"
    _write_codex_session(tmp_path, session_id=session_id)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    await client.get("/api/sessions?engine=codex")

    calls = []

    async def fake_automation(prompt: str, *, preferred_engine: str = "", timeout: float = 120.0) -> str:
        calls.append({"prompt": prompt, "preferred_engine": preferred_engine, "timeout": timeout})
        return '"Codex 標題"\nignored'

    monkeypatch.setattr(main, "_run_automation_prompt", fake_automation)
    resp = await client.post(f"/api/sessions/{session_id}/auto-title")

    assert resp.status == 200
    body = await resp.json()
    assert body["title"] == "Codex 標題"
    assert calls[0]["preferred_engine"] == "codex"
    assert "hello from codex" in calls[0]["prompt"]
    assert "codex answer" in calls[0]["prompt"]


async def test_skill_generate_uses_codex_messages_and_registry_dir(client, tmp_path, monkeypatch):
    session_id = "019fabcd-0000-7000-8000-000000000005"
    _write_codex_session(tmp_path, session_id=session_id)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    await client.get("/api/sessions?engine=codex")

    skills_dir = tmp_path / "registry" / "skills"
    monkeypatch.setattr(main._db_mod, "REGISTRY_SKILLS_DIR", skills_dir)

    calls = []

    async def fake_automation(prompt: str, *, preferred_engine: str = "", timeout: float = 120.0) -> str:
        calls.append({"prompt": prompt, "preferred_engine": preferred_engine, "timeout": timeout})
        return "---\nname: codex-skill\ndescription: test\n---\n\n## When to Use\nUse it.\n"

    monkeypatch.setattr(main, "_run_automation_prompt", fake_automation)
    resp = await client.post("/api/skills/generate", json={"session_id": session_id})

    assert resp.status == 200
    body = await resp.json()
    out_path = skills_dir / f"auto-{session_id[:8]}.md"
    assert body["path"] == str(out_path)
    assert out_path.exists()
    assert calls[0]["preferred_engine"] == "codex"
    assert "hello from codex" in calls[0]["prompt"]


async def test_sessions_reject_invalid_engine(client):
    resp = await client.get("/api/sessions?engine=not-real")
    assert resp.status == 400
