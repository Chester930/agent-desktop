"""
engines/availability.py — 「這個引擎現在真的能跑嗎」的偵測與執行期防護網。

跟 engines/registry.py 的 resolve_engine_name() 是分開的兩層：
- registry.resolve_engine_name()：純優先序（frontmatter > request > default），
  完全不管這個引擎現在到底能不能用——這一層維持原樣，不動。
- 這個模組：疊加在上面的「可用性」關注點——installed/loggedIn 偵測（帶
  TTL cache，避免每個 turn 都重新 spawn CLI 子行程）、以及
  apply_availability_fallback()，供既有呼叫點在「resolve 完引擎名稱之後、
  真的執行前」多包一層防護。

用量／額度數字沒有做：claude/codex 兩邊 CLI 都沒有任何文件化、可腳本化
的管道可以查到「剩餘用量」（已在真實已登入的 claude/codex CLI 上驗證過，
只查得到 installed/loggedIn，查不到任何 usage/limit/quota 欄位）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from helpers import safe_kill_process, wrap_cmd

CHECK_TIMEOUT = 8.0     # 單次 CLI 探測逾時（秒）
CACHE_TTL = 25.0        # 每個引擎各自的 cache 有效期（秒）

_LABEL = {"claude": "Claude Code", "codex": "OpenAI Codex"}
_REASON_LABEL = {
    "not_installed": "未安裝",
    "not_logged_in": "未登入",
    "check_timeout": "狀態檢查逾時",
    "unexpected_output": "狀態檢查失敗",
    "": "",
}


class NoEngineAvailableError(Exception):
    """Claude 和 Codex 都不可用時丟出。訊息已經是可以直接顯示給使用者的完整句子。"""


def _bin_for(engine_name: str) -> str:
    attr = "CLAUDE_BIN" if engine_name == "claude" else "CODEX_BIN"
    for mod_name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, attr):
            return getattr(mod, attr, engine_name)
    return engine_name  # "claude" / "codex" 字面值，讓 OS 自己解析 PATH


async def _check_claude() -> dict:
    """已驗證：`claude auth status --json` 回傳乾淨的 JSON，含 loggedIn 欄位。
    一次呼叫同時涵蓋 installed（能不能 spawn）跟 loggedIn 兩件事，不需要
    另外呼叫 --version，省一次子行程。"""
    proc = None
    try:
        cmd = wrap_cmd(_bin_for("claude"), ["auth", "status", "--json"])
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
        return {"installed": True, "loggedIn": False, "available": False, "reason": "check_timeout"}
    except Exception:
        # 含 FileNotFoundError（binary 不存在）——跟 mcp_sync._run_cli 同一套邏輯。
        return {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace").strip())
        logged_in = bool(data.get("loggedIn"))
    except Exception:
        # 解析不出來就當作沒登入，寧可誤判成不可用（觸發 fallback），也不要
        # 把「看起來壞掉」的引擎回報成可用。
        return {"installed": True, "loggedIn": False, "available": False, "reason": "unexpected_output"}

    return {"installed": True, "loggedIn": logged_in, "available": logged_in,
            "reason": "" if logged_in else "not_logged_in"}


async def _check_codex() -> dict:
    """已驗證：`codex login status` 已登入時輸出 "Logged in using ChatGPT"（純文字，
    沒有 --json），exit code 0。未登入時的確切輸出文字沒有驗證過（不應該為了
    測試而登出真實帳號）——這裡用 substring 比對 + exit code 雙重防呆，解析
    不出預期字樣一律當作未登入（fail closed，不要 fail open）。"""
    proc = None
    try:
        cmd = wrap_cmd(_bin_for("codex"), ["login", "status"])
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
        return {"installed": True, "loggedIn": False, "available": False, "reason": "check_timeout"}
    except Exception:
        return {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}

    text = stdout.decode("utf-8", errors="replace").strip().lower()
    logged_in = proc.returncode == 0 and "logged in" in text
    return {"installed": True, "loggedIn": logged_in, "available": logged_in,
            "reason": "" if logged_in else "not_logged_in"}


_CHECKS = {"claude": _check_claude, "codex": _check_codex}
_cache: dict = {}
_cache_lock = asyncio.Lock()


async def get_status(force: bool = False) -> dict:
    """回傳 {"claude": {...}, "codex": {...}}。TTL cache + lock 序列化 refresh，
    避免 parallel Team Run 同時好幾個 member 在 TTL 過期瞬間各自重複 spawn。"""
    async with _cache_lock:
        now = time.monotonic()
        stale = [n for n in _CHECKS if force or n not in _cache or now - _cache[n][0] >= CACHE_TTL]
        if stale:
            results = await asyncio.gather(*[_CHECKS[n]() for n in stale])
            for n, r in zip(stale, results):
                _cache[n] = (now, r)
        return {n: _cache[n][1] for n in _CHECKS}


def _format_notice(preferred: str, fallback: str, reason: str) -> str:
    why = _REASON_LABEL.get(reason, reason)
    return (f"[系統：{_LABEL[preferred]} 目前無法使用"
            f"{f'（{why}）' if why else ''}，已自動切換為 {_LABEL[fallback]}。]")


async def apply_availability_fallback(preferred_name: str):
    """preferred_name 是 registry.resolve_engine_name() 已經算出來的結果（純優先序，
    這裡完全不碰）。回傳 (final_engine_name, notice_text|None)。
    preferred 可用時 notice 必為 None、final==preferred——跟這次改動之前行為
    完全一樣，不影響任何現有已驗證路徑。兩邊都不可用時丟 NoEngineAvailableError。"""
    status = await get_status()
    if status.get(preferred_name, {}).get("available"):
        return preferred_name, None

    other = "codex" if preferred_name == "claude" else "claude"
    if status.get(other, {}).get("available"):
        reason = status.get(preferred_name, {}).get("reason", "")
        return other, _format_notice(preferred_name, other, reason)

    raise NoEngineAvailableError(
        "Claude Code 與 OpenAI Codex 目前都無法使用（未安裝或未登入），"
        "請安裝並登入至少一個 CLI 後再試一次。"
    )
