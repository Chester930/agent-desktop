import json
import sqlite3
import asyncio
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

def update_paths(value: Path):
    global CLAUDE_HOME, AGENTS_DIR, SKILLS_DIR, TEAMS_DIR, SESSIONS_DIR, SCHEDULES_FILE, SESSION_NAMES_FILE, SOUL_FILE, SOULS_DIR
    CLAUDE_HOME = value
    AGENTS_DIR = value / "agents"
    SKILLS_DIR = value / "skills"
    TEAMS_DIR = value / "teams"
    SESSIONS_DIR = value / "sessions"
    SCHEDULES_FILE = value / "schedules.json"
    SESSION_NAMES_FILE = value / "session_names.json"
    SOUL_FILE = value / "soul.md"
    SOULS_DIR = value / "souls"

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

def _find_session_file(sid: str) -> Path | None:
    """Find a session .jsonl file by ID; check DB path first, then scan."""
    try:
        with _db() as c:
            row = c.execute("SELECT file_path FROM sessions WHERE id=?", (sid,)).fetchone()
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
    path.write_text(content, encoding="utf-8")

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

# ── SQLite session index ──────────────────────────────────────────────────────
_INDEX_DB = CLAUDE_HOME / "claude-desktop-index.db"

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_INDEX_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def _init_db() -> None:
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
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
        """)

def _migrate_db() -> None:
    """Add missing columns introduced in newer schema versions."""
    with _db() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
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
    with _db() as c:
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
        with _db() as c:
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

def _sync_index() -> None:
    """Incrementally sync ALL project JSONL files into SQLite; remove orphaned rows."""
    all_files = _all_session_files()
    if not all_files:
        return
    try:
        with _db() as c:
            indexed = {r["id"]: (r["mtime"], r["project_path"]) for r in c.execute("SELECT id, mtime, project_path FROM sessions")}
            existing_ids: set[str] = set()
            for f in all_files:
                sid = f.stem
                existing_ids.add(sid)
                mtime = f.stat().st_mtime
                entry = indexed.get(sid)
                if entry and entry[0] == mtime and entry[1]:  # same mtime AND has project_path
                    continue
                title, search_text, inp, out, msg_count = _parse_jsonl_session(f)
                project_path = _read_session_cwd(f)
                c.execute("""
                    INSERT INTO sessions(id, title, mtime, search_text, input_tokens, output_tokens, message_count, file_path, project_path)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title, mtime=excluded.mtime,
                        search_text=excluded.search_text,
                        input_tokens=excluded.input_tokens,
                        output_tokens=excluded.output_tokens,
                        message_count=excluded.message_count,
                        file_path=excluded.file_path,
                        project_path=excluded.project_path
                """, (sid, title, mtime, search_text, inp, out, msg_count, str(f), project_path))
                c.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
                c.execute("INSERT INTO sessions_fts(id, title, search_text) VALUES(?,?,?)",
                          (sid, title, search_text))
            # remove orphaned rows
            for sid in set(indexed) - existing_ids:
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
    LOCAL_MCP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_MCP_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def _analyze_mcp_entry(name: str) -> dict:
    """Read ~/.claude/claude.json and return type + metadata for one MCP."""
    config_path = CLAUDE_HOME / "claude.json"
    entry: dict = {}
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            entry = raw.get("mcpServers", {}).get(name, {})
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
