"""
mcp_sync.py — 把 app 自己的 MCP server 定義（backend/database.py 的
`_load_mcp_servers()`/`_save_mcp_servers()`）同步到 Claude Code CLI 跟
OpenAI Codex CLI 各自的原生設定。

設計決定（2026-07-11，經 plan mode 研究確認，非猜測）：
- **不直接讀寫 `~/.claude.json`／`~/.codex/config.toml`**，改成 shell out
  到兩邊 CLI 自己的 `mcp add`/`mcp remove` 指令——這兩個檔案常帶著
  API key/token，app 自己動手改風險高；而且 `~/.claude.json` 已經有
  `handle_cli`（main.py）的白名單機制證實 shell out 是安全、已驗證的做法
  （只是原本沒開放 `add`）。
- **Claude**：`claude mcp add <name> -e K=V -s user -- <command> [args...]`
  （stdio）／`claude mcp add --transport http -s user <name> <url> [--header "K: V"]`
  （http）。`-s user`（scope=user，全域、不綁 cwd）是刻意選擇——這個 app
  的 agent 會在各種不同工作目錄下執行，只有 user scope 保證每次都看得到，
  local/project scope 是綁定特定目錄的。
- **Codex**：`codex mcp add <name> --env K=V -- <command> [args...]`
  （stdio）／`codex mcp add <name> --url <url>`（http）。**已確認**：
  Codex 的 HTTP MCP 只支援 `--bearer-token-env-var`／OAuth 這類認證，
  沒有 Claude 那種任意 `--header` 機制——如果使用者填了 `headers`，
  Codex 端目前只能忽略（見 `_codex_add_cmd` 註解），這是兩邊 CLI 本身
  能力不對稱，不是這裡的 bug。
- **不去碰 Codex 的 plugin marketplace**（`[plugins."x@source"]`，
  figma/github/linear/notion 這類官方 curated 外掛）——那是 Codex 自己的
  帳號/OAuth 生態，跟這裡要同步的「簡單 stdio/HTTP server 定義」不是同一
  回事。
- 兩邊 CLI 的 `mcp add`/`remove` 都沒有 JSON 輸出，只能看 exit code——
  `sync_add`/`sync_remove` 回傳 `{"claude": bool, "codex": bool}`，某一邊
  CLI 不存在/呼叫失敗只會讓那一邊記 `False`，不影響另一邊、不拋例外。
- 兩邊 CLI 都沒有替設定檔本身上鎖，這裡用模組層級 `asyncio.Lock` 序列化
  同步操作，避免併發呼叫互踩。

尚未驗證（下一步用真實 CLI 跑一次 add → get → remove 確認語法完全正確，
特別是 `remove` 是否也接受 `-s user`，這次沒有在官方 `--help` 明確看到）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from helpers import safe_kill_process, wrap_cmd

_sync_lock = asyncio.Lock()


def _claude_bin() -> str:
    for mod_name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "CLAUDE_BIN"):
            return getattr(mod, "CLAUDE_BIN", "claude")
    return "claude"


def _codex_bin() -> str:
    for mod_name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "CODEX_BIN"):
            return getattr(mod, "CODEX_BIN", "codex")
    return "codex"


def _claude_add_args(name: str, cfg: dict) -> list[str]:
    """組出 `claude mcp add ...` 的參數（不含 binary 本身）。"""
    args = ["mcp", "add"]
    if cfg.get("type") == "http":
        args += ["--transport", "http", "-s", "user", name, cfg.get("url", "")]
        for k, v in (cfg.get("headers") or {}).items():
            args += ["--header", f"{k}: {v}"]
    else:
        args += ["-s", "user"]
        for k, v in (cfg.get("env") or {}).items():
            args += ["-e", f"{k}={v}"]
        args += [name, "--", cfg.get("command", "")] + list(cfg.get("args") or [])
    return args


def _codex_add_args(name: str, cfg: dict) -> list[str]:
    """組出 `codex mcp add ...` 的參數（不含 binary 本身）。"""
    args = ["mcp", "add", name]
    if cfg.get("type") == "http":
        args += ["--url", cfg.get("url", "")]
        # 已確認：Codex 的 HTTP MCP 認證只有 --bearer-token-env-var／OAuth，
        # 沒有 Claude 那種任意 header 機制，headers 欄位這裡無法翻譯，
        # 靜默略過（不是 bug，是兩邊 CLI 能力不對稱）。
    else:
        for k, v in (cfg.get("env") or {}).items():
            args += ["--env", f"{k}={v}"]
        args += ["--", cfg.get("command", "")] + list(cfg.get("args") or [])
    return args


async def _run_cli(bin_path: str, args: list[str], timeout: float = 30.0) -> bool:
    """執行一次 CLI 呼叫，只回傳成不成功（exit code 0），不 parse 輸出——
    兩邊 CLI 的 mcp add/remove 都沒有機器可讀的輸出格式。CLI 不存在（找不到
    binary）或逾時都視為失敗，不拋例外，讓呼叫端可以繼續處理另一邊。"""
    proc = None
    try:
        cmd = wrap_cmd(bin_path, args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
        return False
    except Exception:
        # 包含 FileNotFoundError（binary 不存在，例如使用者沒裝 Codex）。
        return False


async def sync_add(name: str, cfg: dict) -> dict:
    """把一個 MCP server 定義同步到兩邊 CLI。回傳 {"claude": bool, "codex": bool}，
    某一邊失敗不影響另一邊、不拋例外。"""
    async with _sync_lock:
        claude_ok = await _run_cli(_claude_bin(), _claude_add_args(name, cfg))
        codex_ok = await _run_cli(_codex_bin(), _codex_add_args(name, cfg))
        return {"claude": claude_ok, "codex": codex_ok}


async def sync_remove(name: str) -> dict:
    """把一個 MCP server 從兩邊 CLI 移除。回傳 {"claude": bool, "codex": bool}。"""
    async with _sync_lock:
        claude_ok = await _run_cli(_claude_bin(), ["mcp", "remove", "-s", "user", name])
        codex_ok = await _run_cli(_codex_bin(), ["mcp", "remove", name])
        return {"claude": claude_ok, "codex": codex_ok}
