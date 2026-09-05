"""Deterministic contract tests for the provider-neutral agent harness."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agent_harness import AgentCheckpointStore, AgentTask, evaluate_event_contract, resolve_ready_tasks
import routes.teams as teams_module


def test_agent_task_round_trips_structured_handoff():
    task = AgentTask(
        task_id="step-1",
        parent_run_id="run-123",
        assigned_agent="researcher",
        input_refs=["memory:brief"],
        acceptance_criteria=["include sources", "return a concise summary"],
        output_refs=["artifact:research"],
    )

    assert AgentTask.from_dict(task.to_dict()).to_dict() == task.to_dict()


@pytest.mark.parametrize("field,value", [
    ("task_id", "../escape"),
    ("assigned_agent", "agent/name"),
])
def test_agent_task_rejects_unsafe_identifiers(field, value):
    payload = {
        "task_id": "step-1",
        "assigned_agent": "researcher",
        "acceptance_criteria": ["return output"],
        field: value,
    }
    with pytest.raises(ValueError):
        AgentTask.from_dict(payload)


def test_agent_task_requires_acceptance_criteria():
    with pytest.raises(ValueError, match="acceptance_criteria"):
        AgentTask(task_id="step-1", assigned_agent="researcher", acceptance_criteria=[])


def test_agent_task_round_trips_dependency_graph_fields():
    task = AgentTask(
        task_id="step-2",
        assigned_agent="reviewer",
        acceptance_criteria=["approve or request changes"],
        blocks=["step-3"],
        blocked_by=["step-1"],
        discovered_from="step-1",
    )

    round_tripped = AgentTask.from_dict(task.to_dict())
    assert round_tripped.blocks == ["step-3"]
    assert round_tripped.blocked_by == ["step-1"]
    assert round_tripped.discovered_from == "step-1"
    assert round_tripped.to_dict() == task.to_dict()


def test_agent_task_dependency_fields_default_empty():
    task = AgentTask(task_id="step-1", assigned_agent="researcher", acceptance_criteria=["done"])
    assert task.blocks == []
    assert task.blocked_by == []
    assert task.discovered_from == ""


def test_agent_task_rejects_unsafe_discovered_from():
    with pytest.raises(ValueError):
        AgentTask(
            task_id="step-1",
            assigned_agent="researcher",
            acceptance_criteria=["done"],
            discovered_from="../escape",
        )


def test_resolve_ready_tasks_returns_only_unblocked_pending_work():
    upstream = AgentTask(task_id="a", assigned_agent="x", acceptance_criteria=["done"], status="done")
    downstream_ready = AgentTask(
        task_id="b", assigned_agent="y", acceptance_criteria=["done"], blocked_by=["a"],
    )
    downstream_blocked = AgentTask(
        task_id="c", assigned_agent="z", acceptance_criteria=["done"], blocked_by=["b"],
    )
    independent = AgentTask(task_id="d", assigned_agent="w", acceptance_criteria=["done"])

    ready = resolve_ready_tasks([upstream, downstream_ready, downstream_blocked, independent])

    assert set(ready) == {"b", "d"}


def test_resolve_ready_tasks_treats_unknown_blocker_as_unresolved():
    task = AgentTask(
        task_id="b", assigned_agent="y", acceptance_criteria=["done"], blocked_by=["missing-task"],
    )
    assert resolve_ready_tasks([task]) == []


def test_resolve_ready_tasks_handles_circular_dependency_without_looping():
    task_a = AgentTask(task_id="a", assigned_agent="x", acceptance_criteria=["done"], blocked_by=["b"])
    task_b = AgentTask(task_id="b", assigned_agent="y", acceptance_criteria=["done"], blocked_by=["a"])

    assert resolve_ready_tasks([task_a, task_b]) == []


def test_resolve_ready_tasks_excludes_terminal_tasks():
    done_task = AgentTask(task_id="a", assigned_agent="x", acceptance_criteria=["done"], status="done")
    cancelled_task = AgentTask(task_id="b", assigned_agent="y", acceptance_criteria=["done"], status="cancelled")
    assert resolve_ready_tasks([done_task, cancelled_task]) == []


def test_checkpoint_store_survives_new_store_instance(tmp_path):
    run = {"id": "run-123", "status": "running", "steps": []}
    events = [{"type": "run_started", "run_id": "run-123"}]
    AgentCheckpointStore(tmp_path).save("run-123", run, events)

    restored = AgentCheckpointStore(tmp_path).load("run-123")
    assert restored is not None
    assert restored["run"] == run
    assert restored["events"] == events
    assert restored["schema_version"] == 2
    assert (tmp_path / "agent-runs" / "checkpoints.sqlite3").exists()


def test_checkpoint_store_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        AgentCheckpointStore(tmp_path).save("../escape", {}, [])


def test_checkpoint_store_appends_events_without_duplicating(tmp_path):
    """Each save() call passes the full accumulated event list (see
    routes/teams.py `_checkpoint_save`); the store must only append the new
    tail, not duplicate previously-stored events."""
    store = AgentCheckpointStore(tmp_path)
    run = {"id": "run-1", "status": "running"}
    store.save("run-1", run, [{"type": "run_started"}])
    store.save("run-1", run, [{"type": "run_started"}, {"type": "step_start", "step": 0}])
    store.save("run-1", {**run, "status": "done"}, [
        {"type": "run_started"}, {"type": "step_start", "step": 0}, {"type": "done"},
    ])

    conn = sqlite3.connect(str(tmp_path / "agent-runs" / "checkpoints.sqlite3"))
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM events WHERE run_id = ?", ("run-1",)).fetchone()
    finally:
        conn.close()
    assert count == 3

    restored = store.load("run-1")
    assert [e["type"] for e in restored["events"]] == ["run_started", "step_start", "done"]
    assert restored["run"]["status"] == "done"


def test_checkpoint_store_reads_legacy_json_when_no_sqlite_record(tmp_path):
    """Runs persisted by the earlier single-JSON-per-run implementation must
    still be readable after upgrading to the SQLite-backed store."""
    legacy_dir = tmp_path / "agent-runs"
    legacy_dir.mkdir(parents=True)
    legacy_payload = {
        "schema_version": 1,
        "run_id": "legacy-run",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "run": {"id": "legacy-run", "status": "done"},
        "events": [{"type": "done"}],
    }
    (legacy_dir / "legacy-run.json").write_text(json.dumps(legacy_payload), encoding="utf-8")

    restored = AgentCheckpointStore(tmp_path).load("legacy-run")
    assert restored == legacy_payload


def test_checkpoint_store_purge_older_than_leaves_recent_runs(tmp_path):
    store = AgentCheckpointStore(tmp_path)
    store.save("old-run", {"id": "old-run"}, [{"type": "done"}])
    store.save("fresh-run", {"id": "fresh-run"}, [{"type": "done"}])

    conn = sqlite3.connect(str(tmp_path / "agent-runs" / "checkpoints.sqlite3"))
    try:
        old_updated_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (old_updated_at, "old-run"))
        conn.commit()
    finally:
        conn.close()

    purged = store.purge_older_than(days=7)

    assert purged == 1
    assert store.load("old-run") is None
    assert store.load("fresh-run") is not None


def test_checkpoint_store_purge_older_than_rejects_negative_days(tmp_path):
    with pytest.raises(ValueError):
        AgentCheckpointStore(tmp_path).purge_older_than(days=-1)


def test_event_contract_reports_missing_and_forbidden_events():
    result = evaluate_event_contract(
        [{"type": "run_started"}, {"type": "text_delta"}, {"type": "run_error"}],
        required_types=("run_started", "run_finished"),
        forbidden_types=("permission_requested",),
    )

    assert result["passed"] is False
    assert result["missing_types"] == ["run_finished"]
    assert result["forbidden_types_seen"] == []


def test_event_contract_reports_elapsed_time_and_passes():
    result = evaluate_event_contract(
        [{"type": "run_started", "timestamp_ms": 100}, {"type": "run_finished", "timestamp_ms": 250}],
        required_types=("run_started", "run_finished"),
    )

    assert result["passed"] is True
    assert result["elapsed_ms"] == 150


def test_team_run_can_restore_checkpoint_after_process_restart(tmp_path, monkeypatch):
    store = AgentCheckpointStore(tmp_path)
    monkeypatch.setattr(teams_module, "_checkpoint_store", lambda: store)
    run_id = "run-restore"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "status": "done",
        "_checkpoint_enabled": True,
        "steps": [],
        "summary": "finished",
    }
    teams_module._team_events[run_id] = [{"type": "done", "summary": "finished"}]
    teams_module._checkpoint_save(run_id)
    teams_module._team_runs.pop(run_id)
    teams_module._team_events.pop(run_id)

    assert teams_module._restore_team_run(run_id) is True
    assert teams_module._team_runs[run_id]["status"] == "done"
    assert teams_module._team_events[run_id][0]["type"] == "done"
    teams_module._team_runs.pop(run_id, None)
    teams_module._team_events.pop(run_id, None)
    teams_module._team_queues.pop(run_id, None)
