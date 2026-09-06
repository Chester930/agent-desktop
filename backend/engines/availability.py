"""
engines/availability.py — 「這個引擎現在真的能跑嗎」的偵測與執行期防護網。

跟 engines/registry.py 的 resolve_engine_name() 是分開的兩層：
- registry.resolve_engine_name()：純優先序（frontmatter > request > default），
  完全不管這個引擎現在到底能不能用——這一層維持原樣，不動。
- 這個模組：疊加在上面的「可用性」關注點——installed/loggedIn 偵測（帶
  TTL cache，避免每個 turn 都重新 spawn CLI 子行程）、以及
  apply_availability_fallback()，供既有呼叫點在「resolve 完引擎名稱之後、
  真的執行前」多包一層防護。

Claude 的額度不是從 CLI 查，而是沿用 Claude Code OAuth credentials 呼叫
Anthropic usage API，成功時疊到 quota layer；查不到時標成 unknown，不阻擋。
Codex 目前仍以 installed/loggedIn 為 runtime gate，前端另有 Codex usage
面板可顯示 app-server 回傳的限制。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

# Keep safe_kill_process available for legacy callers/tests; asynchronous
# cleanup paths use terminate_and_reap so the child is always awaited.
from helpers import safe_kill_process, subprocess_creationflags, wrap_cmd
try:
    from process_lifecycle import terminate_and_reap
except ImportError:
    from backend.process_lifecycle import terminate_and_reap

CHECK_TIMEOUT = 8.0     # 單次 CLI 探測逾時（秒）
CACHE_TTL = 25.0        # 每個引擎各自的 cache 有效期（秒）

_LABEL = {"claude": "Claude Code", "codex": "OpenAI Codex"}
_REASON_LABEL = {
    "not_installed": "未安裝",
    "not_logged_in": "未登入",
    "check_timeout": "狀態檢查逾時",
    "unexpected_output": "狀態檢查失敗",
    "quota_exhausted": "用量已滿",
    "runtime_error": "執行失敗",
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


def _main_attr(name: str, default=None):
    for mod_name in ("main", "backend.main", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, name):
            return getattr(mod, name)
    return default


def _claude_home() -> Path:
    return Path(_main_attr("CLAUDE_HOME", Path.home() / ".claude"))


def _claude_version() -> str:
    return str(_main_attr("CLAUDE_VERSION", "unknown") or "unknown")


def _base_quota(state: str = "unknown", remaining=None, resets_at=None, windows=None) -> dict:
    return {
        "state": state,
        "remainingPercent": remaining,
        "resetsAt": resets_at,
        "windows": windows or {},
    }


def _quota_window(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("utilization")
    try:
        used_f = float(used)
    except (TypeError, ValueError):
        return None
    remaining = max(0.0, min(100.0, 100.0 - used_f))
    return {
        "usedPercent": used_f,
        "remainingPercent": remaining,
        "resetsAt": raw.get("resets_at"),
    }


def normalize_claude_quota(usage: dict) -> dict:
    """Normalize Claude OAuth usage response into the shared quota layer."""
    five_hour = _quota_window(usage.get("five_hour") if isinstance(usage, dict) else None)
    seven_day = _quota_window(usage.get("seven_day") if isinstance(usage, dict) else None)
    windows = {}
    if five_hour:
        windows["five_hour"] = five_hour
    if seven_day:
        windows["seven_day"] = seven_day
    if not windows:
        return _base_quota()

    limiting = min(windows.values(), key=lambda w: w["remainingPercent"])
    remaining = limiting["remainingPercent"]
    state = "ok"
    if any(w["usedPercent"] >= 100 for w in windows.values()):
        state = "exhausted"
    elif remaining <= 20:
        state = "low"
    return _base_quota(state, remaining, limiting.get("resetsAt"), windows)


async def _fetch_claude_quota() -> dict:
    creds_file = _claude_home() / ".credentials.json"
    if not creds_file.exists():
        return _base_quota()
    try:
        creds = json.loads(creds_file.read_text(encoding="utf-8"))
        access_token = creds.get("claudeAiOauth", {}).get("accessToken", "")
    except Exception:
        return _base_quota()
    if not access_token:
        return _base_quota()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "User-Agent": f"claude-code/{_claude_version()}",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return _base_quota()
                return normalize_claude_quota(await resp.json())
    except Exception:
        return _base_quota()


def _state_detail(engine_name: str, status: dict) -> tuple[str, str, str]:
    quota = status.get("quota") if isinstance(status.get("quota"), dict) else _base_quota()
    if not status.get("installed"):
        return "not_installed", "not_installed", f"{_LABEL[engine_name]} 未安裝。"
    if not status.get("loggedIn"):
        reason = status.get("reason") or "not_logged_in"
        return reason, reason, f"{_LABEL[engine_name]} 尚未登入。"
    if quota.get("state") == "exhausted":
        reset = quota.get("resetsAt")
        suffix = f"，重置時間：{reset}" if reset else ""
        return "quota_exhausted", "quota_exhausted", f"{_LABEL[engine_name]} 用量已滿{suffix}。"
    if quota.get("state") == "low":
        remaining = quota.get("remainingPercent")
        suffix = f"（剩餘 {remaining:.0f}%）" if isinstance(remaining, (int, float)) else ""
        return "quota_low", "", f"{_LABEL[engine_name]} 用量偏低{suffix}。"
    reason = status.get("reason") or ""
    if reason:
        return reason, reason, _REASON_LABEL.get(reason, reason)
    return "ready", "", f"{_LABEL[engine_name]} 已就緒。"


async def _decorate_status(engine_name: str, status: dict) -> dict:
    decorated = dict(status)
    decorated.setdefault("installed", False)
    decorated.setdefault("loggedIn", False)
    decorated.setdefault("available", False)
    decorated.setdefault("reason", "")
    decorated.setdefault("quota", _base_quota())

    if engine_name == "claude" and decorated.get("installed") and decorated.get("loggedIn"):
        decorated["quota"] = await _fetch_claude_quota()

    state, reason, detail = _state_detail(engine_name, decorated)
    runnable = (
        bool(decorated.get("installed"))
        and bool(decorated.get("loggedIn"))
        and state not in {"quota_exhausted", "check_timeout", "unexpected_output", "not_installed", "not_logged_in"}
    )
    decorated["state"] = state
    decorated["reason"] = reason
    decorated["runnable"] = runnable
    decorated["available"] = runnable
    decorated["detail"] = detail
    return decorated


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
            creationflags=subprocess_creationflags(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return {"installed": True, "loggedIn": False, "available": False, "reason": "check_timeout"}
    except Exception:
        # 含 FileNotFoundError（binary 不存在）——跟 mcp_sync._run_cli 同一套邏輯。
        return {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}
    finally:
        await terminate_and_reap(proc)

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
            creationflags=subprocess_creationflags(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return {"installed": True, "loggedIn": False, "available": False, "reason": "check_timeout"}
    except Exception:
        return {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}
    finally:
        await terminate_and_reap(proc)

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
            decorated = await asyncio.gather(*[_decorate_status(n, r) for n, r in zip(stale, results)])
            for n, r in zip(stale, decorated):
                _cache[n] = (now, r)
        return {n: _cache[n][1] for n in _CHECKS}


def _format_notice(preferred: str, fallback: str, reason: str) -> str:
    why = _REASON_LABEL.get(reason, reason)
    return (f"[系統：{_LABEL[preferred]} 目前無法使用"
            f"{f'（{why}）' if why else ''}，已自動切換為 {_LABEL[fallback]}。]")


_ALL_ENGINES = frozenset({"claude", "codex"})


async def apply_availability_fallback(preferred_name: str, allowed: "frozenset[str]" = _ALL_ENGINES):
    """preferred_name 是 registry.resolve_engine_name()／resolve_engine_name_gated()
    已經算出來的結果（純優先序，這裡完全不碰）。回傳 (final_engine_name, notice_text|None)。
    preferred 可用時 notice 必為 None、final==preferred——跟這次改動之前行為
    完全一樣，不影響任何現有已驗證路徑。

    allowed 預設是兩個引擎都算候選——跟這次加鎖定模式之前的行為完全一樣，
    既有呼叫點跟既有測試不用改一行。鎖定模式的呼叫點會傳
    allowed=frozenset({mode})，讓另一個引擎即使可用，也不會被拿來墊背——
    這是「只用 Claude」要成為硬限制而非軟性偏好的關鍵：不然使用者鎖定了
    範圍，結果系統還是在背後偷偷切去另一個引擎，等於鎖定形同虛設。"""
    status = await get_status()
    if preferred_name in allowed and status.get(preferred_name, {}).get("available"):
        return preferred_name, None

    other = "codex" if preferred_name == "claude" else "claude"
    if other in allowed and status.get(other, {}).get("available"):
        reason = status.get(preferred_name, {}).get("reason", "")
        return other, _format_notice(preferred_name, other, reason)

    if allowed != _ALL_ENGINES:
        reason = status.get(preferred_name, {}).get("reason", "")
        why = _REASON_LABEL.get(reason, reason)
        raise NoEngineAvailableError(
            f"已鎖定僅使用 {_LABEL.get(preferred_name, preferred_name)}"
            f"（Settings → Agent Engine），但目前無法使用"
            f"{f'（{why}）' if why else ''}。請安裝並登入 "
            f"{_LABEL.get(preferred_name, preferred_name)}，或到 Settings 把執行引擎"
            f"範圍改為「兩者都開放」以允許自動切換到其他引擎。"
        )

    raise NoEngineAvailableError(
        "Claude Code 與 OpenAI Codex 目前都無法使用（未安裝或未登入），"
        "請安裝並登入至少一個 CLI 後再試一次。"
    )
