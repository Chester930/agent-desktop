"""Regression coverage for launch_windows_terminal_monitor().

Reported bug 1: a user's "Project" run left a pane permanently stuck on a
Windows Terminal error dialog — ``Get-Content -LiteralPath
'.agent_<id>.log' -Wait`` failing with ERROR_FILE_NOT_FOUND
(0x80070002). The Python-side pre-create step (`log_file.write_text("")`)
exists but its failure was silently swallowed, and once that single
`powershell -NoExit -Command "..."` pane's Get-Content call fails, it
never recovers (``-NoExit`` just keeps the error on screen).

Fix: the PowerShell command itself is now self-healing (Test-Path +
New-Item before Get-Content -Wait), independent of whether the Python-side
pre-create succeeded, and pre-create failures are logged instead of
silently discarded.

Reported issue 2: a team with several members produced one Windows
Terminal window carved into an increasingly tiny split-pane grid — one
pane per member. Fixed by opening one window per Project with one *tab*
per member instead of a split-pane grid, switched via the tab strip.
"""
import main


def test_no_windows_terminal_launch_on_non_windows(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Linux")
    calls = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))

    main.launch_windows_terminal_monitor("/some/project", [{"agent": "a"}])

    assert calls == []


def test_no_windows_terminal_launch_with_no_members(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Windows")
    calls = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: calls.append((a, kw)))

    main.launch_windows_terminal_monitor("/some/project", [])

    assert calls == []


def test_monitor_command_self_heals_missing_log_file(monkeypatch, tmp_path):
    """The PowerShell -Command must guard Get-Content -Wait with a
    Test-Path/New-Item check, so a pane never dies with
    ERROR_FILE_NOT_FOUND regardless of whether the Python-side
    pre-create step ran or succeeded."""
    monkeypatch.setattr(main.platform, "system", lambda: "Windows")
    captured = {}

    def fake_popen(args, shell=False):
        captured["args"] = args

    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)

    members = [{"agent": "exec-locked-agent"}, {"agent": "second-agent"}]
    main.launch_windows_terminal_monitor(str(tmp_path), members)

    args = captured["args"]
    assert args[0] == "wt"

    # Every -Command payload for every pane must self-heal before tailing.
    command_payloads = [args[i + 1] for i, tok in enumerate(args) if tok == "-Command"]
    assert len(command_payloads) == len(members)
    for payload in command_payloads:
        assert "Test-Path" in payload
        assert "New-Item" in payload
        assert "Get-Content -LiteralPath" in payload
        assert "-Wait -Tail 20" in payload

    # The log files should also still be pre-created as the first line of
    # defense (belt-and-suspenders, not a replacement for the self-heal).
    for m in members:
        assert (tmp_path / f".agent_{m['agent']}.log").exists()


def test_one_window_one_tab_per_member_not_split_panes(monkeypatch, tmp_path):
    """One Project == one `wt` window; each additional member is a
    `new-tab` (switched via the tab strip), not a `split-pane` (which
    turns into an unreadably small grid once a team has more than a
    couple of members)."""
    monkeypatch.setattr(main.platform, "system", lambda: "Windows")
    captured = {}

    def fake_popen(args, shell=False):
        captured["args"] = args

    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)

    members = [{"agent": "leader"}, {"agent": "coder"}, {"agent": "reviewer"}]
    main.launch_windows_terminal_monitor(str(tmp_path), members)

    args = captured["args"]
    assert args[0] == "wt"
    assert "split-pane" not in args
    assert args.count("new-tab") == len(members) - 1
    # Every pane/tab (including the initial one) is titled after its agent
    # so the tab strip identifies which agent it's watching.
    titles = [args[i + 1] for i, tok in enumerate(args) if tok == "--title"]
    assert titles == [m["agent"] for m in members]


def test_precreate_failure_is_logged_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(main.platform, "system", lambda: "Windows")
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **kw: None)

    logged = []
    monkeypatch.setattr(main, "_log", lambda msg: logged.append(msg))

    class _BoomPath:
        def __init__(self, *a, **kw):
            pass

        def exists(self):
            return False

        def write_text(self, *a, **kw):
            raise OSError("simulated disk error")

    # Force Path(project_path) / f".agent_{id}.log" to yield an object whose
    # write_text() always raises, without needing a real unwritable path.
    real_path = main.Path

    def fake_path(value):
        if value == str(tmp_path):
            class _Dir:
                def __truediv__(self, name):
                    return _BoomPath()
            return _Dir()
        return real_path(value)

    monkeypatch.setattr(main, "Path", fake_path)

    main.launch_windows_terminal_monitor(str(tmp_path), [{"agent": "broken-agent"}])

    assert any("broken-agent" in msg and "simulated disk error" in msg for msg in logged)
