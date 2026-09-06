"""Read Codex account limits through the local app-server protocol.

This deliberately delegates authentication to the installed Codex CLI.  Agent
Desktop never reads Codex OAuth credentials and never exposes opaque reset-credit
identifiers to the browser.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from helpers import subprocess_creationflags, wrap_cmd


class CodexUsageError(RuntimeError):
    pass


def _window(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        used = max(0, min(100, int(value.get("usedPercent", 0))))
    except (TypeError, ValueError):
        used = 0
    return {
        "usedPercent": used,
        "remainingPercent": 100 - used,
        "windowDurationMins": value.get("windowDurationMins"),
        "resetsAt": value.get("resetsAt"),
    }


def normalize_codex_usage(rate_limits: dict, token_usage: dict) -> dict:
    snapshot = rate_limits.get("rateLimits") if isinstance(rate_limits, dict) else {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    credits = snapshot.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    reset_credits = rate_limits.get("rateLimitResetCredits")
    if not isinstance(reset_credits, dict):
        reset_credits = {}
    summary = token_usage.get("summary") if isinstance(token_usage, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    buckets = token_usage.get("dailyUsageBuckets") if isinstance(token_usage, dict) else []
    if not isinstance(buckets, list):
        buckets = []

    # Only expose fields useful to the UI. In particular, do not return the
    # opaque reset-credit IDs supplied by the account service.
    return {
        "available": True,
        "planType": snapshot.get("planType"),
        "primary": _window(snapshot.get("primary")),
        "secondary": _window(snapshot.get("secondary")),
        "credits": {
            "hasCredits": bool(credits.get("hasCredits", False)),
            "unlimited": bool(credits.get("unlimited", False)),
            "balance": credits.get("balance"),
        },
        "individualLimit": snapshot.get("individualLimit"),
        "rateLimitReachedType": snapshot.get("rateLimitReachedType"),
        "resetCreditsAvailable": int(reset_credits.get("availableCount", 0) or 0),
        "tokenUsage": {
            "lifetimeTokens": summary.get("lifetimeTokens"),
            "peakDailyTokens": summary.get("peakDailyTokens"),
            "longestRunningTurnSec": summary.get("longestRunningTurnSec"),
            "currentStreakDays": summary.get("currentStreakDays"),
            "longestStreakDays": summary.get("longestStreakDays"),
            "dailyUsageBuckets": buckets,
        },
    }


async def _send(proc: asyncio.subprocess.Process, payload: dict) -> None:
    if proc.stdin is None:
        raise CodexUsageError("Codex app-server stdin is unavailable")
    proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _read_ids(proc: asyncio.subprocess.Process, wanted: set[int]) -> dict[int, dict]:
    if proc.stdout is None:
        raise CodexUsageError("Codex app-server stdout is unavailable")
    found: dict[int, dict] = {}
    while wanted - found.keys():
        line = await proc.stdout.readline()
        if not line:
            raise CodexUsageError("Codex app-server closed before returning usage data")
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        message_id = message.get("id") if isinstance(message, dict) else None
        if message_id in wanted:
            if isinstance(message.get("error"), dict):
                error = message["error"]
                raise CodexUsageError(str(error.get("message") or error))
            found[message_id] = message
    return found


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None:
        try:
            proc.stdin.close()
            await proc.stdin.wait_closed()
        except Exception:
            pass
    try:
        if proc.returncode is None:
            proc.terminate()
        # communicate() drains stdout/stderr and lets Windows Proactor pipe
        # transports close before the event loop is torn down.
        communicate = getattr(proc, "communicate", None)
        if communicate is not None:
            await asyncio.wait_for(communicate(), timeout=2)
        else:
            await asyncio.wait_for(proc.wait(), timeout=2)
    except Exception:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass


async def fetch_codex_usage(codex_bin: str, timeout: float = 20.0) -> dict:
    """Start a short-lived app-server, read limits + token usage, then stop it."""
    cmd = wrap_cmd(codex_bin, ["app-server", "--stdio"])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
            creationflags=subprocess_creationflags(),
        )
    except (FileNotFoundError, OSError) as exc:
        raise CodexUsageError(f"Codex CLI is unavailable: {exc}") from exc

    async def exchange() -> dict:
        await _send(proc, {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "agent-desktop", "version": "1.0.0"},
                "capabilities": None,
            },
        })
        await _read_ids(proc, {1})
        await _send(proc, {"method": "initialized"})
        await _send(proc, {"id": 2, "method": "account/rateLimits/read", "params": None})
        await _send(proc, {"id": 3, "method": "account/usage/read", "params": None})
        responses = await _read_ids(proc, {2, 3})
        rate_result = responses[2].get("result") or {}
        usage_result = responses[3].get("result") or {}
        return normalize_codex_usage(rate_result, usage_result)

    try:
        return await asyncio.wait_for(exchange(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise CodexUsageError("Codex usage query timed out") from exc
    finally:
        await _stop(proc)
