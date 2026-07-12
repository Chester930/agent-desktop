"""
engines/registry.py — 引擎名稱 → 模組的對照表，以及「該用哪個引擎」的解析順序。

解析順序（優先序由高到低）：
1. Agent frontmatter 裡個別宣告的 `engine:`（同一個 team 混用 Claude/Codex
   成員時，這裡就是每個成員各自的選擇）。
2. 呼叫端（例如 POST /api/team/run 的 `agent_engine` 欄位）傳入的預設值。
3. DEFAULT_ENGINE_NAME（"codex"）——使用者兩邊 CLI 都裝、都能用時的預設
   選擇（2026-07-13 確認）。
"""

from __future__ import annotations

from . import claude_engine, codex_engine

ENGINES = {
    claude_engine.name: claude_engine,
    codex_engine.name: codex_engine,
}

DEFAULT_ENGINE_NAME = "codex"


def resolve_engine_name(frontmatter_engine: str, request_engine: str) -> str:
    for candidate in (frontmatter_engine, request_engine):
        if candidate and candidate in ENGINES:
            return candidate
    return DEFAULT_ENGINE_NAME


def resolve_engine_name_gated(frontmatter_engine: str, request_engine: str, mode: str) -> str:
    """疊加在 resolve_engine_name() 之上的「模式鎖定」層。mode 是
    database.get_engine_mode() 的回傳值（已正規化成 'claude'/'codex'/'both'
    三選一）：
    - 'claude' / 'codex'：直接回傳該值，frontmatter_engine／request_engine
      完全不看——這是刻意的行為收斂，不是優先序調整。鎖定生效時，agent
      自己的 engine: 宣告在執行期是死的，即使 UI 還讓人看到它。
    - 'both'：原封不動 delegate 給 resolve_engine_name()，行為與這次改動
      之前完全相同。
    """
    if mode in ("claude", "codex"):
        return mode
    return resolve_engine_name(frontmatter_engine, request_engine)


def get_engine(engine_name: str):
    return ENGINES.get(engine_name, ENGINES[DEFAULT_ENGINE_NAME])
