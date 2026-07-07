"""T23: parallel 模式下同一個 run_id 底下多個 step 各自 spawn 一個 process，
原本用單一 dict[run_id]=proc 只能記住最後一個，其餘變成孤兒 process。"""
import pytest

import routes.teams as teams_module


class FakeProc:
    pass


def test_register_multiple_procs_same_run_id_tracked_independently():
    run_id = "run-multi"
    p1, p2, p3 = FakeProc(), FakeProc(), FakeProc()

    teams_module._register_team_proc(run_id, p1)
    teams_module._register_team_proc(run_id, p2)
    teams_module._register_team_proc(run_id, p3)

    assert teams_module._team_run_processes[run_id] == {p1, p2, p3}


def test_unregister_one_does_not_drop_the_others():
    run_id = "run-partial-finish"
    p1, p2 = FakeProc(), FakeProc()
    teams_module._register_team_proc(run_id, p1)
    teams_module._register_team_proc(run_id, p2)

    # simulates step 1 finishing first (its `finally` unregisters itself)
    teams_module._unregister_team_proc(run_id, p1)

    assert teams_module._team_run_processes[run_id] == {p2}


def test_kill_team_run_processes_kills_every_tracked_proc(monkeypatch):
    run_id = "run-kill-all"
    p1, p2, p3 = FakeProc(), FakeProc(), FakeProc()
    teams_module._register_team_proc(run_id, p1)
    teams_module._register_team_proc(run_id, p2)
    teams_module._register_team_proc(run_id, p3)

    killed = []
    monkeypatch.setattr(teams_module, "safe_kill_process", lambda p: killed.append(p))

    teams_module._kill_team_run_processes(run_id)

    assert set(killed) == {p1, p2, p3}


def test_last_unregister_cleans_up_empty_entry():
    run_id = "run-empty-cleanup"
    p1 = FakeProc()
    teams_module._register_team_proc(run_id, p1)
    teams_module._unregister_team_proc(run_id, p1)

    assert run_id not in teams_module._team_run_processes
