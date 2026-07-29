import json
import os
import sqlite3
import asyncio
import time
from datetime import datetime
from pathlib import Path

_DEFAULT_CLAUDE_HOME = Path.home() / ".claude"
CONFIG_FILE = _DEFAULT_CLAUDE_HOME / "claude-desktop-config.json"

def _resolve_claude_home() -> Path:
    """Allow users to override ~/.claude via claudeHome in config."""
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("claudeHome", "").strip()
        if raw:
            p = Path(raw).expanduser()
            if p.is_dir():
                return p
    except Exception:
        pass
    return _DEFAULT_CLAUDE_HOME

CLAUDE_HOME  = _resolve_claude_home()
AGENTS_DIR   = CLAUDE_HOME / "agents"
SKILLS_DIR   = CLAUDE_HOME / "skills"
TEAMS_DIR    = CLAUDE_HOME / "teams"
SESSIONS_DIR = CLAUDE_HOME / "sessions"   # legacy fallback (unused for real sessions)
SCHEDULES_FILE     = CLAUDE_HOME / "schedules.json"
SESSION_NAMES_FILE = CLAUDE_HOME / "session_names.json"
SOUL_FILE          = CLAUDE_HOME / "soul.md"
SOULS_DIR          = CLAUDE_HOME / "souls"


def _resolve_registry_home(default: Path) -> Path:
    """Agent/Skill 的單一來源（registry）預設等於 claudeHome（向後相容、零成本
    升級：既有使用者的 Agent/Skill 位置完全不變）。使用者若不想讓資源綁在
    Claude Code 的原生目錄底下（例如只用 Codex），可以透過設定裡的
    registryHome 指到獨立路徑；此時 Claude 端會改由 resource sync 產生鏡像
    副本，而不是直接讀寫同一份檔案。

    容器部署（docker-compose）沒有桌面版的設定檔 UI 可以填 registryHome，
    所以額外支援 REGISTRY_HOME 環境變數覆寫——跟 routes/resource_sync.py
    讀取 CODEX_RESOURCE_HOME／CODEX_SKILLS_HOME 環境變數走的是同一套模式。
    環境變數優先於設定檔，設定檔優先於預設值。"""
    env_raw = os.environ.get("REGISTRY_HOME", "").strip()
    if env_raw:
        p = Path(env_raw).expanduser()
        if p.is_dir():
            return p
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("registryHome", "").strip()
        if raw:
            p = Path(raw).expanduser()
            if p.is_dir():
                return p
    except Exception:
        pass
    return default

REGISTRY_HOME       = _resolve_registry_home(CLAUDE_HOME)
REGISTRY_AGENTS_DIR = REGISTRY_HOME / "agents"
REGISTRY_SKILLS_DIR = REGISTRY_HOME / "skills"

def update_paths(value: Path):
    global CLAUDE_HOME, AGENTS_DIR, SKILLS_DIR, TEAMS_DIR, SESSIONS_DIR, SCHEDULES_FILE, SESSION_NAMES_FILE, SOUL_FILE, SOULS_DIR, LOCAL_MCP_CONFIG_FILE, MCP_SERVERS_FILE, _INDEX_DB, REGISTRY_HOME, REGISTRY_AGENTS_DIR, REGISTRY_SKILLS_DIR
    CLAUDE_HOME = value
    AGENTS_DIR = value / "agents"
    SKILLS_DIR = value / "skills"
    TEAMS_DIR = value / "teams"
    SESSIONS_DIR = value / "sessions"
    SCHEDULES_FILE = value / "schedules.json"
    SESSION_NAMES_FILE = value / "session_names.json"
    SOUL_FILE = value / "soul.md"
    SOULS_DIR = value / "souls"
    LOCAL_MCP_CONFIG_FILE = value / "claude-desktop-local-mcps.json"
    MCP_SERVERS_FILE = value / "claude-desktop-mcp-servers.json"
    _INDEX_DB = value / "claude-desktop-index.db"
    REGISTRY_HOME = _resolve_registry_home(CLAUDE_HOME)
    REGISTRY_AGENTS_DIR = REGISTRY_HOME / "agents"
    REGISTRY_SKILLS_DIR = REGISTRY_HOME / "skills"

def _safe_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子寫入檔案，避免寫入中途崩潰時造成設定檔損毀"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        tmp_path.write_text(content, encoding=encoding)
        os.replace(tmp_path, path)
    except Exception:
        try:
            path.write_text(content, encoding=encoding)
        except Exception:
            pass
    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass


def _all_session_files() -> list[Path]:
    """Yield all *.jsonl session files across every project slug directory."""
    projects = CLAUDE_HOME / "projects"
    files: list[Path] = []
    if not projects.exists():
        return files
    for slug_dir in projects.iterdir():
        if slug_dir.is_dir():
            files.extend(slug_dir.glob("*.jsonl"))
    return files

def _codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".codex"

def _all_codex_session_files() -> list[Path]:
    """Yield Codex CLI session JSONL files without touching its private SQLite DBs."""
    sessions_dir = _codex_home() / "sessions"
    if not sessions_dir.exists():
        return []
    return [p for p in sessions_dir.rglob("*.jsonl") if p.is_file()]

def _codex_session_id_from_path(f: Path) -> str:
    stem = f.stem
    prefix = "rollout-"
    if stem.startswith(prefix):
        parts = stem.split("-")
        if len(parts) >= 8:
            return "-".join(parts[-5:])
    return stem

def _load_codex_session_index() -> dict[str, dict]:
    index_file = _codex_home() / "session_index.jsonl"
    result: dict[str, dict] = {}
    if not index_file.exists():
        return result
    try:
        for line in index_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            sid = str(row.get("id") or row.get("session_id") or "").strip()
            if sid:
                result[sid] = row
    except Exception:
        pass
    return result

def _parse_iso_timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0.0

def _find_session_file(sid: str) -> Path | None:
    """Find a session .jsonl file by ID; check DB path first, then scan."""
    try:
        with _db_ctx() as c:
            row = c.execute("SELECT file_path FROM sessions WHERE id=? AND engine='claude'", (sid,)).fetchone()
            if row and row["file_path"]:
                p = Path(row["file_path"])
                if p.exists():
                    return p
    except Exception:
        pass
    # fallback: scan all project dirs
    for f in _all_session_files():
        if f.stem == sid:
            return f
    return None

def _find_session_record(sid: str) -> tuple[Path, str] | None:
    """Find a session file and its owning engine."""
    try:
        with _db_ctx() as c:
            row = c.execute("SELECT file_path, engine FROM sessions WHERE id=?", (sid,)).fetchone()
            if row and row["file_path"]:
                p = Path(row["file_path"])
                if p.exists():
                    return p, row["engine"] or "claude"
    except Exception:
        pass
    f = _find_session_file(sid)
    if f:
        return f, "claude"
    for f in _all_codex_session_files():
        if _codex_session_id_from_path(f) == sid:
            return f, "codex"
    return None



def _memory_dir() -> Path:
    """Return ~/.claude/projects/<slug>/memory/ for the configured projectDir.
    Falls back to ~/.claude/memory/ when no projectDir is set."""
    proj_dir = _load_config().get("projectDir", "").strip()
    if proj_dir:
        return CLAUDE_HOME / "projects" / _encode_slug(proj_dir) / "memory"
    return CLAUDE_HOME / "memory"

def _encode_slug(dir_path: str) -> str:
    """Convert a filesystem path to the Claude Code project slug format."""
    return dir_path.replace(":", "-").replace("\\", "-").replace("/", "-")

def _global_memory_dir() -> Path:
    """~/.claude/memory/ — 全域公共記憶根目錄"""
    return CLAUDE_HOME / "memory"

def _agent_memory_dir(agent_id: str) -> Path:
    """~/.claude/memory/agents/<id>/"""
    return CLAUDE_HOME / "memory" / "agents" / agent_id

def _team_memory_dir(team_id: str) -> Path:
    """~/.claude/memory/teams/<id>/"""
    return CLAUDE_HOME / "memory" / "teams" / team_id

def _read_md(path: Path) -> str | None:
    """讀取 markdown 檔案，不存在回傳 None，限制 2000 字元。"""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content[:2000] if len(content) > 2000 else content
    except Exception:
        return None

def _write_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_write_text(path, content)

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_config(cfg: dict) -> None:
    _safe_write_text(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))

_VALID_ENGINE_MODES = frozenset({"claude", "codex", "both"})

def get_engine_mode() -> str:
    """權威值，決定 agent 自己的 engine: frontmatter 覆寫在執行期是否生效：
    'claude'/'codex' 表示鎖定單一引擎（agent 覆寫完全被忽略）；'both'（含缺值/
    未知值）表示維持既有行為，agent frontmatter 照舊優先於全域預設。
    預設 'both'——今天 3 個引擎解析呼叫點的實際行為就是「agent 覆寫無條件
    生效」，等於現狀已經是 'both' 語意，改成別的預設值會是真正的行為倒退。"""
    mode = _load_config().get("engineMode", "both")
    return mode if mode in _VALID_ENGINE_MODES else "both"

# ── SQLite session index ──────────────────────────────────────────────────────
_INDEX_DB = CLAUDE_HOME / "claude-desktop-index.db"

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_INDEX_DB))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
    except Exception:
        # Don't leak the handle if setup fails (e.g. corrupted/non-sqlite file) —
        # an open handle can block a subsequent unlink()+rebuild, especially on Windows.
        conn.close()
        raise


import contextlib as _contextlib

@_contextlib.contextmanager
def _db_ctx():
    """
    健檢第二輪修復：原本各處都直接對 _db() 的回傳值開 with 區塊，用的是
    sqlite3.Connection 自己的 context manager —— 那個只會 commit/rollback，
    不會 close()。每個呼叫點都會洩漏一個 WAL 連線 handle，長期執行下來
    handle 數量會持續增加；在 Windows 上洩漏的 handle 還可能擋住之後的
    檔案操作（例如 _init_db 損毀重建時的 unlink）。用法完全相同，只是額外
    保證離開時一定 close()。
    """
    conn = _db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    engine        TEXT NOT NULL DEFAULT 'claude',
    title         TEXT NOT NULL DEFAULT '',
    mtime         REAL NOT NULL DEFAULT 0,
    search_text   TEXT NOT NULL DEFAULT '',
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    file_path     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sess_mtime ON sessions(mtime DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    id UNINDEXED, title, search_text,
    tokenize='trigram'
);
-- add column if upgrading from previous schema without it
CREATE TABLE IF NOT EXISTS _schema_ver (ver INTEGER PRIMARY KEY);
"""

# Substrings that reliably indicate the DB file itself is corrupted (as opposed
# to a transient OperationalError like "database is locked"/"disk I/O error").
# sqlite3.OperationalError is a subclass of sqlite3.DatabaseError, so we can't
# rely on the exception class alone to tell corruption apart from transient
# failures — a locked/busy DB should surface the error, not get deleted.
_CORRUPTION_MARKERS = ("not a database", "malformed", "corrupt")

def _init_db() -> None:
    def _apply_schema() -> None:
        # Explicitly close the connection (the `with conn:` context manager only
        # commits/rolls back, it does not close) so the file handle is released
        # before a caller-side unlink+rebuild attempt (matters on Windows, where
        # an open handle blocks file deletion).
        conn = _db()
        try:
            with conn:
                conn.executescript(_SCHEMA_SQL)
        finally:
            conn.close()

    try:
        _apply_schema()
    except sqlite3.DatabaseError as e:
        if not any(marker in str(e).lower() for marker in _CORRUPTION_MARKERS):
            # Transient error (locked, busy, I/O) — don't destroy the user's
            # session index over it, let the caller see the real failure.
            print(f"[sqlite] database init error (non-corruption, not rebuilding): {e}", flush=True)
            raise
        print(f"[sqlite] database appears corrupted: {e}. Rebuilding brand new database...", flush=True)
        try:
            _INDEX_DB.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _apply_schema()
        except Exception as rebuild_err:
            print(f"[sqlite] rebuild failed: {rebuild_err}", flush=True)
            raise

def _migrate_db() -> None:
    """Add missing columns introduced in newer schema versions."""
    with _db_ctx() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        if "engine" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN engine TEXT NOT NULL DEFAULT 'claude'")
        if "message_count" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0")
        if "file_path" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN file_path TEXT NOT NULL DEFAULT ''")
        if "project_path" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN project_path TEXT NOT NULL DEFAULT ''")

def _migrate_fts_tokenizer() -> None:
    """
    unicode61 doesn't segment CJK text into searchable words, so Chinese
    queries match inconsistently (some substrings happen to land on a
    token boundary elsewhere in the indexed text, most don't). trigram
    indexes overlapping 3-char n-grams instead, which works for both
    CJK and Latin text without an external segmenter.
    """
    with _db_ctx() as c:
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='sessions_fts'"
        ).fetchone()
        if row and "trigram" not in row[0]:
            c.executescript("""
                DROP TABLE sessions_fts;
                CREATE VIRTUAL TABLE sessions_fts USING fts5(
                    id UNINDEXED, title, search_text,
                    tokenize='trigram'
                );
                INSERT INTO sessions_fts(id, title, search_text)
                    SELECT id, title, search_text FROM sessions;
            """)

def _backfill_project_paths() -> None:
    """One-time: populate project_path for sessions that still have empty value."""
    try:
        with _db_ctx() as c:
            rows = c.execute(
                "SELECT id, file_path FROM sessions WHERE project_path = '' AND file_path != ''"
            ).fetchall()
            updated = 0
            for row in rows:
                cwd = _read_session_cwd(Path(row["file_path"]))
                if cwd:
                    c.execute("UPDATE sessions SET project_path = ? WHERE id = ?", (cwd, row["id"]))
                    updated += 1
            if updated:
                print(f"[sqlite] backfilled project_path for {updated} sessions")
    except Exception as e:
        print(f"[sqlite] backfill error: {e}")

def _read_session_cwd(f: Path) -> str:
    """Read the first cwd value found in a session JSONL (scans at most 20 lines)."""
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 20:
                    break
                try:
                    ev = json.loads(line)
                    cwd = ev.get("cwd", "")
                    if cwd:
                        return cwd
                except Exception:
                    continue
    except Exception:
        pass
    return ""

def _parse_jsonl_session(f: Path) -> tuple[str, str, int, int, int]:
    """Parse a JSONL session file — returns (title, search_text, inp_tok, out_tok, msg_count)."""
    title = f.stem
    parts: list[str] = []
    inp = out = msg_count = 0
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        got_title = False
        for line in lines:
            try:
                ev = json.loads(line)
                t = ev.get("type", "")
                if t in ("user", "assistant"):
                    msg_count += 1
                    content = ev.get("message", {}).get("content", "")
                    text = ""
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "text":
                                text = c["text"]; break
                    elif isinstance(content, str):
                        text = content
                    if text:
                        if not got_title and t == "user":
                            title = text[:80]
                            got_title = True
                        parts.append(text[:200])
                elif t == "result":
                    u = ev.get("usage", {})
                    inp += u.get("input_tokens", 0)
                    out += u.get("output_tokens", 0)
            except Exception:
                pass
    except Exception:
        pass
    return title, " ".join(parts)[:2000], inp, out, msg_count

def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            if block.get("type") in ("input_text", "output_text", "text") and isinstance(block.get("content"), str):
                parts.append(block["content"])
        return "\n".join(parts)
    return ""

def _parse_codex_session(f: Path, index_row: dict | None = None) -> tuple[str, str, int, int, int, str, float]:
    """Parse Codex session JSONL into the shared session index shape."""
    sid = _codex_session_id_from_path(f)
    title = str((index_row or {}).get("thread_name") or "").strip() or sid
    updated_at = _parse_iso_timestamp(str((index_row or {}).get("updated_at") or ""))
    project_path = ""
    parts: list[str] = []
    msg_count = 0
    got_title = bool(title and title != sid)
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "session_meta":
                    payload = ev.get("payload", {})
                    if isinstance(payload, dict):
                        sid = str(payload.get("session_id") or payload.get("id") or sid)
                        project_path = str(payload.get("cwd") or project_path)
                        updated_at = updated_at or _parse_iso_timestamp(str(payload.get("timestamp") or ""))
                    continue
                if ev.get("type") != "response_item":
                    continue
                payload = ev.get("payload", {})
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = _message_text(payload.get("content", ""))
                if text:
                    msg_count += 1
                    if not got_title and role == "user":
                        title = text[:80]
                        got_title = True
                    parts.append(text[:200])
    except Exception:
        pass
    return title, " ".join(parts)[:2000], 0, 0, msg_count, project_path, updated_at or f.stat().st_mtime

_last_sync_ts = 0.0
_SYNC_MIN_INTERVAL_SEC = 1.5  # 見下方註解

def _sync_index(force: bool = False) -> None:
    """Incrementally sync Claude and Codex JSONL histories into SQLite.

    這支函式在每次 GET /api/sessions、/api/stats 都會被呼叫一次，單次
    reload() 前端就會連續觸發兩者（見 app.ts reload()），且使用中的對話
    jsonl 檔案每則訊息都會改變 mtime，導致整份逐漸變大的檔案被重新讀取
    解析——對話越長，側欄「顯示延遲」越明顯。這裡用極短的 TTL 把短時間內
    的重複呼叫合併成一次，session 清單本來就不需要次毫秒等級的即時性。
    """
    global _last_sync_ts
    now = time.monotonic()
    if not force and now - _last_sync_ts < _SYNC_MIN_INTERVAL_SEC:
        return
    _last_sync_ts = now
    claude_files = _all_session_files()
    codex_files = _all_codex_session_files()
    if not claude_files and not codex_files:
        return
    try:
        with _db_ctx() as c:
            indexed = {
                (r["engine"] or "claude", r["id"]): (r["mtime"], r["project_path"])
                for r in c.execute("SELECT id, engine, mtime, project_path FROM sessions")
            }
            existing_keys: set[tuple[str, str]] = set()
            for f in claude_files:
                sid = f.stem
                existing_keys.add(("claude", sid))
                mtime = f.stat().st_mtime
                entry = indexed.get(("claude", sid))
                if entry and entry[0] == mtime and entry[1]:  # same mtime AND has project_path
                    continue
                title, search_text, inp, out, msg_count = _parse_jsonl_session(f)
                project_path = _read_session_cwd(f)
                c.execute("""
                    INSERT INTO sessions(id, engine, title, mtime, search_text, input_tokens, output_tokens, message_count, file_path, project_path)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        engine=excluded.engine,
                        title=excluded.title, mtime=excluded.mtime,
                        search_text=excluded.search_text,
                        input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,
                        message_count=excluded.message_count,
                        file_path=excluded.file_path,
                        project_path=excluded.project_path
                """, (sid, "claude", title, mtime, search_text, inp, out, msg_count, str(f), project_path))
                c.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
                c.execute("INSERT INTO sessions_fts(id, title, search_text) VALUES(?,?,?)",
                          (sid, title, search_text))
            codex_index = _load_codex_session_index()
            for f in codex_files:
                sid = _codex_session_id_from_path(f)
                existing_keys.add(("codex", sid))
                title, search_text, inp, out, msg_count, project_path, mtime = _parse_codex_session(f, codex_index.get(sid))
                entry = indexed.get(("codex", sid))
                if entry and entry[0] == mtime and entry[1] == project_path:
                    continue
                c.execute("""
                    INSERT INTO sessions(id, engine, title, mtime, search_text, input_tokens, output_tokens, message_count, file_path, project_path)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        engine=excluded.engine,
                        title=excluded.title, mtime=excluded.mtime,
                        search_text=excluded.search_text,
                        input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,
                        message_count=excluded.message_count,
                        file_path=excluded.file_path,
                        project_path=excluded.project_path
                """, (sid, "codex", title, mtime, search_text, inp, out, msg_count, str(f), project_path))
                c.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
                c.execute("INSERT INTO sessions_fts(id, title, search_text) VALUES(?,?,?)",
                          (sid, title, search_text))
            # remove orphaned rows
            for engine, sid in set(indexed) - existing_keys:
                c.execute("DELETE FROM sessions WHERE id=?", (sid,))
                c.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
    except Exception as e:
        print(f"[sqlite] sync error: {e}")

# Usage API 快取（5 分鐘）
_usage_cache: dict = {"data": None, "expires": 0.0}

# Local MCP config (Docker metadata, compose paths, etc.)
LOCAL_MCP_CONFIG_FILE = CLAUDE_HOME / "claude-desktop-local-mcps.json"

def _load_local_mcp_cfg() -> dict:
    if LOCAL_MCP_CONFIG_FILE.exists():
        try:
            return json.loads(LOCAL_MCP_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_local_mcp_cfg(cfg: dict) -> None:
    _safe_write_text(LOCAL_MCP_CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))

# App 自己的 MCP server 定義單一來源——跟上面的 LOCAL_MCP_CONFIG_FILE
# （Docker/compose 執行期 metadata：container 名稱、port 等）是兩回事。
# 這裡存的是 server 本身的定義（command/args/env 或 url/headers），
# 用來同步到 Claude／Codex 兩邊 CLI 各自的原生設定（backend/mcp_sync.py）。
MCP_SERVERS_FILE = CLAUDE_HOME / "claude-desktop-mcp-servers.json"

def _load_mcp_servers() -> dict:
    if MCP_SERVERS_FILE.exists():
        try:
            return json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_mcp_servers(servers: dict) -> None:
    _safe_write_text(MCP_SERVERS_FILE, json.dumps(servers, ensure_ascii=False, indent=2))

def _analyze_mcp_entry(name: str) -> dict:
    """Read ~/.claude.json and return type + metadata for one MCP.

    `claude mcp add` 的預設 scope 是 "local"，設定會寫進
    projects[<cwd>].mcpServers，不是頂層的 mcpServers（那個只有明確用
    `-s user` 加的項目才會出現）。這個 app 呼叫 CLI 一律用 Path.home()
    當 cwd（見 handle_cli），查詢要用同一把 key 才對得上；沒對上的話
    docker/stdio 偵測會整個落空，被誤判成 external。local scope 優先於
    user scope，跟 Claude CLI 本身「更精確的 scope 蓋掉較廣的」邏輯一致。
    """
    config_path = CLAUDE_HOME.parent / ".claude.json"
    entry: dict = {}
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            project_key = str(Path.home())
            local_entry = raw.get("projects", {}).get(project_key, {}).get("mcpServers", {}).get(name)
            entry = local_entry if local_entry is not None else raw.get("mcpServers", {}).get(name, {})
        except Exception:
            pass

    cmd   = entry.get("command", "")
    args  = entry.get("args", [])
    url   = entry.get("url", "")
    etype = entry.get("type", "")      # "stdio" | "http" | "sse"

    # Docker: command is 'docker' OR first arg is 'run'/'start'/'compose'
    is_docker_cmd = (cmd == "docker") or (cmd and args and args[0] in ("run", "start", "compose"))
    is_local_url  = url and (url.startswith("http://localhost") or url.startswith("http://127.0.0.1") or url.startswith("ws://localhost"))

    import re as _re
    port_match = _re.search(r":(\d+)", url)
    port = port_match.group(1) if port_match else None

    # Extract Docker image from args: docker run [opts] IMAGE
    docker_image = None
    if is_docker_cmd and args:
        for i, a in enumerate(args):
            if a in ("-p", "--name", "-e", "--network", "-v", "--rm", "-d", "-it"):
                continue
            if a.startswith("-"):
                continue
            if args[i-1] in ("-p", "--name", "-e", "--network", "-v") if i > 0 else False:
                continue
            # First non-flag arg that isn't a subcommand
            if a not in ("run", "start", "stop", "restart", "exec", "compose", "up", "down"):
                docker_image = a
                break

    if is_docker_cmd:
        return {"mcpType": "docker", "command": cmd, "args": args, "dockerImage": docker_image, "port": port}
    elif etype == "stdio" or (cmd and not url):
        return {"mcpType": "stdio", "command": cmd, "args": args, "port": port}
    elif is_local_url or etype == "http":
        return {"mcpType": "local-http", "url": url, "port": port}
    else:
        return {"mcpType": "external", "url": url, "port": port}

_log_buffer: list[str] = []

def _log(msg: str) -> None:
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    _log_buffer.append(entry)
    if len(_log_buffer) > 200:
        _log_buffer.pop(0)
    print(entry)
