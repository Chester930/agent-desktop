"""Optional checkpoint retention scheduling.

See docs/SDD-2026-09-checkpoint-store-durability.md: retention is disabled
by default (no automatic deletion of a user's run history) and only runs
once the user explicitly sets `checkpointRetentionDays` in config.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import database
import routes.teams as teams_module
from agent_harness import AgentCheckpointStore


def test_get_checkpoint_retention_days_defaults_to_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "CONFIG_FILE", tmp_path / "config.json")
    assert database.get_checkpoint_retention_days() is None


def test_get_checkpoint_retention_days_rejects_invalid_values(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    for bad in (-1, "7", True, 3.5, None):
        config_file.write_text(json.dumps({"checkpointRetentionDays": bad}), encoding="utf-8")
        assert database.get_checkpoint_retention_days() is None


def test_get_checkpoint_retention_days_accepts_valid_value(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)
    config_file.write_text(json.dumps({"checkpointRetentionDays": 14}), encoding="utf-8")
    assert database.get_checkpoint_retention_days() == 14


def test_run_checkpoint_retention_once_is_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "CONFIG_FILE", tmp_path / "config.json")
    assert teams_module._run_checkpoint_retention_once() is None


def test_run_checkpoint_retention_once_purges_when_enabled(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"checkpointRetentionDays": 7}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    store = AgentCheckpointStore(tmp_path)
    monkeypatch.setattr(teams_module, "_checkpoint_store", lambda: store)

    store.save("old-run", {"id": "old-run"}, [{"type": "done"}])
    store.save("fresh-run", {"id": "fresh-run"}, [{"type": "done"}])

    conn = sqlite3.connect(str(tmp_path / "agent-runs" / "checkpoints.sqlite3"))
    try:
        old_updated_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (old_updated_at, "old-run"))
        conn.commit()
    finally:
        conn.close()

    purged = teams_module._run_checkpoint_retention_once()

    assert purged == 1
    assert store.load("old-run") is None
    assert store.load("fresh-run") is not None


def test_run_checkpoint_retention_once_swallows_purge_errors(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"checkpointRetentionDays": 7}), encoding="utf-8")
    monkeypatch.setattr(database, "CONFIG_FILE", config_file)

    class _BoomStore:
        def purge_older_than(self, days):
            raise RuntimeError("simulated disk error")

    monkeypatch.setattr(teams_module, "_checkpoint_store", lambda: _BoomStore())

    assert teams_module._run_checkpoint_retention_once() is None
