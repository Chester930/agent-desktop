"""End-to-end integration test for the P0/P1 harness work.

Drives a Team run through the *real* aiohttp HTTP routes (POST /api/team/run,
GET .../stream, GET the run) instead of calling internal functions directly,
and verifies against the *real* SQLite-backed AgentCheckpointStore on disk
(via the isolated tmp CLAUDE_HOME the `client` fixture already sets up).

Only the deepest CLI/provider boundary (`_agent_run_capture`) is stubbed —
that is the one call that would otherwise spend real Claude/Codex API quota.
Everything above it (routing, background task scheduling, checkpoint
persistence, SSE event delivery) runs unmodified.
"""

import json

import routes.teams as teams_module
from agent_harness import AgentCheckpointStore


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
