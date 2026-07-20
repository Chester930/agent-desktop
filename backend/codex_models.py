"""Discover the live Codex model catalog via `codex debug models --bundled`.

Codex's model lineup changes every few weeks and, unlike Claude's stable
opus/sonnet/haiku tier aliases, there's no stable alias system to hardcode
against — a fixed list baked into this app's own source would go stale within
weeks (confirmed: a web search for "current Codex models" already returned
slugs that don't match this machine's actually-installed CLI catalog). This
shells out to the installed Codex CLI's own `debug models` subcommand instead,
which prints its bundled model catalog as JSON, so the list this app offers is
always whatever that specific CLI build actually supports right now.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from helpers import wrap_cmd


class CodexModelsError(RuntimeError):
    pass


async def fetch_codex_models(codex_bin: str, timeout: float = 15.0) -> list[dict]:
    """Return [{slug, display_name, description}] for user-selectable models.

    Filters to visibility == "list" — the catalog also includes internal-only
    entries (e.g. "codex-auto-review", visibility "hide") that aren't meant to
    be picked directly by a user.
    """
    cmd = wrap_cmd(codex_bin, ["debug", "models", "--bundled"])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
        )
    except (FileNotFoundError, OSError) as exc:
        raise CodexModelsError(f"Codex CLI is unavailable: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        try:
            proc.kill()
        except Exception:
            pass
        raise CodexModelsError("Codex model catalog query timed out") from exc

    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip()
        raise CodexModelsError(msg or "codex debug models failed")

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise CodexModelsError("Codex model catalog returned invalid JSON") from exc

    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise CodexModelsError("Codex model catalog missing 'models' list")

    result = []
    for m in models:
        if not isinstance(m, dict) or m.get("visibility") != "list":
            continue
        slug = m.get("slug")
        if not slug:
            continue
        result.append({
            "slug": slug,
            "display_name": m.get("display_name") or slug,
            "description": m.get("description") or "",
        })
    return result
