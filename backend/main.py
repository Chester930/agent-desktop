import asyncio
import base64
import io
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import aiohttp
from aiohttp import web
import aiohttp_cors

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

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
SESSIONS_DIR = CLAUDE_HOME / "sessions"   # legacy fallback (unused for real sessions)

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

# ── 全域路徑：靈魂/記憶/排程 與專案目錄無關 ─────────────────────────────────
# 與 Claude Code CLI 的 ~/.claude/ 共用同一份 memory/
MEMORY_DIR         = CLAUDE_HOME / "memory"
SCHEDULES_FILE     = CLAUDE_HOME / "schedules.json"
SESSION_NAMES_FILE = CLAUDE_HOME / "session_names.json"
SOUL_FILE          = CLAUDE_HOME / "soul.md"
SOULS_DIR          = CLAUDE_HOME / "souls"

def _encode_slug(dir_path: str) -> str:
    """Convert a filesystem path to the Claude Code project slug format."""
    return dir_path.replace(":", "-").replace("\\", "-").replace("/", "-")

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
            tokenize='unicode61 remove_diacritics 1'
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

_init_db()
_migrate_db()
_backfill_project_paths()

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

# ─────────────────────────────────────────────────────────────────────────────

active_sessions: dict[str, str] = {}   # client_id -> claude session_id
active_procs:    dict[str, asyncio.subprocess.Process] = {}  # client_id -> proc
_mcp_procs:      dict[str, asyncio.subprocess.Process] = {}  # mcp name -> proc
_mcp_logs:       dict[str, list[str]] = {}                   # mcp name -> log lines

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

def load_session_names() -> dict:
    if SESSION_NAMES_FILE.exists():
        try: return json.loads(SESSION_NAMES_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def save_session_names(names: dict) -> None:
    SESSION_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")

def migrate_soul():
    if not SOULS_DIR.exists():
        SOULS_DIR.mkdir(parents=True, exist_ok=True)
        if SOUL_FILE.exists():
            try:
                shutil.copy(SOUL_FILE, SOULS_DIR / "default.md")
            except Exception:
                pass

def get_concatenated_soul() -> str:
    migrate_soul()
    parts = []
    for f in sorted(SOULS_DIR.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## Section: {f.stem}\n{content}")
        except Exception:
            pass
    return "\n\n".join(parts)


def load_schedules() -> list:
    if SCHEDULES_FILE.exists():
        try:
            return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def save_schedules(data: list) -> None:
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Locate claude executable on Windows ───────────────
def find_claude() -> str:
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        # Windows
        Path.home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        Path("C:/Program Files/claude/claude.exe"),
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        # macOS (Homebrew + npm global + direct install)
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
        Path.home() / ".npm-global" / "bin" / "claude",
        Path.home() / "Library" / "Application Support" / "claude" / "claude",
        # Linux
        Path("/usr/bin/claude"),
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".nvm" / "versions" / "node" / "default" / "bin" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "claude"   # fallback, let OS resolve

CLAUDE_BIN = find_claude()


def _resolve_api_key() -> str:
    """Run apiKeyCmd from config and return the trimmed API key, or '' if not set."""
    cfg = _load_config()
    cmd = cfg.get("apiKeyCmd", "").strip()
    if not cmd:
        return ""
    import subprocess
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[apiKeyCmd] error: {e}")
        return ""


async def handle_chat(request: web.Request) -> web.StreamResponse:
    data      = await request.json()
    message      = data.get("message", "")
    client_id    = data.get("client_id", "default")
    agent        = data.get("agent", "")
    cwd_override = data.get("cwd", "")
    bin_override = data.get("claude_bin", "")
    attachments  = data.get("attachments", [])
    model        = data.get("model", "")
    effort       = data.get("effort", "")
    permission_mode = data.get("permission_mode", "")

    claude_bin = bin_override if bin_override else CLAUDE_BIN
    cwd        = cwd_override if (cwd_override and Path(cwd_override).is_dir()) else str(Path.home())

    soul = get_concatenated_soul()
    full_message = f"[System Persona]\n{soul}\n\n{message}" if soul else message

    cmd = [claude_bin, "-p", full_message, "--output-format", "stream-json", "--verbose"]
    if model and model not in ("sonnet", ""):
        cmd += ["--model", model]
    if effort and effort != "medium":
        cmd += ["--effort", effort]
    if permission_mode and permission_mode not in ("default", ""):
        cmd += ["--permission-mode", permission_mode]
    for att in attachments:
        if Path(att).exists():
            cmd += ["--input-file", att]
    if agent:
        agent_file = AGENTS_DIR / f"{agent}.md"
        if agent_file.exists():
            try:
                text = agent_file.read_text(encoding="utf-8")
                body = text
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        body = parts[2].strip()
                if body:
                    full_message = f"[代理人：{agent}]\n{body}\n\n---\n\n{full_message}"
            except Exception:
                pass
    if client_id in active_sessions:
        cmd += ["--resume", active_sessions[client_id]]

    response = web.StreamResponse(headers={
        "Content-Type":    "text/event-stream",
        "Cache-Control":   "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    try:
        env = {**os.environ}
        api_key = _resolve_api_key()
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=env,
        )
        active_procs[client_id] = proc

        async for line in proc.stdout:
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
                if event.get("type") == "result" and "session_id" in event:
                    active_sessions[client_id] = event["session_id"]
                await response.write(f"data: {raw}\n\n".encode())
            except json.JSONDecodeError:
                payload = json.dumps({"type": "text", "text": raw})
                await response.write(f"data: {payload}\n\n".encode())

        await proc.wait()
        active_procs.pop(client_id, None)
        await response.write(b'data: {"type":"done"}\n\n')
    except Exception as e:
        payload = json.dumps({"type": "error", "text": str(e)})
        await response.write(f"data: {payload}\n\n".encode())

    return response


async def handle_sessions(request: web.Request) -> web.Response:
    q      = request.rel_url.query.get("q", "").strip()
    offset = int(request.rel_url.query.get("offset", "0"))
    PAGE   = 30
    _sync_index()
    custom_names = load_session_names()

    def _proj_dir(file_path: str) -> str:
        if not file_path:
            return ""
        parts = Path(file_path).parent.name.split('--')
        return parts[-1] if parts else ""

    try:
        with _db() as c:
            if q:
                rows = c.execute("""
                    SELECT s.id, s.title, s.mtime, s.message_count, s.file_path, s.project_path,
                           snippet(sessions_fts, 2, '<mark>', '</mark>', '…', 12) AS snippet
                    FROM sessions_fts f
                    JOIN sessions s ON s.id = f.id
                    WHERE sessions_fts MATCH ?
                    ORDER BY s.mtime DESC
                """, (q,)).fetchall()
            else:
                rows = c.execute("""
                    SELECT id, title, mtime, message_count, file_path, project_path,
                           substr(search_text, 1, 120) AS snippet
                    FROM sessions ORDER BY mtime DESC
                """).fetchall()
        total = len(rows)
        items = [
            {
                "id":           r["id"],
                "title":        custom_names.get(r["id"]) or r["title"],
                "mtime":        r["mtime"],
                "snippet":      r["snippet"] or "",
                "messageCount": r["message_count"],
                "projectDir":   _proj_dir(r["file_path"]),
                "projectPath":  r["project_path"] or "",
            }
            for r in rows[offset: offset + PAGE]
        ]
    except Exception as e:
        print(f"[sessions] DB error, falling back: {e}")
        items, total = [], 0
    return web.json_response({"items": items, "has_more": total > offset + PAGE})


async def handle_restore(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field  = await reader.next()
    data   = await field.read()
    buf    = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buf) as zf:
            mapping = {
                'soul.md':          SOUL_FILE,
                'schedules.json':   SCHEDULES_FILE,
                'session_names.json': SESSION_NAMES_FILE,
            }
            for arc, dest in mapping.items():
                if arc in zf.namelist():
                    Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    Path(dest).write_bytes(zf.read(arc))
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            for name in zf.namelist():
                if name.startswith('memory/') and name.endswith('.md'):
                    (MEMORY_DIR / Path(name).name).write_bytes(zf.read(name))
    except Exception as e:
        return web.json_response({'error': str(e)}, status=400)
    return web.json_response({'ok': True})


async def handle_soul_reset(request: web.Request) -> web.Response:
    if SOUL_FILE.exists():
        try: SOUL_FILE.unlink()
        except Exception: pass
    if SOULS_DIR.exists():
        for f in SOULS_DIR.glob("*.md"):
            try: f.unlink()
            except Exception: pass
    return web.json_response({'ok': True})


async def handle_backup(request: web.Request) -> web.Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for src, arc in [
            (SOUL_FILE,          'soul.md'),
            (SCHEDULES_FILE,     'schedules.json'),
            (SESSION_NAMES_FILE, 'session_names.json'),
        ]:
            if Path(src).exists():
                zf.write(src, arc)
        if MEMORY_DIR.exists():
            for f in MEMORY_DIR.glob('*.md'):
                zf.write(f, f'memory/{f.name}')
    buf.seek(0)
    return web.Response(
        body=buf.read(),
        content_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="claude-backup.zip"'},
    )


async def handle_session_rename(request: web.Request) -> web.Response:
    sid   = request.match_info["id"]
    data  = await request.json()
    title = data.get("title", "").strip()
    if not title:
        return web.json_response({"error": "empty title"}, status=400)
    names = load_session_names()
    names[sid] = title
    save_session_names(names)
    return web.json_response({"ok": True})


async def handle_chat_stop(request: web.Request) -> web.Response:
    data = await request.json()
    client_id = data.get("client_id", "")
    proc = active_procs.pop(client_id, None)
    if proc:
        try: proc.kill()
        except Exception: pass
    return web.json_response({"ok": True})


async def handle_session_delete(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    f = _find_session_file(sid)
    if f and f.exists():
        f.unlink()
    for k in [k for k, v in active_sessions.items() if v == sid]:
        active_sessions.pop(k, None)
    return web.json_response({"ok": True})


async def handle_resume_session(request: web.Request) -> web.Response:
    data = await request.json()
    client_id = data.get("client_id", "default")
    session_id = data.get("session_id", "")
    active_sessions[client_id] = session_id
    return web.json_response({"ok": True})


async def handle_agents(request: web.Request) -> web.Response:
    agents = []
    if AGENTS_DIR.exists():
        for f in AGENTS_DIR.glob("*.md"):
            try:
                text = f.read_text(encoding="utf-8")
                desc = ""
                name = f.stem
                for line in text.splitlines():
                    if line.startswith("description:"):
                        desc = line.replace("description:", "").strip()
                        break
                agents.append({"id": name, "name": name, "description": desc})
            except Exception:
                pass
    return web.json_response(agents)


import re as _re

def _parse_frontmatter_desc(text: str) -> str:
    """Return the description: value from YAML frontmatter, or '' if absent."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    fm, in_fm = [], False
    for line in lines[1:]:
        if line.strip() == "---":
            in_fm = True
            break
        fm.append(line)
    if not in_fm:
        return ""
    collecting, buf = False, []
    for line in fm:
        if collecting:
            if line.startswith("  ") or line.strip() == "":
                buf.append(line.strip())
            else:
                break
        else:
            m = _re.match(r'^description:\s*(.*)$', line)
            if m:
                val = m.group(1).strip()
                if val in (">", "|"):
                    collecting = True
                else:
                    return val.strip('"\'')
    return " ".join(x for x in buf if x)


def _desc_from_md_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        desc = _parse_frontmatter_desc(text)
        if desc:
            return desc
        # fallback: first non-empty non-heading line
        for line in text.splitlines():
            stripped = line.lstrip("# ").strip()
            if stripped:
                return stripped
    except Exception:
        pass
    return ""


def _desc_from_skill_dir(skill_dir: Path) -> str:
    for candidate in (skill_dir / "SKILL.md", skill_dir / "README.md"):
        if candidate.exists():
            desc = _desc_from_md_file(candidate)
            if desc:
                return desc
    return ""


async def handle_skills(request: web.Request) -> web.Response:
    skills = []
    if not SKILLS_DIR.exists():
        return web.json_response(skills)
    for entry in sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            desc = _desc_from_skill_dir(entry)
            skills.append({"id": entry.name, "name": entry.name, "description": desc})
        elif entry.suffix == ".md":
            name = entry.stem
            desc = _desc_from_md_file(entry)
            skills.append({"id": name, "name": name, "description": desc})
    return web.json_response(skills)



async def handle_memory(request: web.Request) -> web.Response:
    files = {}
    if MEMORY_DIR.exists():
        for f in MEMORY_DIR.glob("*.md"):
            try:
                files[f.stem] = f.read_text(encoding="utf-8")
            except Exception:
                pass
    return web.json_response(files)


UPLOAD_DIR = Path(tempfile.gettempdir()) / "claude_desktop_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

async def handle_upload(request: web.Request) -> web.Response:
    data = await request.json()
    b64  = data.get("data", "")
    name = data.get("name", "upload.bin")
    if not b64:
        return web.json_response({"error": "no data"}, status=400)
    ext  = Path(name).suffix or ".bin"
    dest = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    dest.write_bytes(base64.b64decode(b64))
    return web.json_response({"path": str(dest), "name": name})


async def handle_translate(request: web.Request) -> web.Response:
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"result": ""})

    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": "auto", "tl": "zh-TW", "dt": "t", "q": text}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json(content_type=None)
        result = "".join(seg[0] for seg in data[0] if seg[0])
    except Exception as e:
        result = f"[翻譯失敗：{e}]"

    return web.json_response({"result": result})


async def handle_memory_put(request: web.Request) -> web.Response:
    key  = request.match_info["key"]
    data = await request.json()
    content = data.get("content", "")
    if not key.replace("-", "").replace("_", "").isalnum():
        return web.json_response({"error": "invalid key"}, status=400)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    (MEMORY_DIR / f"{key}.md").write_text(content, encoding="utf-8")
    return web.json_response({"ok": True})

async def handle_memory_delete(request: web.Request) -> web.Response:
    key = request.match_info["key"]
    f = MEMORY_DIR / f"{key}.md"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


async def handle_soul_get(request: web.Request) -> web.Response:
    content = SOUL_FILE.read_text(encoding="utf-8") if SOUL_FILE.exists() else ""
    return web.json_response({"content": content})

async def handle_soul_put(request: web.Request) -> web.Response:
    data = await request.json()
    content = data.get("content", "")
    SOUL_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOUL_FILE.write_text(content, encoding="utf-8")
    return web.json_response({"ok": True})

async def handle_souls_list(request: web.Request) -> web.Response:
    migrate_soul()
    souls = []
    for f in sorted(SOULS_DIR.glob("*.md")):
        try:
            content = f.read_text(encoding="utf-8")
            souls.append({"id": f.stem, "name": f.stem, "content": content})
        except Exception:
            pass
    return web.json_response(souls)

async def handle_soul_save(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    if sid.lower().endswith(".md"):
        sid = sid[:-3]
    # Check alphanumeric with hyphens/underscores
    if not sid.replace("-", "").replace("_", "").isalnum():
        return web.json_response({"error": "invalid name"}, status=400)
    data = await request.json()
    content = data.get("content", "")
    SOULS_DIR.mkdir(parents=True, exist_ok=True)
    (SOULS_DIR / f"{sid}.md").write_text(content, encoding="utf-8")
    return web.json_response({"ok": True})

async def handle_soul_delete(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    if sid.lower().endswith(".md"):
        sid = sid[:-3]
    f = SOULS_DIR / f"{sid}.md"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})



async def handle_schedules_get(request: web.Request) -> web.Response:
    return web.json_response(load_schedules())

async def handle_schedules_post(request: web.Request) -> web.Response:
    data = await request.json()
    prompt = data.get("prompt", "").strip()
    cron   = data.get("cron", "").strip()
    if not prompt or not cron:
        return web.json_response({"error": "prompt and cron required"}, status=400)
    schedules = load_schedules()
    entry = {"id": str(uuid.uuid4()), "prompt": prompt, "cron": cron, "enabled": True}
    schedules.append(entry)
    save_schedules(schedules)
    return web.json_response(entry)

async def handle_schedules_delete(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    schedules = [s for s in load_schedules() if s["id"] != sid]
    save_schedules(schedules)
    return web.json_response({"ok": True})

async def handle_schedules_patch(request: web.Request) -> web.Response:
    sid  = request.match_info["id"]
    data = await request.json()
    schedules = load_schedules()
    for sc in schedules:
        if sc["id"] == sid:
            if "enabled" in data:
                sc["enabled"] = data["enabled"]
            break
    save_schedules(schedules)
    return web.json_response({"ok": True})

async def handle_schedules_run(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    schedules = load_schedules()
    target = next((s for s in schedules if s["id"] == sid), None)
    if not target:
        return web.json_response({"error": "not found"}, status=404)
    asyncio.create_task(run_schedule_prompt(target["prompt"]))
    target["last_run"] = datetime.now().isoformat()
    save_schedules(schedules)
    return web.json_response({"ok": True})

async def handle_stats(request: web.Request) -> web.Response:
    """Dashboard 統計 — 用 SQLite index 計算，不重新掃描 JSONL。"""
    from datetime import timedelta
    today = datetime.now().date()
    _sync_index()

    sessions_count = 0
    total_tokens   = 0
    daily_map: dict[str, int] = {}
    active_days_set: set[str] = set()

    try:
        with _db() as c:
            row = c.execute(
                "SELECT COUNT(*) as cnt, SUM(input_tokens+output_tokens) as tok, SUM(message_count) as msgs FROM sessions"
            ).fetchone()
            sessions_count = row["cnt"] or 0
            total_tokens   = row["tok"] or 0
            messages_count = row["msgs"] or 0
            for r in c.execute("SELECT mtime, message_count FROM sessions"):
                d = datetime.fromtimestamp(r["mtime"]).date().isoformat()
                daily_map[d] = daily_map.get(d, 0) + (r["message_count"] or 1)
                active_days_set.add(d)
    except Exception as e:
        print(f"[stats] DB error: {e}")

    # streak calculation
    streak_current = 0
    check = today
    for d in sorted(active_days_set, reverse=True):
        if datetime.fromisoformat(d).date() == check:
            streak_current += 1
            check -= timedelta(days=1)
        else:
            break
    streak_longest = streak_current

    # heatmap: last 91 days (13 weeks)
    heatmap = {}
    for i in range(91):
        d = (today - timedelta(days=i)).isoformat()
        heatmap[d] = daily_map.get(d, 0)

    return web.json_response({
        "sessions":      sessions_count,
        "messages":      messages_count,
        "total_tokens":  total_tokens,
        "active_days":   len(active_days_set),
        "streak_current": streak_current,
        "streak_longest": streak_longest,
        "heatmap":       heatmap,
    })

async def handle_skill_generate(request: web.Request) -> web.Response:
    """POST /api/skills/generate — analyse a session and draft a skill file."""
    data       = await request.json()
    session_id = data.get("session_id", "").strip()
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)
    f = _find_session_file(session_id)
    if not f or not f.exists():
        return web.json_response({"error": "session not found"}, status=404)

    # extract last 20 user messages as context
    snippets: list[str] = []
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
                if ev.get("type") == "user":
                    content = ev.get("message", {}).get("content", "")
                    text = ""
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "text": text = c["text"]; break
                    elif isinstance(content, str):
                        text = content
                    if text: snippets.append(text[:300])
            except Exception:
                pass
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    context = "\n".join(snippets[-20:])
    prompt = (
        "根據以下對話摘錄，生成一個 Claude Code skill 的 Markdown 草稿。"
        "格式：\n---\nname: <slug>\ndescription: <一行說明>\n---\n\n## When to Use\n...\n"
        "## How It Works\n...\n## Example\n...\n\n"
        f"對話摘錄：\n{context}"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", prompt, "--output-format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        result = json.loads(raw.decode("utf-8", errors="replace"))
        skill_md = result.get("result", "") or result.get("content", "")
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    # auto-save to ~/.claude/skills/auto-<session_id[:8]>.md
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"auto-{session_id[:8]}"
    out_path = SKILLS_DIR / f"{slug}.md"
    out_path.write_text(skill_md, encoding="utf-8")
    return web.json_response({"ok": True, "slug": slug, "path": str(out_path), "content": skill_md})


async def handle_logs(request: web.Request) -> web.Response:
    return web.json_response({"logs": _log_buffer[-100:]})

async def handle_files(request: web.Request) -> web.Response:
    raw = request.rel_url.query.get("path", "")
    p = Path(raw) if raw else Path.home()
    if not p.is_dir():
        p = Path.home()
    items = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith('.'):
                continue
            items.append({"name": child.name, "path": str(child), "isDir": child.is_dir()})
    except PermissionError:
        pass
    return web.json_response({"path": str(p), "parent": str(p.parent), "items": items})

async def handle_cli(request: web.Request) -> web.Response:
    data = await request.json()
    args = data.get("args", [])
    if not isinstance(args, list):
        return web.json_response({"error": "args must be list"}, status=400)
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return web.json_response({"output": out.decode("utf-8", errors="replace"), "code": proc.returncode})
    except asyncio.TimeoutError:
        return web.json_response({"output": "[逾時]", "code": -1})
    except Exception as e:
        return web.json_response({"output": str(e), "code": -1})

async def _drain_mcp(name: str, proc: asyncio.subprocess.Process) -> None:
    """Drain stdout+stderr of an MCP process into _mcp_logs[name]."""
    async def read_stream(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip()
            buf = _mcp_logs.setdefault(name, [])
            buf.append(decoded)
            if len(buf) > 300:
                buf.pop(0)
    await asyncio.gather(read_stream(proc.stdout), read_stream(proc.stderr))


async def handle_mcp_logs(request: web.Request) -> web.Response:
    name  = request.match_info["name"]
    lines = list(_mcp_logs.get(name, []))

    # Also try `docker logs --tail 80` if container is configured
    local_cfg = _load_local_mcp_cfg().get(name, {})
    container = local_cfg.get("containerName", "")
    if container and not lines:
        try:
            p = await asyncio.create_subprocess_exec(
                "docker", "logs", "--tail", "80", container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
            lines = out.decode("utf-8", errors="replace").splitlines()
        except Exception:
            pass

    return web.json_response({"name": name, "lines": lines[-100:]})


async def handle_local_mcp_config_get(request: web.Request) -> web.Response:
    """Return all local MCP Docker/compose metadata."""
    return web.json_response(_load_local_mcp_cfg())


async def handle_local_mcp_config_put(request: web.Request) -> web.Response:
    """Save Docker/compose metadata for one MCP server."""
    name = request.match_info["name"]
    data = await request.json()
    cfg  = _load_local_mcp_cfg()
    cfg[name] = {
        "containerName":  data.get("containerName", ""),
        "composeFile":    data.get("composeFile", ""),
        "composeService": data.get("composeService", ""),
        "port":           data.get("port", ""),
        "notes":          data.get("notes", ""),
    }
    _save_local_mcp_cfg(cfg)
    return web.json_response({"ok": True})


async def handle_mcp_info(request: web.Request) -> web.Response:
    """Return type + metadata for one MCP server (reads claude.json)."""
    name = request.match_info["name"]
    info = _analyze_mcp_entry(name)
    local_cfg = _load_local_mcp_cfg().get(name, {})
    return web.json_response({**info, **local_cfg})


async def handle_session_auto_title(request: web.Request) -> web.Response:
    """Generate a concise session title using Claude from the first few messages."""
    sid = request.match_info["id"]
    f = _find_session_file(sid)
    if not f or not f.exists():
        return web.json_response({"error": "session not found"}, status=404)

    # Extract first user+assistant messages
    snippets: list[str] = []
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:30]:
            try:
                ev = json.loads(line)
                role = ev.get("type", "")
                if role not in ("user", "assistant"):
                    continue
                content = ev.get("message", {}).get("content", "")
                text = ""
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "text":
                            text = c["text"]; break
                elif isinstance(content, str):
                    text = content
                if text:
                    snippets.append(f"[{role}] {text[:300]}")
                if len(snippets) >= 4:
                    break
            except Exception:
                pass
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    if not snippets:
        return web.json_response({"error": "no messages"}, status=400)

    prompt = (
        "根據以下對話片段，生成一個精簡的標題（4–10 個中文字或英文字）。"
        "只回覆標題本身，不要標點符號或引號。\n\n"
        + "\n".join(snippets)
    )

    env = os.environ.copy()
    key = _resolve_api_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key

    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", prompt, "--model", "claude-haiku-4-5-20251001",
            "--output-format", "text", "--no-caching",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        title = stdout.decode("utf-8", errors="replace").strip().splitlines()[0][:60]
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    if not title:
        return web.json_response({"error": "empty title"}, status=500)

    # Save to session_names.json
    names = load_session_names()
    names[sid] = title
    SESSION_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also update SQLite
    try:
        with _db() as c:
            c.execute("UPDATE sessions SET title=? WHERE id=?", (title, sid))
            c.execute("UPDATE sessions_fts SET title=? WHERE id=?", (title, sid))
    except Exception:
        pass

    return web.json_response({"ok": True, "title": title})


async def handle_mcp_action(request: web.Request) -> web.Response:
    name   = request.match_info["name"]
    action = request.match_info["action"]   # start | stop | restart

    mcp_info    = _analyze_mcp_entry(name)
    local_cfg   = _load_local_mcp_cfg().get(name, {})
    container   = local_cfg.get("containerName", "")
    compose_f   = local_cfg.get("composeFile", "")
    compose_svc = local_cfg.get("composeService", "")
    is_docker   = mcp_info["mcpType"] == "docker" or bool(container) or bool(compose_f)

    # ── STOP / RESTART phase 1: shut down ────────────────────────────────────
    if action in ("stop", "restart"):
        if compose_f:
            svc_args = [compose_svc] if compose_svc else []
            p = await asyncio.create_subprocess_exec(
                "docker", "compose", "-f", compose_f, "stop", *svc_args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await p.communicate()
        elif container:
            p = await asyncio.create_subprocess_exec(
                "docker", "stop", container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await p.communicate()
        else:
            proc = _mcp_procs.pop(name, None)
            if proc:
                try: proc.kill()
                except Exception: pass

    # ── START / RESTART phase 2: launch ──────────────────────────────────────
    if action in ("start", "restart"):
        try:
            if compose_f:
                svc_args = [compose_svc] if compose_svc else []
                proc = await asyncio.create_subprocess_exec(
                    "docker", "compose", "-f", compose_f, "up", "-d", *svc_args,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            elif container:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "start", container,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            else:
                mcp_cmd = _get_mcp_command(name)
                if not mcp_cmd:
                    return web.json_response({"ok": False, "error": "no command configured"})
                proc = await asyncio.create_subprocess_exec(
                    *mcp_cmd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            _mcp_procs[name] = proc
            _mcp_logs[name] = []
            asyncio.create_task(_drain_mcp(name, proc))
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    running = name in _mcp_procs and _mcp_procs[name].returncode is None
    return web.json_response({
        "ok": True, "name": name, "action": action, "running": running,
        "mcpType": mcp_info["mcpType"],
    })


def _get_mcp_command(name: str) -> list[str] | None:
    """Parse ~/.claude/claude.json to find the stdio command for an MCP server."""
    config_path = CLAUDE_HOME / "claude.json"
    if not config_path.exists():
        return None
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        servers = cfg.get("mcpServers", {})
        entry = servers.get(name, {})
        cmd = entry.get("command")
        args = entry.get("args", [])
        if cmd:
            return [cmd] + args
    except Exception:
        pass
    return None


# ── #17 Profile switching ─────────────────────────────────────────────────────

async def handle_profiles(request: web.Request) -> web.Response:
    """List available project profiles from ~/.claude/projects/."""
    projects_dir = CLAUDE_HOME / "projects"
    profiles = []
    if projects_dir.exists():
        for d in sorted(projects_dir.iterdir(), key=lambda x: -x.stat().st_mtime):
            if not d.is_dir():
                continue
            mem_dir = d / "memory"
            mem_count = len(list(mem_dir.glob("*.md"))) if mem_dir.exists() else 0
            profiles.append({
                "slug":         d.name,
                "mtime":        d.stat().st_mtime,
                "memoryCount":  mem_count,
                "hasSoul":      (d / "soul.md").exists() or (d / "souls").exists(),
                "hasSchedules": (d / "schedules.json").exists(),
            })
    current = _load_config().get("projectDir", "")
    return web.json_response({"profiles": profiles, "current": current})


# ── #16 Multi-provider OpenAI-compatible streaming ────────────────────────────

async def handle_chat_provider(request: web.Request) -> web.StreamResponse:
    """Stream chat via any OpenAI-compatible API (OpenAI / OpenRouter / Gemini)."""
    data     = await request.json()
    api_url  = data.get("apiUrl", "https://api.openai.com/v1").rstrip("/")
    api_key  = data.get("apiKey", "")
    model    = data.get("model", "gpt-4o-mini")
    messages = data.get("messages", [])

    resp = web.StreamResponse()
    resp.headers["Content-Type"]  = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    await resp.prepare(request)

    async def send(ev: dict) -> None:
        await resp.write(("data: " + json.dumps(ev) + "\n\n").encode())

    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{api_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "stream": True},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as upstream:
                if upstream.status >= 400:
                    body = await upstream.text()
                    await send({"type": "error", "text": f"Provider {upstream.status}: {body[:300]}"})
                    return
                async for line in upstream.content:
                    line_s = line.decode("utf-8").strip()
                    if not line_s.startswith("data: "):
                        continue
                    payload = line_s[6:]
                    if payload == "[DONE]":
                        await send({"type": "result", "usage": {}, "total_cost_usd": 0})
                        break
                    try:
                        chunk   = json.loads(payload)
                        delta   = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            await send({"type": "text", "text": content})
                    except Exception:
                        pass
    except Exception as e:
        await send({"type": "error", "text": str(e)})

    return resp


# ── #18 Telegram Bot gateway ──────────────────────────────────────────────────

TELEGRAM_CONFIG_FILE = CLAUDE_HOME / "claude-desktop-telegram.json"

_tg_state: dict = {"token": "", "enabled": False, "offset": 0}
_tg_task = None

def _load_tg_config() -> dict:
    if TELEGRAM_CONFIG_FILE.exists():
        try:
            return json.loads(TELEGRAM_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"token": "", "enabled": False}

def _save_tg_config(cfg: dict) -> None:
    TELEGRAM_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

async def _tg_send_msg(session: aiohttp.ClientSession, token: str, chat_id: int, text: str) -> None:
    try:
        await session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    except Exception as e:
        _log(f"[telegram] send error: {e}")

async def _tg_run_claude(prompt: str) -> str:
    env = os.environ.copy()
    key = _resolve_api_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", prompt, "--output-format", "text", "--no-caching",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        return stdout.decode("utf-8", errors="replace").strip() or "[no response]"
    except Exception as e:
        return f"[Error: {e}]"

async def _telegram_poll() -> None:
    cfg  = _tg_state
    base = f"https://api.telegram.org/bot{cfg['token']}"
    _log("[telegram] polling started")
    async with aiohttp.ClientSession() as session:
        while cfg["enabled"] and cfg["token"]:
            try:
                params: dict = {"timeout": 20}
                if cfg["offset"]:
                    params["offset"] = cfg["offset"]
                async with session.get(
                    f"{base}/getUpdates", params=params,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as r:
                    data = await r.json()
                    for upd in data.get("result", []):
                        cfg["offset"] = upd["update_id"] + 1
                        msg     = upd.get("message") or {}
                        chat_id = msg.get("chat", {}).get("id")
                        text    = msg.get("text", "")
                        if text and chat_id:
                            await _tg_send_msg(session, cfg["token"], chat_id, "⏳ 處理中…")
                            reply = await _tg_run_claude(text)
                            for i in range(0, len(reply), 4000):
                                await _tg_send_msg(session, cfg["token"], chat_id, reply[i:i+4000])
            except asyncio.CancelledError:
                break
            except Exception as e:
                _log(f"[telegram] poll error: {e}")
                await asyncio.sleep(5)
    _log("[telegram] polling stopped")

async def handle_telegram_get(request: web.Request) -> web.Response:
    cfg = _load_tg_config()
    running = _tg_task is not None and not _tg_task.done()
    return web.json_response({
        "token":   "***" if cfg.get("token") else "",
        "enabled": cfg.get("enabled", False),
        "running": running,
    })

async def handle_telegram_put(request: web.Request) -> web.Response:
    global _tg_task
    data = await request.json()
    cfg  = _load_tg_config()
    if "token" in data and data["token"] not in ("***", ""):
        cfg["token"] = data["token"]
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    _save_tg_config(cfg)
    _tg_state.update({"token": cfg.get("token",""), "enabled": cfg.get("enabled", False)})

    if cfg["enabled"] and cfg["token"]:
        if _tg_task is None or _tg_task.done():
            _tg_task = asyncio.create_task(_telegram_poll())
    else:
        if _tg_task and not _tg_task.done():
            _tg_task.cancel()
            _tg_task = None

    running = _tg_task is not None and not _tg_task.done()
    return web.json_response({"ok": True, "running": running})


# ── #20 Debug Dump ────────────────────────────────────────────────────────────

async def handle_debug_dump(request: web.Request) -> web.Response:
    import platform, sys
    cfg      = _load_config()
    safe_cfg = {k: v for k, v in cfg.items()
                if "key" not in k.lower() and "token" not in k.lower() and "password" not in k.lower()}
    sqlite_stats: dict = {}
    try:
        with _db() as c:
            row = c.execute("SELECT COUNT(*) as n, SUM(message_count) as m FROM sessions").fetchone()
            sqlite_stats = {"sessions": row["n"] or 0, "messages": row["m"] or 0}
    except Exception:
        pass
    dump = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform":  {"os": platform.system(), "release": platform.release(), "python": sys.version.split()[0]},
        "claude_bin": CLAUDE_BIN,
        "config":    safe_cfg,
        "sqlite":    sqlite_stats,
        "mcp_running": [k for k, v in _mcp_procs.items() if v.returncode is None],
        "telegram_running": _tg_task is not None and not _tg_task.done(),
        "log_tail":  _log_buffer[-30:],
    }
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return web.Response(
        body=json.dumps(dump, ensure_ascii=False, indent=2).encode(),
        headers={
            "Content-Type": "application/json",
            "Content-Disposition": f'attachment; filename="claude-debug-{ts}.json"',
        },
    )


async def handle_status(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "active_sessions": len(active_sessions), "claude_bin": CLAUDE_BIN})


async def handle_config_get(request: web.Request) -> web.Response:
    cfg = _load_config()
    cfg.setdefault("projectDir", "")
    cfg.setdefault("claudeHome", "")
    cfg["_resolvedClaudeHome"] = str(CLAUDE_HOME)   # read-only info for UI
    return web.json_response(cfg)


async def handle_config_put(request: web.Request) -> web.Response:
    global CLAUDE_HOME, AGENTS_DIR, SKILLS_DIR, SESSIONS_DIR
    data = await request.json()
    cfg = _load_config()
    if "projectDir" in data:
        cfg["projectDir"] = data["projectDir"].strip()
    if "apiKeyCmd" in data:
        cfg["apiKeyCmd"] = data["apiKeyCmd"].strip()
    if "claudeHome" in data:
        cfg["claudeHome"] = data["claudeHome"].strip()
    _save_config(cfg)
    # Re-resolve CLAUDE_HOME in case claudeHome changed
    CLAUDE_HOME  = _resolve_claude_home()
    AGENTS_DIR   = CLAUDE_HOME / "agents"
    SKILLS_DIR   = CLAUDE_HOME / "skills"
    SESSIONS_DIR = CLAUDE_HOME / "sessions"
    _log(f"Config updated: claudeHome={CLAUDE_HOME}  projectDir={cfg.get('projectDir','')!r}")
    return web.json_response({"ok": True, "slug": cfg.get("projectDir", "")})


def build_app() -> web.Application:
    app = web.Application()

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        )
    })

    # Routes grouped by resource path to avoid double-CORS registration
    route_groups: dict[str, list[tuple[str, any]]] = {}
    for method, path, handler in [
        ("POST",   "/api/chat",           handle_chat),
        ("POST",   "/api/chat/stop",      handle_chat_stop),
        ("GET",    "/api/backup",          handle_backup),
        ("POST",   "/api/restore",         handle_restore),
        ("DELETE", "/api/soul",            handle_soul_reset),
        ("POST",   "/api/upload",         handle_upload),
        ("POST",   "/api/translate",      handle_translate),
        ("PUT",    "/api/memory/{key}",    handle_memory_put),
        ("DELETE", "/api/memory/{key}",   handle_memory_delete),
        ("GET",    "/api/soul",           handle_soul_get),
        ("PUT",    "/api/soul",           handle_soul_put),
        ("GET",    "/api/souls",          handle_souls_list),
        ("PUT",    "/api/souls/{id}",     handle_soul_save),
        ("DELETE", "/api/souls/{id}",  handle_soul_delete),
        ("GET",    "/api/sessions",                      handle_sessions),
        ("POST",   "/api/sessions/resume",              handle_resume_session),
        ("DELETE", "/api/sessions/{id}",               handle_session_delete),
        ("PATCH",  "/api/sessions/{id}",               handle_session_rename),
        ("POST",   "/api/sessions/{id}/auto-title",    handle_session_auto_title),
        ("GET",    "/api/agents",          handle_agents),
        ("GET",    "/api/skills",          handle_skills),
        ("POST",   "/api/skills/generate", handle_skill_generate),
        ("GET",    "/api/memory",          handle_memory),
        ("GET",    "/api/schedules",       handle_schedules_get),
        ("POST",   "/api/schedules",       handle_schedules_post),
        ("DELETE", "/api/schedules/{id}",       handle_schedules_delete),
        ("PATCH",  "/api/schedules/{id}",       handle_schedules_patch),
        ("POST",   "/api/schedules/{id}/run",   handle_schedules_run),
        ("GET",    "/api/stats",            handle_stats),
        ("GET",    "/api/logs",            handle_logs),
        ("GET",    "/api/status",          handle_status),
        ("GET",    "/api/files",            handle_files),
        ("POST",   "/api/cli",             handle_cli),
        ("GET",    "/api/config",          handle_config_get),
        ("PUT",    "/api/config",          handle_config_put),
        ("POST",   "/api/mcp/{name}/{action}",  handle_mcp_action),
        ("GET",    "/api/mcp/{name}/logs",     handle_mcp_logs),
        ("GET",    "/api/mcp/{name}/info",     handle_mcp_info),
        ("GET",    "/api/mcp-local-config",    handle_local_mcp_config_get),
        ("PUT",    "/api/mcp-local-config/{name}", handle_local_mcp_config_put),
        # P3
        ("GET",    "/api/profiles",           handle_profiles),
        ("POST",   "/api/chat/provider",      handle_chat_provider),
        ("GET",    "/api/telegram",           handle_telegram_get),
        ("PUT",    "/api/telegram",           handle_telegram_put),
        ("GET",    "/api/debug-dump",         handle_debug_dump),
    ]:
        route_groups.setdefault(path, []).append((method, handler))

    for path, method_handlers in route_groups.items():
        resource = cors.add(app.router.add_resource(path))
        for method, handler in method_handlers:
            cors.add(resource.add_route(method, handler))

    return app


async def run_schedule_runner() -> None:
    """Check every 60 s whether any enabled schedule is due to run."""
    if not HAS_CRONITER:
        return
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        schedules = load_schedules()
        changed = False
        for sc in schedules:
            if not sc.get("enabled"):
                continue
            try:
                cron = croniter(sc["cron"], now)
                prev = cron.get_prev(datetime)
                last_run_str = sc.get("last_run", "")
                last_run = datetime.fromisoformat(last_run_str) if last_run_str else None
                if last_run is None or (now - prev).total_seconds() < 60 and prev > last_run:
                    sc["last_run"] = now.isoformat()
                    changed = True
                    asyncio.create_task(run_schedule_prompt(sc["prompt"]))
            except Exception:
                pass
        if changed:
            save_schedules(schedules)


async def run_schedule_prompt(prompt: str) -> None:
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        await proc.communicate()
    except Exception as e:
        print(f"[schedule] Error running prompt: {e}")


async def on_startup(app: web.Application) -> None:
    global _tg_task
    _log(f"Backend started. Claude: {CLAUDE_BIN}")
    asyncio.create_task(run_schedule_runner())
    # Auto-start Telegram bot if configured
    tg_cfg = _load_tg_config()
    _tg_state.update({"token": tg_cfg.get("token",""), "enabled": tg_cfg.get("enabled", False)})
    if tg_cfg.get("enabled") and tg_cfg.get("token"):
        _tg_task = asyncio.create_task(_telegram_poll())


if __name__ == "__main__":
    print("Claude Desktop backend starting on http://localhost:8765")
    app = build_app()
    app.on_startup.append(on_startup)
    web.run_app(app, host="127.0.0.1", port=8765)
