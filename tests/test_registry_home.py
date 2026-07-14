import json

import database


def test_registry_home_defaults_to_claude_home_when_unconfigured(tmp_path, monkeypatch):
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    claude_home = tmp_path / ".claude"
    assert database._resolve_registry_home(claude_home) == claude_home


def test_registry_home_honors_explicit_override(tmp_path, monkeypatch):
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(json.dumps({"registryHome": str(registry_dir)}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    claude_home = tmp_path / ".claude"
    assert database._resolve_registry_home(claude_home) == registry_dir


def test_registry_home_falls_back_when_override_dir_does_not_exist(tmp_path, monkeypatch):
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(
        json.dumps({"registryHome": str(tmp_path / "does-not-exist")}), encoding="utf-8"
    )
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    claude_home = tmp_path / ".claude"
    assert database._resolve_registry_home(claude_home) == claude_home


def test_registry_home_env_var_takes_precedence_over_config(tmp_path, monkeypatch):
    """Container deployments (docker-compose) have no desktop settings UI to
    write registryHome into the JSON config — REGISTRY_HOME must work as a
    plain env var override, mirroring how CODEX_RESOURCE_HOME already works
    in routes/resource_sync.py::_service()."""
    env_dir = tmp_path / "env-registry"
    env_dir.mkdir()
    config_dir = tmp_path / "config-registry"
    config_dir.mkdir()
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(json.dumps({"registryHome": str(config_dir)}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)
    monkeypatch.setenv("REGISTRY_HOME", str(env_dir))

    claude_home = tmp_path / ".claude"
    assert database._resolve_registry_home(claude_home) == env_dir


def test_registry_home_ignores_env_var_pointing_to_missing_dir(tmp_path, monkeypatch):
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)
    monkeypatch.setenv("REGISTRY_HOME", str(tmp_path / "does-not-exist"))

    claude_home = tmp_path / ".claude"
    assert database._resolve_registry_home(claude_home) == claude_home


def test_update_paths_recomputes_registry_dirs(tmp_path, monkeypatch):
    config_file = tmp_path / "claude-desktop-config.json"
    config_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    new_home = tmp_path / "new-claude-home"
    database.update_paths(new_home)

    assert database.REGISTRY_HOME == new_home
    assert database.REGISTRY_AGENTS_DIR == new_home / "agents"
    assert database.REGISTRY_SKILLS_DIR == new_home / "skills"
