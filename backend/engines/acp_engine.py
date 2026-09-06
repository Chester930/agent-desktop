"""
engines/acp_engine.py — ACP (Agent Client Protocol) client engine.

MVP target: Gemini CLI's `--acp` mode (`gemini --acp`). See
docs/SDD-2026-09-acp-client-engine.md for the full design rationale and
explicit exclusions — this module only implements what that spec calls for.

We are the ACP *client*; the spawned CLI process is the ACP *agent*. This
uses the official `agent-client-protocol` PyPI package (imported as `acp`,
pinned to protocol v1 — see `acp.PROTOCOL_VERSION`).

Unlike `acp.stdio.spawn_agent_process()` (a convenience helper the SDK
ships that spawns the subprocess itself), this module spawns the
subprocess manually via the same `wrap_cmd()`/`subprocess_creationflags()`
conventions as `claude_engine.py`/`codex_engine.py`: the SDK helper takes
a bare `command: str` with no Windows `.cmd`-shim resolution and no way to
suppress the console window it flashes on Windows, which would silently
reintroduce two bugs already fixed for the other two engines. `acp`'s
`ClientSideConnection` accepts plain `asyncio.StreamWriter`/`StreamReader`
directly (see `acp.client.connection.ClientSideConnection.__init__`), so
wiring our own `proc.stdin`/`proc.stdout` into it needs no extra adapter.

Event conversion deliberately targets the *legacy* `tool_use`/`user`
envelope documented in `engines/base.py` — not the ACP-native canonical
types added for the frontend event layer — so `handle_chat`'s SSE forward
and `_format_tool_event_as_text()` (Team Run) work with zero changes. See
the SDD's "事件轉換" section for the full reasoning.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, connect_to_agent, text_block
from acp.schema import AllowedOutcome, ClientCapabilities, DeniedOutcome, RequestPermissionResponse

from helpers import subprocess_creationflags, wrap_cmd
from process_lifecycle import terminate_and_reap
from .base import RunResult

name = "acp"
DEFAULT_PERMISSION_MODE = "acceptEdits"

# ACP's session/request_permission is a synchronous "agent pauses and waits
# for a decision" request — a fundamentally different model from this
# codebase's headless, one-shot run_turn() (see routes/teams.py's existing
# permission_mode handling). Rather than build per-tool-call allow/deny
# heuristics with no real usage to validate them against, run_turn() only
# supports two outcomes: auto-allow every request for the permission_mode
# values below, or reject the run upfront with a clear error for anything
# else. See docs/SDD-2026-09-acp-client-engine.md "Permission" section.
VALID_PERMISSION_MODES = frozenset(
    {"acceptEdits", "workspace-write", "bypassPermissions", "danger-full-access"}
)


def _acp_bin(bin_override: str = "") -> str:
    return bin_override or "gemini"


def _tool_call_content_text(update: Any) -> str:
    """Best-effort readable text for a tool_call_update, for the legacy
    tool_result envelope's `content` field."""
    parts: list[str] = []
    for item in update.content or []:
        block = getattr(item, "content", None)
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts)
    if update.raw_output is not None:
        return str(update.raw_output)
    return update.title or ""


class _Client:
    """Implements the ACP `Client` protocol (`acp.interfaces.Client`) for a
    single run_turn() call.

    Declares no filesystem/terminal capabilities in initialize() (see
    run_turn()), so the agent is expected to touch the filesystem and run
    commands directly rather than asking us to proxy them —
    write_text_file/read_text_file/create_terminal/etc. are therefore never
    called by a well-behaved agent and are intentionally left unimplemented
    here rather than stubbed out speculatively.
    """

    def __init__(self, on_text, on_tool_event) -> None:
        self._on_text = on_text
        self._on_tool_event = on_tool_event

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        kind = getattr(update, "session_update", None)
        if kind == "agent_message_chunk":
            content = update.content
            if getattr(content, "type", None) == "text" and self._on_text:
                await self._on_text(content.text)
        elif kind == "tool_call":
            if self._on_tool_event:
                await self._on_tool_event({
                    "type": "tool_use",
                    "id": update.tool_call_id,
                    "name": update.title,
                    "input": update.raw_input or {},
                })
        elif kind == "tool_call_update":
            if self._on_tool_event:
                await self._on_tool_event({
                    "type": "user",
                    "message": {"content": [{
                        "type": "tool_result",
                        "tool_use_id": update.tool_call_id,
                        "content": _tool_call_content_text(update),
                        "is_error": update.status == "failed",
                    }]},
                })
        # Every other update kind (plan, thought chunks, mode/config
        # updates, ...) has no existing callback to feed and is ignored —
        # see SDD "事件轉換" 小節: `plan` 本輪不接任何 callback.

    async def request_permission(self, session_id, tool_call, options, **kwargs: Any) -> RequestPermissionResponse:
        # Only reached when run_turn() already validated permission_mode is
        # in VALID_PERMISSION_MODES — always select an "allow" option.
        chosen = next((o for o in options if o.kind == "allow_always"), None)
        chosen = chosen or next((o for o in options if o.kind == "allow_once"), None)
        chosen = chosen or next((o for o in options if not o.kind.startswith("reject")), None)
        if chosen is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=chosen.option_id))

    def on_connect(self, conn: Any) -> None:
        pass


async def run_turn(
    *,
    prompt: str,
    cwd: str,
    model: str,
    permission_mode: str,
    resume_session_id: "str | None",
    api_key: str,
    on_text,
    on_process=None,
    is_cancelled=None,
    effort: str = "",
    attachments: "list[str] | None" = None,
    bin_override: str = "",
    on_tool_event=None,
) -> RunResult:
    # 空字串／"default" 代表呼叫端根本沒指定 permission_mode（多數 /api/chat
    # 呼叫都是這樣，見 main.py 的 `data.get("permission_mode", "")`）——比照
    # codex_engine._normalize_sandbox_mode() 的既有慣例，這種「沒選」的情況
    # 悄悄退回引擎自己的預設值，不當成錯誤。只有呼叫端「明確選了」一個不在
    # 允許清單內的值（例如唯讀/plan 類語彙）時才拒絕整個 run——見 SDD
    # 「Permission」小節：要拒絕的是猜錯的明確選擇，不是沒有選擇。
    effective_mode = permission_mode if permission_mode and permission_mode != "default" else DEFAULT_PERMISSION_MODE
    if effective_mode not in VALID_PERMISSION_MODES:
        allowed = ", ".join(sorted(VALID_PERMISSION_MODES))
        return RunResult(error=(
            f"ACP 引擎目前只支援允許編輯類的 permission_mode（{allowed}），"
            f"收到的是 {permission_mode!r}。"
        ))

    acp_bin = _acp_bin(bin_override)
    safe_cwd = cwd if (cwd and Path(cwd).is_dir()) else str(Path.home())
    cmd = wrap_cmd(acp_bin, ["--acp"])

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=safe_cwd,
            creationflags=subprocess_creationflags(),
        )
    except (FileNotFoundError, OSError) as exc:
        return RunResult(error=f"無法啟動 ACP agent（{acp_bin}）：{exc}")

    if on_process:
        on_process(proc)

    output_parts: list[str] = []

    async def _collecting_on_text(chunk: str) -> None:
        output_parts.append(chunk)
        if on_text:
            await on_text(chunk)

    client = _Client(_collecting_on_text, on_tool_event)
    conn = connect_to_agent(client, proc.stdin, proc.stdout)

    session_id = ""
    try:
        # No fs/terminal capabilities declared (ClientCapabilities()'s own
        # defaults already say no to both): the agent is expected to access
        # the filesystem and run commands directly, not through us.
        await conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
        )

        if resume_session_id:
            # Reuse the id for the next prompt only — see SDD "明確排除" #2:
            # no session/load full-history replay in this MVP.
            session_id = resume_session_id
        else:
            new_session = await conn.new_session(cwd=safe_cwd)
            session_id = new_session.session_id

        watcher_task = None
        if is_cancelled:
            async def _watch_cancel() -> None:
                while True:
                    await asyncio.sleep(0.5)
                    if is_cancelled():
                        with contextlib.suppress(Exception):
                            await conn.cancel(session_id)
                        return
            watcher_task = asyncio.create_task(_watch_cancel())

        try:
            await conn.prompt(session_id=session_id, prompt=[text_block(prompt)])
        finally:
            if watcher_task:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task

        return RunResult(output="".join(output_parts), session_id=session_id)
    except Exception as exc:
        return RunResult(output="".join(output_parts), session_id=session_id, error=str(exc))
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
        await terminate_and_reap(proc)
