"""2026-07-11：engines/availability.py 的可用性偵測與 fallback 邏輯。

這裡全部 mock 掉 asyncio.create_subprocess_exec——真實 CLI 的 happy path
（兩邊都已安裝且登入）已經在對話紀錄裡另外用這台機器上真實已登入的
claude/codex CLI 驗證過。「未登入」的確切輸出文字（尤其是 codex login
status）沒有刻意登出真實帳號去驗證過，這裡的 mock 內容是根據 --help
跟已觀察到的登入態輸出反推的假設，非真實驗證。
"""
import asyncio

import pytest

from engines import availability

pytestmark = pytest.mark.asyncio

# conftest.py 有一個 autouse fixture 把 availability.get_status 整個換成假的
# 「兩邊都可用」版本，避免既有測試意外 spawn 真實 CLI 子行程。但這個檔案
# 底下有幾個測試就是在測 get_status() 本身的 cache 邏輯，需要真正的實作
# ——這裡在任何 monkeypatch 發生前就先存一份原始函式的參照，供那幾個測試
# 用 monkeypatch.setattr 換回來。
_REAL_GET_STATUS = availability.get_status


@pytest.fixture(autouse=True)
def _clear_cache():
    availability._cache.clear()
    yield
    availability._cache.clear()


class _FakeStdout:
    def __init__(self, data: bytes):
        self._data = data

    async def communicate_result(self):
        return self._data, b""


def _make_fake_create_subprocess_exec(stdout: bytes, returncode: int = 0, delay: float = 0.0):
    async def _fake(*args, **kwargs):
        class _FakeProc:
            def __init__(self):
                self.returncode = returncode

            async def communicate(self):
                if delay:
                    await asyncio.sleep(delay)
                return stdout, b""

        return _FakeProc()

    return _fake


async def test_check_claude_logged_in(monkeypatch):
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b'{"loggedIn": true, "subscriptionType": "pro"}'),
    )
    result = await availability._check_claude()
    assert result == {"installed": True, "loggedIn": True, "available": True, "reason": ""}


async def test_check_claude_not_logged_in(monkeypatch):
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b'{"loggedIn": false}'),
    )
    result = await availability._check_claude()
    assert result == {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"}


async def test_check_claude_not_installed(monkeypatch):
    async def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(availability.asyncio, "create_subprocess_exec", _raise_not_found)
    result = await availability._check_claude()
    assert result == {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}


async def test_check_claude_unparseable_output_fails_closed(monkeypatch):
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b"not json at all"),
    )
    result = await availability._check_claude()
    assert result["available"] is False
    assert result["reason"] == "unexpected_output"


async def test_check_claude_timeout(monkeypatch):
    async def _hang(*args, **kwargs):
        class _FakeProc:
            returncode = None

            async def communicate(self):
                await asyncio.sleep(999)

        return _FakeProc()

    monkeypatch.setattr(availability.asyncio, "create_subprocess_exec", _hang)
    monkeypatch.setattr(availability, "CHECK_TIMEOUT", 0.01)
    monkeypatch.setattr(availability, "safe_kill_process", lambda proc: None)
    result = await availability._check_claude()
    assert result == {"installed": True, "loggedIn": False, "available": False, "reason": "check_timeout"}


async def test_check_codex_logged_in(monkeypatch):
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b"Logged in using ChatGPT", returncode=0),
    )
    result = await availability._check_codex()
    assert result == {"installed": True, "loggedIn": True, "available": True, "reason": ""}


async def test_check_codex_not_logged_in_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b"Not logged in", returncode=1),
    )
    result = await availability._check_codex()
    assert result == {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"}


async def test_check_codex_unexpected_output_fails_closed(monkeypatch):
    """Exit code 0 但輸出文字不含 "logged in"——fail closed，不當作已登入。"""
    monkeypatch.setattr(
        availability.asyncio, "create_subprocess_exec",
        _make_fake_create_subprocess_exec(b"some unexpected banner", returncode=0),
    )
    result = await availability._check_codex()
    assert result["available"] is False


async def test_check_codex_not_installed(monkeypatch):
    async def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(availability.asyncio, "create_subprocess_exec", _raise_not_found)
    result = await availability._check_codex()
    assert result == {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"}


async def test_get_status_caches_within_ttl(monkeypatch):
    monkeypatch.setattr(availability, "get_status", _REAL_GET_STATUS)
    call_count = {"n": 0}

    async def _fake_check():
        call_count["n"] += 1
        return {"installed": True, "loggedIn": True, "available": True, "reason": ""}

    monkeypatch.setattr(availability, "_CHECKS", {"claude": _fake_check, "codex": _fake_check})

    await availability.get_status()
    await availability.get_status()
    assert call_count["n"] == 2  # 兩個引擎各自第一次呼叫，之後 cache 命中


async def test_get_status_force_bypasses_cache(monkeypatch):
    monkeypatch.setattr(availability, "get_status", _REAL_GET_STATUS)
    call_count = {"n": 0}

    async def _fake_check():
        call_count["n"] += 1
        return {"installed": True, "loggedIn": True, "available": True, "reason": ""}

    monkeypatch.setattr(availability, "_CHECKS", {"claude": _fake_check, "codex": _fake_check})

    await availability.get_status()
    await availability.get_status(force=True)
    assert call_count["n"] == 4


async def test_get_status_refreshes_after_ttl_expiry(monkeypatch):
    monkeypatch.setattr(availability, "get_status", _REAL_GET_STATUS)
    call_count = {"n": 0}

    async def _fake_check():
        call_count["n"] += 1
        return {"installed": True, "loggedIn": True, "available": True, "reason": ""}

    monkeypatch.setattr(availability, "_CHECKS", {"claude": _fake_check, "codex": _fake_check})
    monkeypatch.setattr(availability, "CACHE_TTL", -1)  # 立刻視為過期

    await availability.get_status()
    await availability.get_status()
    assert call_count["n"] == 4


async def test_apply_availability_fallback_preferred_available_is_noop(monkeypatch):
    async def _both_available(force=False):
        return {
            "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            "codex": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
        }

    monkeypatch.setattr(availability, "get_status", _both_available)
    name, notice = await availability.apply_availability_fallback("claude")
    assert name == "claude"
    assert notice is None


async def test_apply_availability_fallback_switches_to_other(monkeypatch):
    async def _only_claude_available(force=False):
        return {
            "claude": {"installed": True, "loggedIn": True, "available": True, "reason": ""},
            "codex": {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"},
        }

    monkeypatch.setattr(availability, "get_status", _only_claude_available)
    name, notice = await availability.apply_availability_fallback("codex")
    assert name == "claude"
    assert notice is not None
    assert "Codex" in notice or "codex" in notice.lower()
    assert "Claude" in notice


async def test_apply_availability_fallback_neither_available_raises(monkeypatch):
    async def _neither_available(force=False):
        return {
            "claude": {"installed": False, "loggedIn": False, "available": False, "reason": "not_installed"},
            "codex": {"installed": True, "loggedIn": False, "available": False, "reason": "not_logged_in"},
        }

    monkeypatch.setattr(availability, "get_status", _neither_available)
    with pytest.raises(availability.NoEngineAvailableError):
        await availability.apply_availability_fallback("claude")
