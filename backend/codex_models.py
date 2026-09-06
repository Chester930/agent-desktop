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

from helpers import subprocess_creationflags, wrap_cmd
try:
    from process_lifecycle import terminate_and_reap
except ImportError:
    from backend.process_lifecycle import terminate_and_reap


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
            creationflags=subprocess_creationflags(),
        )
    except (FileNotFoundError, OSError) as exc:
        raise CodexModelsError(f"Codex CLI is unavailable: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise CodexModelsError("Codex model catalog query timed out") from exc
    finally:
        await terminate_and_reap(proc)

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
        item = {
            "slug": slug,
            "display_name": m.get("display_name") or slug,
            "description": m.get("description") or "",
        }
        # Reasoning levels are model-specific. Preserve the CLI catalog's
        # metadata so the UI never offers an effort the selected model rejects.
        if m.get("default_reasoning_level"):
            item["default_reasoning_level"] = m["default_reasoning_level"]
        levels = m.get("supported_reasoning_levels")
        if isinstance(levels, list):
            item["supported_reasoning_levels"] = [
                {
                    "effort": level.get("effort"),
                    "description": level.get("description") or "",
                }
                for level in levels
                if isinstance(level, dict) and level.get("effort")
            ]
        result.append(item)
    return result
