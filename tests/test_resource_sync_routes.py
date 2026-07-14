from pathlib import Path

import pytest

from routes import resource_sync as routes



class FakeService:
    def status(self):
        return {"agents": {"missing_in_codex": ["planner"]}, "skills": {}}

    def sync(self, dry_run=False):
        return {"agents": {"created": ["planner"]}, "skills": {}, "was_dry": dry_run}

    def import_native(self, dry_run=False):
        return {"agents": {"imported": ["reviewer"]}, "skills": {}, "was_dry": dry_run}


async def test_resource_sync_status(client, monkeypatch):
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    response = await client.get("/api/resource-sync")
    assert response.status == 200
    body = await response.json()
    assert body["agents"]["missing_in_codex"] == ["planner"]


async def test_resource_sync_dry_run(client, monkeypatch):
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    response = await client.post("/api/resource-sync", json={"dry_run": True})
    assert response.status == 200
    body = await response.json()
    assert body["dry_run"] is True
    assert body["was_dry"] is True


async def test_resource_sync_rejects_non_boolean_dry_run(client, monkeypatch):
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    response = await client.post("/api/resource-sync", json={"dry_run": "yes"})
    assert response.status == 400


async def test_service_uses_container_resource_paths(monkeypatch, tmp_path):
    codex_home = tmp_path / "host-codex"
    skills_home = tmp_path / "host-agents" / "skills"
    monkeypatch.setenv("CODEX_RESOURCE_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_SKILLS_HOME", str(skills_home))

    service = routes._service()

    assert service.codex_home == Path(codex_home)
    assert service.codex_skills == Path(skills_home)


async def test_resource_sync_import(client, monkeypatch):
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    response = await client.post("/api/resource-sync/import", json={"dry_run": True})
    assert response.status == 200
    body = await response.json()
    assert body["agents"]["imported"] == ["reviewer"]
    assert body["dry_run"] is True
    assert body["was_dry"] is True


async def test_resource_sync_import_rejects_non_boolean_dry_run(client, monkeypatch):
    monkeypatch.setattr(routes, "_service", lambda: FakeService())
    response = await client.post("/api/resource-sync/import", json={"dry_run": "yes"})
    assert response.status == 400


async def test_service_registry_defaults_to_claude_home_with_no_mirror(monkeypatch, tmp_path):
    """Default install: registryHome unset, so registry == CLAUDE_HOME and no
    separate Claude mirror is needed (claude_native_home collapses to None)."""
    import database

    monkeypatch.setattr(database, "REGISTRY_HOME", database.CLAUDE_HOME)
    service = routes._service()

    assert service.claude_home == database.CLAUDE_HOME
    assert service.claude_native_home is None


async def test_service_wires_claude_native_home_when_registry_is_decoupled(monkeypatch, tmp_path):
    """Once registryHome points somewhere else, _service() must pass the
    real Claude Code home along so Claude also gets a rendered mirror."""
    import database

    decoupled_registry = tmp_path / "registry"
    decoupled_registry.mkdir()
    real_claude_home = tmp_path / "real-claude"
    real_claude_home.mkdir()
    monkeypatch.setattr(database, "REGISTRY_HOME", decoupled_registry)
    monkeypatch.setattr(database, "CLAUDE_HOME", real_claude_home)

    service = routes._service()

    assert service.claude_home == decoupled_registry
    assert service.claude_native_home == real_claude_home
