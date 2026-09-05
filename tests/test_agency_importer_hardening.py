"""T33: agency_agents_importer.py 的 div_key 來自釘死版本的上游 GitHub repo
（divisions.json），會被拿去拼 team_id/team_file 檔名。防禦深度：即使上游
repo 或這支 script 之後被改成信任程度較低的來源，一個惡意的 key（例如
"../../../etc"）也不該造成路徑穿越寫入。"""
import agency_agents_importer as importer
import yaml


def test_is_safe_id_rejects_path_traversal():
    assert importer._is_safe_id("../../../etc") is False
    assert importer._is_safe_id("a/b") is False
    assert importer._is_safe_id("a\\b") is False
    assert importer._is_safe_id("") is False


def test_is_safe_id_allows_normal_division_key():
    assert importer._is_safe_id("engineering") is True
    assert importer._is_safe_id("marketing-ops") is True


def test_import_preserves_upstream_metadata_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "get_claude_home", lambda: tmp_path)

    def fake_json(url):
        if "divisions.json" in url:
            return {"divisions": {"engineering": {"label": "Engineering"}}}
        return {
            "sha": "tree-sha-123",
            "tree": [{"path": "engineering/lead-engineer.md"}],
        }

    monkeypatch.setattr(importer, "fetch_json", fake_json)
    monkeypatch.setattr(
        importer,
        "fetch_text",
        lambda url: (
            "---\n"
            "name: Lead Engineer\n"
            "description: Builds reliable systems\n"
            "tools: [Read, Bash]\n"
            "skills: [testing]\n"
            "engine: codex\n"
            "---\n\n"
            "Use evidence and tests."
        ),
    )

    result = importer.run_import()
    metadata = yaml.safe_load((tmp_path / "agents" / "lead-engineer.md").read_text().split("---")[1])

    assert result["agents_count"] == 1
    assert metadata["tools"] == ["Read", "Bash"]
    assert metadata["skills"] == ["testing"]
    assert metadata["engine"] == "codex"
    assert metadata["source"]["revision"] == "tree-sha-123"
    assert metadata["source"]["license"] == "MIT"
    assert "Use evidence and tests." in (tmp_path / "souls" / "lead-engineer.md").read_text()


def test_import_does_not_overwrite_unmanaged_local_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "get_claude_home", lambda: tmp_path)
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "lead-engineer.md").write_text(
        "---\nname: Local Agent\ndescription: Keep me\n---\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        importer,
        "fetch_json",
        lambda url: (
            {"divisions": {"engineering": {"label": "Engineering"}}}
            if "divisions.json" in url
            else {"sha": "tree-sha", "tree": [{"path": "engineering/lead-engineer.md"}]}
        ),
    )
    monkeypatch.setattr(importer, "fetch_text", lambda url: "---\nname: Upstream\n---\nbody")

    result = importer.run_import()

    assert result["agents_count"] == 0
    assert result["skipped_agents_count"] == 1
    assert "Keep me" in (tmp_path / "agents" / "lead-engineer.md").read_text()
    assert not (tmp_path / "teams" / "engineering-team.yaml").exists()
