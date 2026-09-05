"""End-to-end integration tests for the Team Run harness work (happy path,
error cleanup, and mid-run cancel).

Drives a Team run through the *real* aiohttp HTTP routes (POST /api/team/run,
GET .../stream, DELETE to cancel, GET the run) instead of calling internal
functions directly, and verifies against the *real* SQLite-backed
AgentCheckpointStore on disk (via the isolated tmp CLAUDE_HOME the `client`
fixture already sets up).

Only the deepest CLI/provider boundary (`_agent_run_capture`) is stubbed —
that is the one call that would otherwise spend real Claude/Codex API quota.
Everything above it (routing, background task scheduling, checkpoint
persistence, SSE event delivery, cancel/error handling) runs unmodified.

Note: `/api/team/run` has no interactive permission-request concept to test
— each engine call is a headless, non-interactive run_turn() with a fixed
`permission_mode` decided at dispatch time, not a paused session waiting on
a user decision (see `_agent_run_capture`'s permission_mode handling). That
distinguishes it from `/api/chat`'s interactive permission flow.
"""

import asyncio
import json

import routes.teams as teams_module
from agent_harness import AgentCheckpointStore


async def _drain_stream_to_terminal_event(client, run_id: str) -> list[str]:
    stream_resp = await client.get(f"/api/team/run/{run_id}/stream")
    seen_types = []
    async for raw_line in stream_resp.content:
        line = raw_line.decode().strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: "):])
        seen_types.append(event.get("type"))
        if event.get("type") in ("done", "error", "cancelled"):
            break
    return seen_types


async def test_team_run_completes_end_to_end_over_real_http_and_checkpoints(client, monkeypatch):
    calls = []

    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits", default_engine=""):
        calls.append((agent_id, step_idx))
        return f"result-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    resp = await client.post("/api/team/run", json={
        "task": "integration smoke test",
        "team": {
            "name": "integration-team",
            "execution_mode": "sequential",
            "members": [
                {"agent": "agent-a", "role": "first"},
                {"agent": "agent-b", "role": "second"},
            ],
        },
    })
    assert resp.status == 200
    run_id = (await resp.json())["run_id"]

    # Drive the real SSE endpoint to completion. The background task runs
    # cooperatively on the same event loop as this coroutine, so awaiting
    # each streamed line naturally lets the run progress to "done".
    seen_types = await _drain_stream_to_terminal_event(client, run_id)

    assert calls == [("agent-a", 0), ("agent-b", 1)]
    assert "done" in seen_types

    # Final run state, fetched over real HTTP, must reflect completion.
    get_resp = await client.get(f"/api/team/run/{run_id}")
    run_state = await get_resp.json()
    assert run_state["status"] == "done"
    assert run_state["steps"][0]["handoff"]["status"] == "done"
    assert run_state["steps"][1]["handoff"]["status"] == "done"

    # The checkpoint must have actually landed in the SQLite-backed store
    # (P1: docs/SDD-2026-09-checkpoint-store-durability.md), independent of
    # the in-memory _team_runs dict this process happens to still hold.
    import database as db
    store = AgentCheckpointStore(db.CLAUDE_HOME)
    restored = store.load(run_id)
    assert restored is not None
    assert restored["run"]["status"] == "done"
    assert any(e.get("type") == "done" for e in restored["events"])


async def test_team_run_error_mid_run_reflected_in_status_and_checkpoint(client, monkeypatch):
    """An unhandled exception inside a member's capture call must not leave
    the run stuck "running" forever (see tests/test_team_run_error_handling.py
    for the direct-call version of this scenario) — here driven over real
    HTTP end to end, including the checkpoint on disk."""

    async def fake_agent_run_capture_boom(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits", default_engine=""):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture_boom)

    resp = await client.post("/api/team/run", json={
        "task": "error path smoke test",
        "team": {
            "name": "error-team",
            "execution_mode": "sequential",
            "members": [{"agent": "agent-a", "role": "first"}],
        },
    })
    assert resp.status == 200
    run_id = (await resp.json())["run_id"]

    # _execute_team_run's unhandled-exception path emits a terminal "done"
    # wire event carrying an error summary; the run's *status* field (not
    # the SSE event type) is what actually says "error".
    seen_types = await _drain_stream_to_terminal_event(client, run_id)
    assert "done" in seen_types

    get_resp = await client.get(f"/api/team/run/{run_id}")
    run_state = await get_resp.json()
    assert run_state["status"] == "error"
    assert "執行錯誤" in run_state["summary"]

    import database as db
    store = AgentCheckpointStore(db.CLAUDE_HOME)
    restored = store.load(run_id)
    assert restored is not None
    assert restored["run"]["status"] == "error"


async def test_team_run_cancel_mid_run_stops_before_next_step(client, monkeypatch):
    """Cancelling while step 0 is genuinely in flight: step 0 (already
    started) must be allowed to finish, but step 1 must never start, and
    the final state — over real HTTP and in the on-disk checkpoint — must
    say "cancelled", not "running" or "done"."""

    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def fake_agent_run_capture(run_id, step_idx, agent_id, prompt, model, cwd, permission_mode="acceptEdits", default_engine=""):
        calls.append(agent_id)
        if step_idx == 0:
            started.set()
            await release.wait()
        return f"result-from-{agent_id}"

    monkeypatch.setattr(teams_module, "_agent_run_capture", fake_agent_run_capture)

    resp = await client.post("/api/team/run", json={
        "task": "cancel path smoke test",
        "team": {
            "name": "cancel-team",
            "execution_mode": "sequential",
            "members": [
                {"agent": "agent-a", "role": "first"},
                {"agent": "agent-b", "role": "second"},
            ],
        },
    })
    assert resp.status == 200
    run_id = (await resp.json())["run_id"]

    await asyncio.wait_for(started.wait(), timeout=5)

    cancel_resp = await client.delete(f"/api/team/run/{run_id}")
    assert cancel_resp.status == 200

    release.set()

    # Wait for the background task to actually settle (`_finished_at` is
    # only set once _execute_team_run_core has fully returned), not just
    # for the status flag the DELETE handler already flipped synchronously.
    run_state = None
    for _ in range(50):
        get_resp = await client.get(f"/api/team/run/{run_id}")
        run_state = await get_resp.json()
        if run_state.get("_finished_at") is not None:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("team run did not settle after cancel within the timeout")

    assert run_state["status"] == "cancelled"
    # Step 0 was already in flight and must be allowed to complete; step 1
    # must never be dispatched once the run is cancelled.
    assert calls == ["agent-a"]

    import database as db
    store = AgentCheckpointStore(db.CLAUDE_HOME)
    restored = store.load(run_id)
    assert restored is not None
    assert restored["run"]["status"] == "cancelled"


async def test_team_run_tool_events_reach_real_sse_stream(client, monkeypatch):
    """See docs/SDD-2026-09-acp-aligned-event-schema.md's Team Run
    extension: _on_tool_event's canonical-shape pass-through (raw
    tool_use / user-tool_result, tagged with `step`) must actually arrive
    at a real SSE client over HTTP, not just land in the in-memory
    _team_events list (already covered directly in
    tests/test_tool_event_streaming.py). This exercises the codex engine
    path with the session's shared `test-agent` fixture, forcing engine
    selection via the run-level `agent_engine` field."""
    from engines import codex_engine
    from engines.base import RunResult

    async def fake_codex_run_turn(**kwargs):
        await kwargs["on_tool_event"]({
            "type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"},
        })
        await kwargs["on_tool_event"]({
            "type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "i1", "content": "hi"},
            ]},
        })
        return RunResult(output="done", session_id="sid")

    monkeypatch.setattr(codex_engine, "run_turn", fake_codex_run_turn)

    resp = await client.post("/api/team/run", json={
        "task": "tool event smoke test",
        "agent_engine": "codex",
        "team": {
            "name": "tool-event-team",
            "execution_mode": "sequential",
            "members": [{"agent": "test-agent", "role": "first"}],
        },
    })
    assert resp.status == 200
    run_id = (await resp.json())["run_id"]

    stream_resp = await client.get(f"/api/team/run/{run_id}/stream")
    events = []
    async for raw_line in stream_resp.content:
        line = raw_line.decode().strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: "):])
        events.append(event)
        if event.get("type") in ("done", "error", "cancelled"):
            break

    tool_use_events = [e for e in events if e.get("type") == "tool_use"]
    tool_result_events = [e for e in events if e.get("type") == "user"]
    assert tool_use_events == [
        {"type": "tool_use", "id": "i1", "name": "Bash", "input": {"command": "echo hi"}, "step": 0},
    ]
    assert tool_result_events == [
        {"type": "user", "step": 0, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "i1", "content": "hi"},
        ]}},
    ]
