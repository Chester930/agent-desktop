"""2026-07-10 team 協作優化健檢：_execute_team_run() 原本用
`except Exception: pass` 把 _execute_team_run_core() 拋出的任何例外整個吃掉。

後果：
- run["status"] 永遠停在 "running"（core 函式裡負責設 "done"/"_finished_at"
  的那段從未執行到）。
- SSE stream（handle_team_run_stream）永遠不會收到 done/error/cancelled 事件，
  只會每 30 秒送一個 ping，永遠不會主動關閉連線；前端進度面板因此會無限期
  卡在「執行中」，沒有任何錯誤提示。
- 因為 "_finished_at" 永遠不會被設定，_cleanup_old_runs() 的 2 小時回收機制
  抓不到這個 run，_team_runs/_team_events/_team_queues 會一直留在記憶體裡
  直到 process 重啟才釋放。

修法：比照既有的 asyncio.TimeoutError 分支，補上 status="error" +
_finished_at + 帶錯誤文字的 "done" SSE 事件，讓例外不再被靜默吞掉。
"""
import time

import pytest

import routes.teams as teams_module

pytestmark = pytest.mark.asyncio


async def test_core_exception_marks_run_as_error_not_stuck_running(monkeypatch):
    async def boom(run_id, task, model, cwd):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(teams_module, "_execute_team_run_core", boom)

    run_id = "run-exception-swallowed"
    teams_module._team_runs[run_id] = {
        "id": run_id,
        "team_id": "",
        "status": "running",
        "steps": [{"agent": "agent-a", "role": "r", "status": "pending", "output": ""}],
        "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    before = time.time()
    await teams_module._execute_team_run(run_id, "task", "haiku", "/tmp")

    run = teams_module._team_runs[run_id]
    assert run["status"] == "error"
    assert run["_finished_at"] >= before
    assert "simulated unexpected failure" in run["summary"]

    # a terminal event must have been emitted so the SSE stream can close
    # instead of pinging forever with the progress panel stuck at "running"
    events = teams_module._team_events[run_id]
    assert any(e.get("type") == "done" for e in events)


async def test_core_exception_run_is_now_eligible_for_gc(monkeypatch):
    """Without _finished_at, _cleanup_old_runs() can never evict a crashed
    run — it would leak in memory for the life of the process."""
    async def boom(run_id, task, model, cwd):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(teams_module, "_execute_team_run_core", boom)

    run_id = "run-exception-gc-eligible"
    teams_module._team_runs[run_id] = {
        "id": run_id, "team_id": "", "status": "running",
        "steps": [], "summary": "",
    }
    teams_module._team_events[run_id] = []
    teams_module._team_queues[run_id] = []

    await teams_module._execute_team_run(run_id, "task", "haiku", "/tmp")

    # simulate the run being older than the GC max_age and confirm it gets swept
    teams_module._team_runs[run_id]["_finished_at"] = time.time() - 99999
    teams_module._cleanup_old_runs(max_age=7200.0)

    assert run_id not in teams_module._team_runs
    assert run_id not in teams_module._team_events
