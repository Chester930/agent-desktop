"""Small, provider-neutral contracts for agent runs.

This module is intentionally independent from Claude/Codex SDKs.  It gives the
Team runtime two durable primitives that external harnesses can consume later:

* :class:`AgentTask` is the structured handoff between members.
* :class:`AgentCheckpointStore` stores a run snapshot and its event log using an
  atomic replace, so a process restart does not erase the last known state.

The event evaluator is deliberately deterministic.  It is used by the local
contract tests and can also be called by a future Harbor/Inspect adapter.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TASK_STATUSES = frozenset({"pending", "running", "blocked", "done", "error", "cancelled"})


def _clean_id(value: Any, field_name: str, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if not result and not required:
        return ""
    if not _SAFE_ID.fullmatch(result):
        raise ValueError(f"{field_name} must be a safe non-empty identifier")
    return result


def _clean_refs(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    refs = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 512:
            raise ValueError(f"{field_name} must contain non-empty strings")
        refs.append(item.strip())
    return refs


@dataclass(slots=True)
class AgentTask:
    """A validated unit of work handed to one agent in a Team run."""

    task_id: str
    assigned_agent: str
    acceptance_criteria: list[str]
    parent_run_id: str = ""
    input_refs: list[str] = field(default_factory=list)
    output_refs: list[str] = field(default_factory=list)
    status: str = "pending"
    retry_count: int = 0
    # Dependency-graph fields (see docs/SDD-2026-09-agent-task-dependency-graph.md).
    # Naming follows the Beads convention (blocks/blocked-by/discovered-from)
    # from claude-code-orchestrator-kit, adapted to Python identifiers.
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    discovered_from: str = ""

    def __post_init__(self) -> None:
        self.task_id = _clean_id(self.task_id, "task_id")
        self.assigned_agent = _clean_id(self.assigned_agent, "assigned_agent")
        self.parent_run_id = _clean_id(self.parent_run_id, "parent_run_id", required=False)
        if not isinstance(self.acceptance_criteria, list) or not self.acceptance_criteria or not all(
            isinstance(item, str) and item.strip() for item in self.acceptance_criteria
        ):
            raise ValueError("acceptance_criteria must contain at least one non-empty string")
        self.acceptance_criteria = [item.strip() for item in self.acceptance_criteria]
        self.input_refs = _clean_refs(self.input_refs, "input_refs")
        self.output_refs = _clean_refs(self.output_refs, "output_refs")
        self.blocks = _clean_refs(self.blocks, "blocks")
        self.blocked_by = _clean_refs(self.blocked_by, "blocked_by")
        self.discovered_from = _clean_id(self.discovered_from, "discovered_from", required=False)
        if self.status not in _TASK_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(_TASK_STATUSES))}")
        if not isinstance(self.retry_count, int) or isinstance(self.retry_count, bool) or self.retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentTask":
        if not isinstance(payload, dict):
            raise ValueError("task payload must be an object")
        return cls(
            task_id=payload.get("task_id", ""),
            parent_run_id=payload.get("parent_run_id", ""),
            assigned_agent=payload.get("assigned_agent", ""),
            input_refs=payload.get("input_refs", []),
            acceptance_criteria=payload.get("acceptance_criteria", []),
            output_refs=payload.get("output_refs", []),
            status=payload.get("status", "pending"),
            retry_count=payload.get("retry_count", 0),
            blocks=payload.get("blocks", []),
            blocked_by=payload.get("blocked_by", []),
            discovered_from=payload.get("discovered_from", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "parent_run_id": self.parent_run_id,
            "assigned_agent": self.assigned_agent,
            "input_refs": list(self.input_refs),
            "acceptance_criteria": list(self.acceptance_criteria),
            "output_refs": list(self.output_refs),
            "status": self.status,
            "retry_count": self.retry_count,
            "blocks": list(self.blocks),
            "blocked_by": list(self.blocked_by),
            "discovered_from": self.discovered_from,
        }


def resolve_ready_tasks(tasks: Iterable[AgentTask]) -> list[str]:
    """Return the task_ids in ``tasks`` whose ``blocked_by`` deps are all done.

    Only non-terminal tasks (not already ``done``/``cancelled``) are
    considered candidates. A ``blocked_by`` entry that names a task_id absent
    from ``tasks`` is treated as still unresolved (fail safe: an unknown
    blocker is never satisfied), so callers may pass a partial task list
    without risking a false "ready" result.

    This is a pure status filter, not a graph walk, so a dependency cycle
    (A blocked_by B, B blocked_by A) cannot cause recursion or an infinite
    loop: both members simply never appear in the returned list, since
    neither blocker's status ever becomes ``done`` while the cycle stands.
    """
    status_by_id = {task.task_id: task.status for task in tasks}
    ready: list[str] = []
    for task in tasks:
        if task.status in ("done", "cancelled"):
            continue
        if all(status_by_id.get(dep) == "done" for dep in task.blocked_by):
            ready.append(task.task_id)
    return ready


class AgentCheckpointStore:
    """SQLite-backed, append-only checkpoint store.

    Replaces the earlier single-JSON-overwrite-per-run implementation (see
    docs/SDD-2026-09-checkpoint-store-durability.md) with a ``runs`` table
    holding the latest run snapshot and an append-only ``events`` table, so a
    run's history can grow without rewriting everything on every save. The
    public interface (``save``/``load``/``list_run_ids``) is kept compatible
    with the previous implementation; callers do not need to change.

    Pre-existing ``<run_id>.json`` files from the old implementation are
    still readable via a fallback path in :meth:`load`/:meth:`list_run_ids`,
    so upgrading does not make earlier local history disappear.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser() / "agent-runs"
        self._lock = threading.RLock()
        self._db_path = self.root / "checkpoints.sqlite3"
        self._conn: sqlite3.Connection | None = None

    @staticmethod
    def _safe_run_id(run_id: str) -> str:
        return _clean_id(run_id, "run_id")

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, run_json TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL, PRIMARY KEY (run_id, seq))"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    def save(self, run_id: str, run: dict[str, Any], events: Iterable[dict[str, Any]]) -> None:
        if not isinstance(run, dict):
            raise ValueError("run must be an object")
        run_id = self._safe_run_id(run_id)
        event_list = [copy.deepcopy(event) for event in events]
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connection()
            conn.execute(
                "INSERT INTO runs (run_id, updated_at, run_json) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET updated_at = excluded.updated_at, "
                "run_json = excluded.run_json",
                (run_id, now, json.dumps(run, ensure_ascii=False, sort_keys=True)),
            )
            # Callers pass the full accumulated event list on every save (see
            # routes/teams.py `_checkpoint_save`); only append the tail that
            # is not already on disk instead of rewriting everything.
            (already_stored,) = conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
            for offset, event in enumerate(event_list[already_stored:]):
                conn.execute(
                    "INSERT INTO events (run_id, seq, event_json, created_at) VALUES (?, ?, ?, ?)",
                    (run_id, already_stored + offset, json.dumps(event, ensure_ascii=False, sort_keys=True), now),
                )
            conn.commit()

    def load(self, run_id: str) -> dict[str, Any] | None:
        run_id = self._safe_run_id(run_id)
        with self._lock:
            conn = self._connection()
            row = conn.execute(
                "SELECT updated_at, run_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return self._load_legacy_json(run_id)
            event_rows = conn.execute(
                "SELECT event_json FROM events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
            ).fetchall()
        updated_at, run_json = row
        try:
            run = json.loads(run_json)
            events = [json.loads(item[0]) for item in event_rows]
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(run, dict):
            return None
        return {
            "schema_version": 2,
            "run_id": run_id,
            "updated_at": updated_at,
            "run": run,
            "events": events,
        }

    def _legacy_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def _load_legacy_json(self, run_id: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._legacy_path(run_id).read_text(encoding="utf-8"))
            if payload.get("run_id") != run_id:
                return None
            if not isinstance(payload.get("run"), dict) or not isinstance(payload.get("events"), list):
                return None
            return payload
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def list_run_ids(self, older_than: datetime | None = None) -> list[str]:
        with self._lock:
            conn = self._connection()
            if older_than is None:
                rows = conn.execute("SELECT run_id FROM runs").fetchall()
            else:
                cutoff = older_than.astimezone(timezone.utc).isoformat()
                rows = conn.execute(
                    "SELECT run_id FROM runs WHERE updated_at < ?", (cutoff,)
                ).fetchall()
            run_ids = {row[0] for row in rows}
        if self.root.exists():
            for path in self.root.glob("*.json"):
                if not _SAFE_ID.fullmatch(path.stem) or path.stem in run_ids:
                    continue
                if older_than is None:
                    run_ids.add(path.stem)
                    continue
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if mtime < older_than:
                    run_ids.add(path.stem)
        return sorted(run_ids)

    def purge_older_than(self, days: int) -> int:
        """Delete runs (SQLite rows and any legacy JSON file) older than ``days``.

        Not called automatically anywhere; a caller must opt in explicitly so
        history is never silently discarded (see
        docs/SDD-2026-09-checkpoint-store-durability.md).
        """
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise ValueError("days must be a non-negative integer")
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stale_ids = self.list_run_ids(older_than=cutoff)
        with self._lock:
            conn = self._connection()
            for run_id in stale_ids:
                conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
        for run_id in stale_ids:
            try:
                self._legacy_path(run_id).unlink()
            except FileNotFoundError:
                pass
        return len(stale_ids)


def evaluate_event_contract(
    events: Iterable[dict[str, Any]],
    required_types: Iterable[str],
    forbidden_types: Iterable[str] = (),
) -> dict[str, Any]:
    """Return deterministic lifecycle metrics for a provider event stream.

    ``required_types``/``forbidden_types`` are expected to use the canonical
    event vocabulary defined in ``frontend/src/app/agent-events.ts``, which is
    intentionally aligned with the Agent Client Protocol's ``session/update``
    concepts so a future ACP adapter needs no separate contract vocabulary:

    * ``text_delta``           <-> ACP ``agent_message_chunk``
    * ``tool_call_start``      <-> ACP ``tool_call`` (status ``pending``/``in_progress``)
    * ``tool_call_end``        <-> ACP ``tool_call_update`` (status ``completed``/``failed``)
    * ``permission_requested`` <-> ACP ``permission_request``
    * ``plan``                 <-> ACP ``plan``

    Callers should keep using these names rather than inventing new ones, so
    contract tests stay meaningful once a real ACP client lands.
    """

    event_list = [event for event in events if isinstance(event, dict)]
    observed = [event.get("type") for event in event_list]
    observed_set = set(observed)
    required = list(dict.fromkeys(required_types))
    forbidden = list(dict.fromkeys(forbidden_types))
    missing = [event_type for event_type in required if event_type not in observed_set]
    forbidden_seen = [event_type for event_type in forbidden if event_type in observed_set]
    timestamps = [event.get("timestamp_ms") for event in event_list if isinstance(event.get("timestamp_ms"), (int, float))]
    elapsed_ms = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else None
    passed = not missing and not forbidden_seen
    return {
        "passed": passed,
        "event_count": len(event_list),
        "observed_types": list(dict.fromkeys(observed)),
        "missing_types": missing,
        "forbidden_types_seen": forbidden_seen,
        "elapsed_ms": elapsed_ms,
    }
