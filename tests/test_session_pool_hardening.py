"""T24: SessionPool.evict() 原本不拿 per-key lock 就直接斷線，且會把鎖物件整個
刪掉，導致跟 get_or_create() 互踩；且沒有追蹤「這個 key 正在被查詢中」，
run_idle_pruner 可能在一個長 turn 進行中把它斷線。"""
import asyncio
import sys
import types

import pytest

# claude_agent_sdk 是選用依賴（main.py 用 try/except HAS_AGENT_SDK 包住），
# 在沒安裝的測試環境裡直接 import session_pool 會炸；用最小 stub 頂替，
# 反正這裡的測試只驗證 SessionPool 自己的 lock/busy-counter 邏輯，
# 不需要真正的 ClaudeSDKClient 行為。
if "claude_agent_sdk" not in sys.modules:
    stub = types.ModuleType("claude_agent_sdk")
    stub.ClaudeSDKClient = object
    stub.ClaudeAgentOptions = object
    sys.modules["claude_agent_sdk"] = stub

from session_pool import SessionPool


class FakeClient:
    def __init__(self):
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def pool(monkeypatch):
    p = SessionPool(idle_timeout=0.05)
    monkeypatch.setattr("session_pool.ClaudeSDKClient", lambda options=None: FakeClient())
    return p


@pytest.mark.asyncio
async def test_evict_skips_busy_client(pool):
    client = await pool.get_or_create("k1", None)
    await pool.evict("k1")  # not forced — should be a no-op while busy
    assert pool.has("k1") is True
    assert client.disconnected is False


@pytest.mark.asyncio
async def test_release_then_evict_disconnects(pool):
    client = await pool.get_or_create("k1", None)
    pool.release("k1")
    await pool.evict("k1")
    assert pool.has("k1") is False
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_forced_evict_disconnects_even_if_busy(pool):
    client = await pool.get_or_create("k1", None)
    await pool.evict("k1", force=True)
    assert pool.has("k1") is False
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_prune_idle_does_not_evict_busy_long_running_turn(pool):
    """Regression test: a turn that runs longer than idle_timeout must not be
    disconnected out from under itself by the background pruner."""
    client = await pool.get_or_create("k1", None)  # busy=1, idle_timeout=0.05s
    await asyncio.sleep(0.1)  # now "idle" by the timestamp alone, but still busy
    evicted = await pool.prune_idle()
    assert evicted == 0
    assert pool.has("k1") is True
    assert client.disconnected is False


@pytest.mark.asyncio
async def test_prune_idle_evicts_after_release(pool):
    client = await pool.get_or_create("k1", None)
    pool.release("k1")
    await asyncio.sleep(0.1)
    evicted = await pool.prune_idle()
    assert evicted == 1
    assert pool.has("k1") is False
    assert client.disconnected is True


@pytest.mark.asyncio
async def test_lock_identity_preserved_across_evict(pool):
    """evict() must never delete the lock object itself — doing so would let a
    concurrent get_or_create() grab a *different* Lock for the same key,
    silently breaking the mutual-exclusion guarantee."""
    await pool.get_or_create("k1", None)
    lock_before = await pool._lock_for("k1")
    pool.release("k1")
    await pool.evict("k1")
    lock_after = await pool._lock_for("k1")
    assert lock_before is lock_after


@pytest.mark.asyncio
async def test_multiple_concurrent_users_of_same_key_all_tracked_busy(pool):
    await pool.get_or_create("k1", None)
    await pool.get_or_create("k1", None)  # second concurrent user of the same key
    await pool.evict("k1")  # still busy (count=2) — must not disconnect
    assert pool.has("k1") is True
    pool.release("k1")
    await pool.evict("k1")  # still busy (count=1)
    assert pool.has("k1") is True
    pool.release("k1")
    await pool.evict("k1")  # now count=0 — disconnects
    assert pool.has("k1") is False
