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


# ── 回收/認領（resource_sync.py 的 import_native() 對 Agent/Skill 早就有，
# MCP 之前完全沒有）：偵測 Claude 或 Codex 原生已經有、但這個 app 自己的
# 單一來源（database.py::_load_mcp_servers()）裡還沒有的 MCP server——
# 例如直接手動下 `claude mcp add` 加的，沒經過這個 app 的「＋新增」流程。
# ────────────────────────────────────────────────────────────────────────

def claude_native_list() -> dict[str, dict]:
    """直接讀 ~/.claude.json，不透過 `claude mcp list`（那個只有純文字輸出，
    沒有機器可讀格式）。合併兩個 scope：projects[cwd].mcpServers（local
    scope，`claude mcp add` 預設寫這裡）跟頂層 mcpServers（user scope，
    這個 app 自己的「＋新增」用的是這個）；同名時 local 蓋過 user，跟
    main.py::_analyze_mcp_entry／_get_mcp_command 用的是同一套查詢邏輯，
    保持一致。"""
    import json
    import database as _db
    path = _db.CLAUDE_HOME.parent / ".claude.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    project_key = str(Path.home())
    user_scope = raw.get("mcpServers", {}) or {}
    local_scope = raw.get("projects", {}).get(project_key, {}).get("mcpServers", {}) or {}
    return {**user_scope, **local_scope}


async def codex_native_list() -> dict[str, dict]:
    """`codex mcp list --json`——Codex 有自己完全獨立的設定，不會出現在
    ~/.claude.json 裡。"""
    import json
    try:
        proc = await asyncio.create_subprocess_exec(
            _codex_bin(), "mcp", "list", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        entries = json.loads(out.decode("utf-8", errors="replace"))
    except Exception:
        return {}
    result: dict[str, dict] = {}
    for e in entries:
        name = e.get("name")
        if not name:
            continue
        transport = e.get("transport") or {}
        result[name] = {
            "type": "http" if transport.get("type") not in (None, "stdio") else "stdio",
            "command": transport.get("command", ""),
            "args": transport.get("args", []),
            "env": transport.get("env", {}),
            "url": transport.get("url", ""),
        }
    return result


def _native_cfg_to_app_cfg(native: dict) -> dict:
    """把一份原生（Claude 或 Codex）MCP 設定，正規化成這個 app 自己的定義
    格式（見 routes/mcp_servers.py::handle_mcp_servers_post 接受的形狀）。"""
    if native.get("url") or native.get("type") == "http":
        return {"type": "http", "url": native.get("url", ""), "headers": {}}
    return {
        "type": "stdio",
        "command": native.get("command", ""),
        "args": list(native.get("args") or []),
        "env": dict(native.get("env") or {}),
    }


async def compute_importable() -> dict:
    """哪些名稱在 Claude 和／或 Codex 原生已經有註冊，但還沒被這個 app
    自己的單一來源（registry）採納。兩邊都有但設定不一致的話標記
    conflict，交給使用者自己判斷要採用哪一份，不硬猜。"""
    import database as _db
    registry = set(_db._load_mcp_servers().keys())
    claude_native = claude_native_list()
    codex_native = await codex_native_list()
    names = (set(claude_native) | set(codex_native)) - registry
    items = []
    for name in sorted(names):
        in_claude = name in claude_native
        in_codex = name in codex_native
        conflict = False
        if in_claude and in_codex:
            conflict = _native_cfg_to_app_cfg(claude_native[name]) != _native_cfg_to_app_cfg(codex_native[name])
        source = claude_native.get(name) or codex_native.get(name)
        items.append({
            "name": name, "inClaude": in_claude, "inCodex": in_codex,
            "conflict": conflict, "preview": _native_cfg_to_app_cfg(source),
        })
    return {"importable": items}


async def import_native(name: str) -> dict:
    """把一個偵測到的原生 MCP server 採納進 app 自己的單一來源，然後照
    正常流程同步回兩邊 CLI，讓兩邊從此保持一致（原本只有單邊有的，這步
    之後兩邊都會有）。"""
    import database as _db
    registry = _db._load_mcp_servers()
    if name in registry:
        return {"ok": False, "error": "already in registry"}
    claude_native = claude_native_list()
    codex_native = await codex_native_list()
    source = claude_native.get(name) or codex_native.get(name)
    if source is None:
        return {"ok": False, "error": "not found natively"}
    cfg = _native_cfg_to_app_cfg(source)
    synced = await sync_add(name, cfg)
    cfg["synced"] = synced
    registry[name] = cfg
    _db._save_mcp_servers(registry)
    return {"ok": True, "name": name, **cfg}
