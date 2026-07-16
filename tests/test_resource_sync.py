from pathlib import Path
import shutil

from resource_sync import ResourceSyncService


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_status_classifies_missing_and_conflicting_resources(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(
        claude / "agents" / "planner.md",
        "---\nname: planner\ndescription: Plans work\n---\n\nPlan carefully.\n",
    )
    _write(codex / "agents" / "reviewer.toml", 'name = "reviewer"\n')
    _write(claude / "skills" / "tdd" / "SKILL.md", "# TDD\n")
    _write(shared / "tdd" / "SKILL.md", "# Different TDD\n")

    status = ResourceSyncService(claude, codex, shared).status()

    assert status["agents"]["missing_in_codex"] == ["planner"]
    assert status["agents"]["codex_only"] == ["reviewer"]
    assert status["skills"]["conflicts"] == ["tdd"]


def test_sync_creates_codex_agent_and_skill_without_overwriting_conflicts(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(
        claude / "agents" / "planner.md",
        "---\nname: planner\ndescription: Plans work\nmodel: opus\n---\n\nPlan carefully.\n",
    )
    _write(claude / "skills" / "tdd.md", "---\nname: tdd\n---\n\nTest first.\n")
    _write(shared / "keep" / "SKILL.md", "user-owned\n")
    _write(claude / "skills" / "keep" / "SKILL.md", "claude-owned\n")

    result = ResourceSyncService(claude, codex, shared).sync()

    agent = (codex / "agents" / "planner.toml").read_text(encoding="utf-8")
    skill = (shared / "tdd" / "SKILL.md").read_text(encoding="utf-8")
    assert 'name = "planner"' in agent
    assert 'description = "Plans work"' in agent
    assert 'developer_instructions = "' in agent
    assert "Plan carefully." in agent
    assert skill.endswith("Test first.\n")
    assert (shared / "keep" / "SKILL.md").read_text(encoding="utf-8") == "user-owned\n"
    assert result["agents"]["created"] == ["planner"]
    assert result["skills"]["created"] == ["tdd"]
    assert result["skills"]["conflicts"] == ["keep"]


def test_status_matches_legacy_md_skill_targets_by_stem(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(claude / "skills" / "translator.md", "---\nname: translator\n---\n\nTranslate text.\n")
    _write(shared / "translator.md", "---\nname: translator\n---\n\nTranslate text.\n")

    status = ResourceSyncService(claude, codex, shared).status()

    assert status["skills"]["synced"] == ["translator"]
    assert status["skills"]["codex_only"] == []
    assert status["skills"]["conflicts"] == []


def test_sync_updates_only_managed_codex_agent(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    source = claude / "agents" / "planner.md"
    target = codex / "agents" / "planner.toml"

    _write(source, "---\nname: planner\ndescription: v1\n---\n\nFirst body.\n")
    service = ResourceSyncService(claude, codex, shared)
    service.sync()
    _write(source, "---\nname: planner\ndescription: v2\n---\n\nSecond body.\n")

    result = service.sync()

    assert 'description = "v2"' in target.read_text(encoding="utf-8")
    assert "Second body." in target.read_text(encoding="utf-8")
    assert result["agents"]["updated"] == ["planner"]


def test_sync_removes_only_managed_orphans_from_all_engine_targets(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / "real-claude"

    _write(registry / "agents" / "planner.md", "---\nname: planner\n---\n\nBody\n")
    _write(registry / "skills" / "tdd" / "SKILL.md", "# TDD\n")
    service = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native)
    service.sync()

    (registry / "agents" / "planner.md").unlink()
    shutil.rmtree(registry / "skills" / "tdd")
    _write(codex / "agents" / "mine.toml", 'name = "mine"\n')
    _write(shared / "mine" / "SKILL.md", "user-owned\n")

    dry_run = service.sync(dry_run=True)
    assert dry_run["agents"]["deleted"] == ["planner"]
    assert dry_run["skills"]["deleted"] == ["tdd"]
    assert dry_run["claude_mirror"]["agents"]["deleted"] == ["planner"]
    assert dry_run["claude_mirror"]["skills"]["deleted"] == ["tdd"]
    assert (codex / "agents" / "planner.toml").exists()

    result = service.sync()
    assert result["agents"]["deleted"] == ["planner"]
    assert not (codex / "agents" / "planner.toml").exists()
    assert not (shared / "tdd").exists()
    assert not (claude_native / "agents" / "planner.md").exists()
    assert not (claude_native / "skills" / "tdd").exists()
    assert (codex / "agents" / "mine.toml").exists()
    assert (shared / "mine" / "SKILL.md").exists()


def test_sync_detects_changes_in_skill_supporting_files(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    source = registry / "skills" / "tdd"

    _write(source / "SKILL.md", "# TDD\n")
    _write(source / "references" / "guide.md", "version one\n")
    service = ResourceSyncService(registry, codex, shared)
    service.sync()
    _write(source / "references" / "guide.md", "version two\n")

    # UI status intentionally compares the entry document only so hundreds of
    # asset trees do not make the endpoint take minutes. Deployment below still
    # hashes the complete payload and must detect this supporting-file change.
    assert service.status()["skills"]["synced"] == ["tdd"]
    result = service.sync()
    assert result["skills"]["updated"] == ["tdd"]
    assert (shared / "tdd" / "references" / "guide.md").read_text(encoding="utf-8") == "version two\n"


def test_dry_run_makes_no_files(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    _write(claude / "agents" / "planner.md", "---\nname: planner\n---\n\nBody\n")

    result = ResourceSyncService(claude, codex, shared).sync(dry_run=True)

    assert result["agents"]["created"] == ["planner"]
    assert not (codex / "agents" / "planner.toml").exists()


def test_invalid_codex_skill_path_is_a_conflict_in_status_and_sync(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    _write(claude / "skills" / "broken.md", "skill body\n")
    _write(shared / "broken", "not a skill directory\n")

    service = ResourceSyncService(claude, codex, shared)

    assert service.status()["skills"]["conflicts"] == ["broken"]
    assert service.sync(dry_run=True)["skills"]["conflicts"] == ["broken"]


def test_claude_native_home_equal_to_registry_disables_mirror(tmp_path):
    registry = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    service = ResourceSyncService(registry, codex, shared, claude_native_home=registry)

    assert service.claude_native_home is None
    assert "claude_mirror" not in service.status()
    assert "claude_mirror" not in service.sync()


def test_claude_mirror_renders_agent_and_skill_when_registry_is_decoupled(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / "real-claude"

    _write(
        registry / "agents" / "planner.md",
        "---\nname: planner\ndescription: Plans work\ntools: Read, Grep\n---\n\nPlan carefully.\n",
    )
    _write(registry / "skills" / "tdd" / "SKILL.md", "# TDD\nWrite tests first.\n")

    service = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native)
    assert service.claude_native_home == claude_native

    result = service.sync()
    assert result["claude_mirror"]["agents"]["created"] == ["planner"]
    assert result["claude_mirror"]["skills"]["created"] == ["tdd"]

    mirrored = (claude_native / "agents" / "planner.md").read_text(encoding="utf-8")
    # The marker must live *inside* the frontmatter, not before it — a leading
    # comment before "---" would break any real Markdown-frontmatter parser
    # (including Claude Code's own), silently making the mirrored agent
    # invisible even though the file exists on disk.
    assert mirrored.startswith("---\n")
    from resource_sync import _frontmatter_and_body
    meta, body = _frontmatter_and_body(claude_native / "agents" / "planner.md")
    assert meta["name"] == "planner"
    assert meta["tools"] == "Read, Grep"
    assert body == "Plan carefully."

    status = service.status()
    assert status["claude_mirror"]["agents"]["synced"] == ["planner"]
    assert status["claude_mirror"]["skills"]["synced"] == ["tdd"]

    # Re-syncing is a no-op once mirrored content matches the registry.
    assert service.sync()["claude_mirror"]["agents"]["created"] == []


def test_claude_mirror_status_accepts_matching_single_file_skill(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / "real-claude"

    _write(registry / "skills" / "preset.md", "# Preset\n")
    _write(claude_native / "skills" / "preset.md", "# Preset\n")

    service = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native)

    status = service.status()
    assert status["claude_mirror"]["skills"]["synced"] == ["preset"]
    assert status["claude_mirror"]["skills"]["conflicts"] == []
    assert service.sync(dry_run=True)["claude_mirror"]["skills"]["conflicts"] == []


def test_claude_mirror_never_overwrites_unmanaged_native_agent(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / "real-claude"

    _write(registry / "agents" / "planner.md", "---\nname: planner\n---\n\nBody\n")
    _write(claude_native / "agents" / "planner.md", "---\nname: planner\n---\n\nUser-owned body\n")

    service = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native)
    result = service.sync()

    assert result["claude_mirror"]["agents"]["conflicts"] == ["planner"]
    assert (claude_native / "agents" / "planner.md").read_text(encoding="utf-8") == "---\nname: planner\n---\n\nUser-owned body\n"


def test_import_native_adopts_codex_only_agent_and_skill_into_registry(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(
        codex / "agents" / "reviewer.toml",
        'name = "reviewer"\ndescription = "Reviews code"\ndeveloper_instructions = "Review thoroughly."\n',
    )
    _write(shared / "foreign" / "SKILL.md", "# Foreign skill\n")

    service = ResourceSyncService(registry, codex, shared)
    result = service.import_native()

    assert result["agents"]["imported"] == ["reviewer"]
    assert result["skills"]["imported"] == ["foreign"]

    from resource_sync import _frontmatter_and_body
    meta, body = _frontmatter_and_body(registry / "agents" / "reviewer.md")
    assert meta["name"] == "reviewer"
    assert meta["description"] == "Reviews code"
    assert body == "Review thoroughly."
    assert (registry / "skills" / "foreign" / "SKILL.md").read_text(encoding="utf-8") == "# Foreign skill\n"


def test_import_native_adopts_codex_only_md_skill_by_stem(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(shared / "translator.md", "# Translator\n")

    service = ResourceSyncService(registry, codex, shared)
    status = service.status()
    result = service.import_native()

    assert status["skills"]["codex_only"] == ["translator"]
    assert result["skills"]["imported"] == ["translator"]
    assert (registry / "skills" / "translator" / "SKILL.md").read_text(encoding="utf-8") == "# Translator\n"


def test_import_native_skips_orphaned_managed_copies_and_existing_registry_names(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    # An orphaned copy we generated ourselves (source since deleted from the
    # registry) must NOT be resurrected — it doesn't represent independent
    # user intent.
    _write(registry / "agents" / "planner.md", "---\nname: planner\n---\n\nBody\n")
    service = ResourceSyncService(registry, codex, shared)
    service.sync()
    (registry / "agents" / "planner.md").unlink()

    # A Codex-native agent whose name already exists in the registry is left
    # completely alone — the registry copy wins, no overwrite, no import.
    _write(registry / "agents" / "existing.md", "---\nname: existing\n---\n\nMine.\n")
    _write(codex / "agents" / "existing.toml", 'name = "existing"\ndeveloper_instructions = "Theirs."\n')

    result = service.import_native()

    assert "planner" not in result["agents"]["imported"]
    assert "existing" not in result["agents"]["imported"]
    assert (registry / "agents" / "existing.md").read_text(encoding="utf-8") == "---\nname: existing\n---\n\nMine.\n"


def test_import_native_reports_skipped_when_destination_already_occupied(tmp_path):
    """Edge case: a directory that doesn't qualify as a registry skill (no
    SKILL.md/README.md, so _skill_sources() ignores it) but whose name is
    already occupied at the import destination — import must not clobber it,
    and reports it as skipped rather than silently dropping it."""
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    _write(registry / "skills" / "mystery" / "notes.txt", "not a valid skill entry\n")
    _write(codex / "agents" / ".keep", "")  # ensure codex_home exists, unrelated to this case
    _write(shared / "mystery" / "SKILL.md", "# Mystery\nForeign skill content.\n")

    service = ResourceSyncService(registry, codex, shared)
    result = service.import_native()

    assert result["skills"]["skipped"] == ["mystery"]
    assert not (registry / "skills" / "mystery" / "SKILL.md").exists()


def test_import_native_does_not_choose_between_different_engine_versions(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / ".claude"
    _write(codex / "agents" / "reviewer.toml", 'name = "reviewer"\ndeveloper_instructions = "Codex body"\n')
    _write(claude_native / "agents" / "reviewer.md", "---\nname: reviewer\n---\n\nClaude body\n")
    _write(shared / "tdd" / "SKILL.md", "Codex skill\n")
    _write(claude_native / "skills" / "tdd" / "SKILL.md", "Claude skill\n")

    result = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native).import_native()

    assert result["agents"]["conflicts"] == ["reviewer"]
    assert result["skills"]["conflicts"] == ["tdd"]
    assert not (registry / "agents" / "reviewer.md").exists()
    assert not (registry / "skills" / "tdd").exists()


def test_reconcile_adopts_native_only_then_renders_other_engine(tmp_path):
    registry = tmp_path / "registry"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    claude_native = tmp_path / ".claude"
    _write(shared / "native-skill" / "SKILL.md", "Native content\n")

    result = ResourceSyncService(registry, codex, shared, claude_native_home=claude_native).reconcile()

    assert result["adopted"]["skills"]["imported"] == ["native-skill"]
    assert (registry / "skills" / "native-skill" / "SKILL.md").exists()
    assert (claude_native / "skills" / "native-skill" / "SKILL.md").exists()


def test_equivalent_unmanaged_codex_agent_is_already_synced(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"
    _write(claude / "agents" / "planner.md", "---\nname: planner\ndescription: Plans\n---\n\nBody\n")
    _write(
        codex / "agents" / "planner.toml",
        'name = "planner"\ndescription = "Plans"\ndeveloper_instructions = "Body"\n',
    )

    service = ResourceSyncService(claude, codex, shared)

    assert service.status()["agents"]["synced"] == ["planner"]
    assert service.sync(dry_run=True)["agents"]["conflicts"] == []


def test_sync_with_utf8_bom_agent(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    # 寫入帶有 BOM (\ufeff) 的 Markdown 檔案
    bom_content = "\ufeff---\nname: bom-agent\ndescription: Has BOM\n---\n\nBody with BOM\n"
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    (claude / "agents" / "bom-agent.md").write_text(bom_content, encoding="utf-8")

    service = ResourceSyncService(claude, codex, shared)
    result = service.sync()

    assert result["agents"]["created"] == ["bom-agent"]
    target_toml = codex / "agents" / "bom-agent.toml"
    assert target_toml.exists()

    toml_content = target_toml.read_text(encoding="utf-8")
    assert 'description = "Has BOM"' in toml_content
    assert 'developer_instructions = "Body with BOM"' in toml_content


def test_sync_with_blank_description_agent(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    # 寫入一個 description 為空的 Agent Markdown 檔案
    content = "---\nname: blank-desc\ndescription: ''\n---\n\nBody\n"
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    (claude / "agents" / "blank-desc.md").write_text(content, encoding="utf-8")

    service = ResourceSyncService(claude, codex, shared)
    result = service.sync()

    assert result["agents"]["created"] == ["blank-desc"]
    target_toml = codex / "agents" / "blank-desc.toml"
    assert target_toml.exists()

    toml_content = target_toml.read_text(encoding="utf-8")
    # description 應該 fallback 為 name 的值 ("blank-desc")
    assert 'description = "blank-desc"' in toml_content


def test_sync_with_blank_body_agent(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    # 寫入一個 body 為空的 Agent Markdown 檔案
    content = "---\nname: blank-body\ndescription: Has description\n---\n"
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    (claude / "agents" / "blank-body.md").write_text(content, encoding="utf-8")

    service = ResourceSyncService(claude, codex, shared)
    result = service.sync()

    assert result["agents"]["created"] == ["blank-body"]
    target_toml = codex / "agents" / "blank-body.toml"
    assert target_toml.exists()

    toml_content = target_toml.read_text(encoding="utf-8")
    # developer_instructions 應該 fallback 為 "You are {name}." ("You are blank-body.")
    assert 'developer_instructions = "You are blank-body."' in toml_content


def test_agent_frontmatter_supports_yaml_folded_description(tmp_path):
    claude = tmp_path / ".claude"
    codex = tmp_path / ".codex"
    shared = tmp_path / ".agents" / "skills"

    content = "---\nname: planner\ndescription: >\n  Plans work\n  carefully\n---\n\nBody\n"
    (claude / "agents").mkdir(parents=True, exist_ok=True)
    (claude / "agents" / "planner.md").write_text(content, encoding="utf-8")

    service = ResourceSyncService(claude, codex, shared)
    result = service.sync()

    assert result["agents"]["created"] == ["planner"]
    toml_content = (codex / "agents" / "planner.toml").read_text(encoding="utf-8")
    assert 'description = "Plans work carefully"' in toml_content
