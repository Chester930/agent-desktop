"""Pool of persistent ClaudeSDKClient connections, keyed by session key.

Replaces "spawn a new `claude` subprocess every turn + --resume" with one
long-lived subprocess per key, reused across turns via query()/receive_response().
Idle connections are evicted after a timeout so memory doesn't grow unbounded.
"""
import asyncio
import time

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

DEFAULT_IDLE_TIMEOUT = 30 * 60  # 30 minutes


class SessionPool:
    def __init__(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT):
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._touched: dict[str, float] = {}
        self._idle_timeout = idle_timeout
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()
        # 健檢第二輪修復：query()/receive_response() 在 get_or_create() 的鎖範圍
        # 之外執行（可能跑很久），用計數器標記「這個 key 目前正被使用」，讓
        # evict/prune_idle 不會在一個長 turn 進行中把它斷線。呼叫端應在查詢完
        # 成後（無論成功或失敗）呼叫 release()。
        self._busy: dict[str, int] = {}

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._key_locks_guard:
            lock = self._key_locks.setdefault(key, asyncio.Lock())
        return lock

    async def get_or_create(self, key: str, options: ClaudeAgentOptions) -> ClaudeSDKClient:
        # 每個 key 各自的 lock，避免不同 agent/session 的 connect() 互相卡住
        # （team parallel 模式下多個成員會同時建立各自的連線，共用一把鎖會讓「並行」退化成序列）
        lock = await self._lock_for(key)
        async with lock:
            client = self._clients.get(key)
            if client is None:
                client = ClaudeSDKClient(options=options)
                await client.connect()
                self._clients[key] = client
            self._touched[key] = time.time()
            self._busy[key] = self._busy.get(key, 0) + 1
            return client

    def release(self, key: str) -> None:
        """呼叫端查詢完成後呼叫（成功或失敗都要），標記這個 key 不再忙碌，
        並刷新 last-used 時間，避免長 turn 結束當下立刻被判定為 idle。"""
        self._touched[key] = time.time()
        if key in self._busy:
            self._busy[key] = max(0, self._busy[key] - 1)

    def has(self, key: str) -> bool:
        return key in self._clients

    def keys(self) -> list[str]:
        return list(self._clients.keys())

    async def evict(self, key: str, force: bool = False) -> bool:
        # 健檢第二輪修復：原本不拿 per-key lock 就直接 pop+disconnect，會跟
        # get_or_create() 的建立流程互踩；且原本把 lock 物件從 _key_locks 整個
        # 刪掉，若當下有其他 coroutine 正拿著同一把鎖，之後的呼叫端會拿到「新的
        # 另一把鎖」，鎖的互斥保證就整個失效了 —— 所以鎖物件本身永遠不刪除，
        # 只清 _clients/_touched/_busy。
        # force=True（app 關閉時的 evict_all）無條件斷線，不管是否還在使用中。
        # 回傳是否真的斷線了（busy 而被跳過時回傳 False），讓 prune_idle 的
        # 計數能反映實際狀況，而不是「被判定為 idle 的候選數」。
        lock = await self._lock_for(key)
        async with lock:
            if not force and self._busy.get(key, 0) > 0:
                return False  # 有 in-flight 查詢正在用這個 client，先別斷線
            client = self._clients.pop(key, None)
            self._touched.pop(key, None)
            self._busy.pop(key, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        return True

    async def prune_idle(self) -> int:
        now = time.time()
        stale = [k for k, t in self._touched.items() if now - t > self._idle_timeout]
        return sum([await self.evict(k) for k in stale])

    async def evict_all(self) -> None:
        for k in list(self._clients.keys()):
            await self.evict(k, force=True)

    def __len__(self) -> int:
        return len(self._clients)


async def run_idle_pruner(pool: SessionPool, interval: float = 300.0) -> None:
    """Background task: periodically evict connections idle past the timeout."""
    while True:
        await asyncio.sleep(interval)
        try:
            await pool.prune_idle()
        except Exception:
            pass
