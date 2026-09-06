"""Windows console-window suppression for every real CLI subprocess this
backend spawns (Claude/Codex CLI turns, engine availability checks, MCP
server spawns, docker calls).

Without this, every one of those spawns — which on Windows usually goes
through `cmd /c ...` via `wrap_cmd()` for npm `.cmd` shims — lets Windows
allocate a brand new console window per call, since `cmd.exe` is a console
application. On a console-less packaged backend, this visibly flashes a
terminal window for every chat turn / Team Run step / periodic
availability poll / MCP handshake.
"""
import helpers


def test_subprocess_creationflags_is_noop_on_non_windows(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert helpers.subprocess_creationflags() == 0


def test_subprocess_creationflags_suppresses_console_on_windows(monkeypatch):
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert helpers.subprocess_creationflags() == 0x08000000
