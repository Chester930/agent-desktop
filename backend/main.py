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
TEAMS_DIR    = CLAUDE_HOME / "teams"
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

SCHEDULES_FILE     = CLAUDE_HOME / "schedules.json"
SESSION_NAMES_FILE = CLAUDE_HOME / "session_names.json"
SOUL_FILE          = CLAUDE_HOME / "soul.md"
SOULS_DIR          = CLAUDE_HOME / "souls"

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

async def _natural_to_cron(natural_text: str) -> str:
    import re
    # 1. Check if it's already a valid 5-column cron expression
    if HAS_CRONITER:
        try:
            if croniter.is_valid(natural_text):
                return natural_text
        except Exception:
            pass
    else:
        if re.match(r"^(\S+\s+){4}\S+$", natural_text):
            return natural_text

    # 2. Otherwise, translate via Claude CLI
    prompt = (
        "請將以下的自然語言時間，轉換為 5 欄的標準 Cron 表達式（分 時 日 月 週）。\n"
        "每個欄位用空格分隔。不要輸出任何其他欄位（如秒或年），只保留 5 欄格式。\n"
        "範例：\n"
        "「每天早上 9 點」 -> 0 9 * * *\n"
        "「每小時」 -> 0 * * * *\n"
        "「每星期一早上 8:30」 -> 30 8 * * 1\n"
        "「每 5 分鐘」 -> */5 * * * *\n"
        "「每天 15:30」 -> 30 15 * * *\n\n"
        "請嚴格只輸出 Cron 表達式本身，絕對不要包含任何 Markdown 程式碼區塊包裹、引號、說明、前言或後記。\n"
        f"時間：{natural_text}"
    )
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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        res = stdout.decode("utf-8", errors="replace").strip()
        res = re.sub(r"[`'\"“”]", "", res)
        lines = [l.strip() for l in res.splitlines() if l.strip()]
        for cand in lines:
            parts = cand.split()
            if len(parts) == 5:
                return cand
        if lines:
            return lines[0]
    except Exception as e:
        print(f"[schedule] Error translating natural time to cron: {e}")
    return natural_text


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


def build_memory_context(agent_id: str, cwd: str) -> str:
    """
    依序組裝五層記憶並回傳注入字串。
    注入順序：User → System → Agent Identity → Agent Project → Project Internal
    缺少的層自動跳過，全部為空時回傳空字串。
    """
    slug = _encode_slug(cwd) if cwd else ""
    sections: list[str] = []

    user = _read_md(_global_memory_dir() / "user" / "profile.md")
    if user:
        sections.append(f"[User Memory]\n{user}")

    system = _read_md(_global_memory_dir() / "system" / "state.md")
    if system:
        sections.append(f"[System Memory]\n{system}")

    if agent_id:
        identity = _read_md(_agent_memory_dir(agent_id) / "identity.md")
        if identity:
            sections.append(f"[Agent Identity — {agent_id}]\n{identity}")

        if slug:
            proj = _read_md(_agent_memory_dir(agent_id) / "projects" / f"{slug}.md")
            if proj:
                sections.append(f"[Agent Experience — {agent_id} / {slug}]\n{proj}")

    if slug:
        proj_mem_dir = CLAUDE_HOME / "projects" / slug / "memory"
        if proj_mem_dir.exists():
            parts = []
            for f in sorted(proj_mem_dir.glob("*.md")):
                content = _read_md(f)
                if content:
                    parts.append(f"### {f.stem}\n{content}")
            if parts:
                sections.append(f"[Project Internal Memory — {slug}]\n" + "\n\n".join(parts))

    return "\n\n---\n\n".join(sections) if sections else ""


def build_team_memory_context(
    team_id: str,
    all_member_ids: list[str],
    current_agent_id: str,
    cwd: str,
) -> str:
    """
    組裝 Team Run 的記憶 context（每位成員各自收到）。
    注入順序：
      User → Team Shared → Team Project → 所有成員 Identity（互知）
      → 當前成員 Agent Project → Project Internal
    """
    slug = _encode_slug(cwd) if cwd else ""
    sections: list[str] = []

    user = _read_md(_global_memory_dir() / "user" / "profile.md")
    if user:
        sections.append(f"[User Memory]\n{user}")

    team_shared = _read_md(_team_memory_dir(team_id) / "shared.md")
    if team_shared:
        sections.append(f"[Team Memory — {team_id}]\n{team_shared}")

    if slug:
        team_proj = _read_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md")
        if team_proj:
            sections.append(f"[Team Project Memory — {team_id} / {slug}]\n{team_proj}")

    member_identities = []
    for mid in all_member_ids:
        identity = _read_md(_agent_memory_dir(mid) / "identity.md")
        if identity:
            label = "（你）" if mid == current_agent_id else ""
            member_identities.append(f"#### {mid}{label}\n{identity}")
    if member_identities:
        sections.append("[Team Members — 成員背景與專長]\n" + "\n\n".join(member_identities))

    if current_agent_id and slug:
        agent_proj = _read_md(_agent_memory_dir(current_agent_id) / "projects" / f"{slug}.md")
        if agent_proj:
            sections.append(f"[Agent Experience — {current_agent_id} / {slug}]\n{agent_proj}")

    # 專案內部記憶（詳細進度）不預載入 Team context。
    # 需要細節時由 Agent 主動查詢，保持 Team 共享的是智慧與經驗而非行程表。

    return "\n\n---\n\n".join(sections) if sections else ""


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

    mem_ctx = build_memory_context(agent, cwd)
    if mem_ctx:
        full_message = f"[Memory Context]\n{mem_ctx}\n\n---\n\n{full_message}"

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
    return web.json_response({
        "items": items,
        "sessions": items,
        "total": total,
        "has_more": total > offset + PAGE
    })


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
            mem_dir = _memory_dir()
            mem_dir.mkdir(parents=True, exist_ok=True)
            for name in zf.namelist():
                if name.startswith('memory/') and name.endswith('.md'):
                    (mem_dir / Path(name).name).write_bytes(zf.read(name))
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
        mem_dir = _memory_dir()
        if mem_dir.exists():
            for f in mem_dir.glob('*.md'):
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
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                agents.append({"id": f.stem, "name": f.stem, "description": _desc_from_md_file(f)})
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


# ── Frontmatter 完整解析 / 寫回 ──────────────────────────────────────────────

def _parse_yaml_list(lines: list, start: int):
    """Parse indented YAML list starting at index. Returns (items, next_index)."""
    items, i = [], start
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith("- "):
            items.append(s[2:].strip().strip("\"'"))
            i += 1
        elif lines[i].strip() == "":
            i += 1
        else:
            break
    return items, i


def _parse_full_frontmatter(path: Path) -> dict:
    """Parse all key/value pairs from YAML frontmatter of a .md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm = lines[1:end]
    result, i = {}, 0
    while i < len(fm):
        line = fm[i]
        if not line.strip():
            i += 1
            continue
        m = _re.match(r'^([\w][\w-]*):\s*(.*)', line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            items, i = _parse_yaml_list(fm, i + 1)
            result[key] = items
        elif val.startswith("["):
            result[key] = [x.strip().strip("\"'") for x in val.strip("[]").split(",") if x.strip()]
            i += 1
        else:
            result[key] = val.strip("\"'")
            i += 1
    return result


def _write_frontmatter(path: Path, fm: dict) -> None:
    """Rewrite frontmatter of a .md file; preserve body content."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    lines = text.splitlines(keepends=True)
    body_start = None
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                body_start = i + 1
                break
    body = "".join(lines[body_start:]) if body_start is not None else ""
    parts = ["---\n"]
    for key, val in fm.items():
        if isinstance(val, list):
            if val:
                parts.append(f"{key}:\n")
                for item in val:
                    parts.append(f"  - {item}\n")
            else:
                parts.append(f"{key}: []\n")
        else:
            parts.append(f"{key}: {val}\n")
    parts.append("---\n")
    path.write_text("".join(parts) + body, encoding="utf-8")


def _agent_dict(f: Path) -> dict:
    aid = f.stem
    fm = _parse_full_frontmatter(f)

    # 確保每個 Agent 都有對應的 soul 檔案，沒有就自動建立空白檔案
    soul_file = SOULS_DIR / f"{aid}.md"
    if not soul_file.exists():
        try:
            SOULS_DIR.mkdir(parents=True, exist_ok=True)
            soul_file.write_text("", encoding="utf-8")
        except Exception:
            pass

    # 確保 Agent frontmatter 中的 soul 屬性有設定且為 aid
    if not fm.get("soul") or fm.get("soul") != aid:
        fm["soul"] = aid
        try:
            _write_frontmatter(f, fm)
        except Exception:
            pass

    return {
        "id":            aid,
        "name":          fm.get("name", aid),
        "description":   fm.get("description", _desc_from_md_file(f)),
        "soul":          aid,
        "skills":        fm.get("skills", []) if isinstance(fm.get("skills"), list) else [],
        "memory":        fm.get("memory", []) if isinstance(fm.get("memory"), list) else [],
        "mcp":           fm.get("mcp", [])    if isinstance(fm.get("mcp"), list)    else [],
        "output_memory": fm.get("output_memory", []) if isinstance(fm.get("output_memory"), list) else [],
        "tools":         fm.get("tools", ""),
    }


# ── Agent CRUD ────────────────────────────────────────────────────────────────

async def handle_agents(request: web.Request) -> web.Response:
    agents = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                agents.append(_agent_dict(f))
            except Exception:
                pass
    return web.json_response(agents)


async def handle_agents_registry(request: web.Request) -> web.Response:
    registry = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                d = _agent_dict(f)
                registry.append({
                    "id": f.stem,
                    "name": d.get("name", f.stem),
                    "description": d.get("description", ""),
                    "skills": d.get("skills", [])
                })
            except Exception:
                pass
    return web.json_response(registry)


async def _run_hr_agent(task: str) -> dict:
    agents_list = []
    if AGENTS_DIR.exists():
        for f in sorted(AGENTS_DIR.glob("*.md"), key=lambda p: p.name.lower()):
            try:
                d = _agent_dict(f)
                agents_list.append({
                    "id": f.stem,
                    "name": d.get("name", f.stem),
                    "description": d.get("description", ""),
                    "skills": d.get("skills", [])
                })
            except Exception:
                pass
    if not agents_list:
        return {"error": "尚未建立任何 Agent。請先至 Agent 頁籤建立 Agent 後，再使用自動組隊功能。"}

    registry_str = json.dumps(agents_list, ensure_ascii=False, indent=2)
    prompt = f"""你是一個 HR Agent（任務協調整合器）。請分析使用者的任務描述，並從下方的 Agent 列表中，挑選最適合的 Agent 組成一個循序執行的團隊（Team）來完成任務。

可用 Agent 列表：
{registry_str}

請務必遵守以下規定：
1. 僅從上述列表中挑選 Agent，不要捏造不存在的 Agent ID。
2. 根據任務的邏輯順序安排執行步驟（Step 1, Step 2...）。前一個 Agent 的輸出將作為下一個 Agent 的輸入上下文。
3. 為每個步驟的 Agent 設定合適的 role（任務職責說明）。
4. 設定 input_memory（讀取）與 output_memory（寫入）的鍵值（keys），用於在步驟間傳遞 context 或保存中間產物。例如第一步寫入 'step1-result'，第二步讀取 'step1-result'。
5. 請只輸出一個純 JSON 對象，不要包含任何 markdown 標記（如 ```json ... ```）、引言或額外說明文字。

JSON Schema:
{{
  "name": "auto-created-team-name",
  "description": "說明此團隊如何協作與組隊理由",
  "members": [
    {{
      "agent": "選用的 Agent ID",
      "role": "該步驟的具體工作描述",
      "input_memory": ["要讀取的 memory 鍵"],
      "output_memory": ["要寫入的 memory 鍵"]
    }}
  ]
}}

使用者任務描述：
{task}
"""

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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        output_str = stdout.decode("utf-8", errors="replace").strip()
    except Exception as e:
        return {"error": f"HR dispatch failed: {e}"}

    s = output_str.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline:].strip()
        if s.endswith("```"):
            s = s[:-3].strip()

    try:
        plan = json.loads(s)
        return plan
    except Exception:
        start_idx = s.find("{")
        end_idx = s.rfind("}")
        if start_idx != -1 and end_idx != -1:
            try:
                plan = json.loads(s[start_idx:end_idx+1])
                return plan
            except Exception:
                pass
        return {"error": "Failed to parse HR Agent JSON response", "raw": output_str}


async def handle_hr_dispatch(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        task = data.get("task", "").strip()
        if not task:
            return web.json_response({"error": "task is required"}, status=400)
        plan = await _run_hr_agent(task)
        if "error" in plan:
            return web.json_response(plan, status=500)
        return web.json_response(plan)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_agent_get(request: web.Request) -> web.Response:
    aid = request.match_info["id"]
    f = AGENTS_DIR / f"{aid}.md"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_agent_dict(f))


async def handle_agent_put(request: web.Request) -> web.Response:
    aid = request.match_info["id"]
    f = AGENTS_DIR / f"{aid}.md"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    fm = _parse_full_frontmatter(f)
    for field in ("name", "description", "soul", "skills", "memory", "mcp", "output_memory", "tools"):
        if field in data:
            fm[field] = data[field]
    _write_frontmatter(f, fm)
    return web.json_response({"ok": True})


async def handle_agent_post(request: web.Request) -> web.Response:
    data = await request.json()
    raw = data.get("name", "").strip()
    name = _re.sub(r"[^\w-]", "-", raw).lower().strip("-")
    if not name:
        return web.json_response({"error": "invalid name"}, status=400)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    f = AGENTS_DIR / f"{name}.md"
    if f.exists():
        return web.json_response({"error": "already exists"}, status=409)
    desc = data.get("description", "")
    f.write_text(
        f"---\nname: {name}\ndescription: {desc}\ntools: Read, Grep, Glob\n"
        f"soul: \nskills: []\nmemory: []\nmcp: []\noutput_memory: []\n---\n\n## {name}\n\n{desc}\n",
        encoding="utf-8"
    )
    return web.json_response({"ok": True, "id": name})


async def handle_agent_delete(request: web.Request) -> web.Response:
    aid = request.match_info["id"]
    f = AGENTS_DIR / f"{aid}.md"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


# ── Skill CRUD ────────────────────────────────────────────────────────────────

def _skill_dict_from_file(entry: Path) -> dict:
    fm = _parse_full_frontmatter(entry)
    return {
        "id":            entry.stem,
        "name":          entry.stem,
        "description":   fm.get("description", _desc_from_md_file(entry)),
        "type":          "file",
        "mcp":           fm.get("mcp", [])           if isinstance(fm.get("mcp"), list)           else [],
        "memory":        fm.get("memory", [])         if isinstance(fm.get("memory"), list)         else [],
        "output_memory": fm.get("output_memory", [])  if isinstance(fm.get("output_memory"), list)  else [],
    }


def _skill_dict_from_dir(entry: Path) -> dict:
    fm = {}
    for c in (entry / "SKILL.md", entry / "README.md"):
        if c.exists():
            fm = _parse_full_frontmatter(c)
            break
    return {
        "id":            entry.name,
        "name":          entry.name,
        "description":   _desc_from_skill_dir(entry),
        "type":          "directory",
        "mcp":           fm.get("mcp", [])           if isinstance(fm.get("mcp"), list)           else [],
        "memory":        fm.get("memory", [])         if isinstance(fm.get("memory"), list)         else [],
        "output_memory": fm.get("output_memory", [])  if isinstance(fm.get("output_memory"), list)  else [],
    }


async def handle_skills(request: web.Request) -> web.Response:
    skills = []
    if not SKILLS_DIR.exists():
        return web.json_response(skills)
    for entry in sorted(SKILLS_DIR.iterdir(), key=lambda p: p.name.lower()):
        try:
            if entry.is_dir():
                skills.append(_skill_dict_from_dir(entry))
            elif entry.suffix == ".md":
                skills.append(_skill_dict_from_file(entry))
        except Exception:
            pass
    return web.json_response(skills)


async def handle_skill_get(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    f = SKILLS_DIR / f"{sid}.md"
    if f.exists():
        return web.json_response(_skill_dict_from_file(f))
    d = SKILLS_DIR / sid
    if d.is_dir():
        return web.json_response(_skill_dict_from_dir(d))
    return web.json_response({"error": "not found"}, status=404)


async def handle_skill_put(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    f = SKILLS_DIR / f"{sid}.md"
    if not f.exists():
        d = SKILLS_DIR / sid
        for c in (d / "SKILL.md", d / "README.md"):
            if c.exists():
                f = c
                break
        else:
            return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    fm = _parse_full_frontmatter(f)
    for field in ("mcp", "memory", "output_memory"):
        if field in data:
            fm[field] = data[field]
    _write_frontmatter(f, fm)
    return web.json_response({"ok": True})



# ── Teams CRUD ────────────────────────────────────────────────────────────────

def _parse_yaml_simple(text: str) -> dict:
    """Parse team YAML using PyYAML, with fallback to regex."""
    if not text:
        return {}
    
    # 處理可能被 --- frontmatter 包裹的內容
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i
                break
        if end is not None:
            text = "\n".join(lines[1:end])

    try:
        import yaml as _yaml
        res = _yaml.safe_load(text)
        if isinstance(res, dict):
            return res
        return {}
    except Exception:
        pass

    import re as _re
    result = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        m = _re.match(r'^([\w][\w-]*):\s*(.*)', line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        if val == "":
            items, i = _parse_yaml_list(lines, i + 1)
            result[key] = items
        elif val.startswith("[") and val.endswith("]"):
            result[key] = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
            i += 1
        else:
            result[key] = val.strip("\"'")
            i += 1
    return result


def _write_team_yaml(path: Path, data: dict) -> None:
    try:
        import yaml as _yaml
        path.write_text(_yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    except Exception:
        lines = []
        for key, val in data.items():
            if isinstance(val, list):
                if val:
                    lines.append(f"{key}:")
                    for item in val:
                        if isinstance(item, dict):
                            first = True
                            for k, v in item.items():
                                prefix = "  - " if first else "    "
                                lines.append(f"{prefix}{k}: {v}")
                                first = False
                        else:
                            lines.append(f"  - {item}")
                else:
                    lines.append(f"{key}: []")
            else:
                lines.append(f"{key}: {val}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _team_dict(f: Path) -> dict:
    try:
        raw = _parse_yaml_simple(f.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    members_raw = raw.get("members", [])
    members = []
    for m in members_raw:
        if isinstance(m, dict):
            members.append({"agent": m.get("agent", ""), "role": m.get("role", "")})
        elif isinstance(m, str):
            members.append({"agent": m, "role": ""})
    return {
        "id":          f.stem,
        "name":        raw.get("name", f.stem),
        "description": raw.get("description", ""),
        "members":     members,
    }


async def handle_teams(request: web.Request) -> web.Response:
    teams = []
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(TEAMS_DIR.glob("*.yaml"), key=lambda p: p.name.lower()):
        try:
            teams.append(_team_dict(f))
        except Exception:
            pass
    return web.json_response(teams)


async def handle_team_get(request: web.Request) -> web.Response:
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_team_dict(f))


async def handle_team_post(request: web.Request) -> web.Response:
    data = await request.json()
    import re as _re3
    raw = data.get("name", "").strip()
    tid = _re3.sub(r"[^\w-]", "-", raw).lower().strip("-") or "new-team"
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    f = TEAMS_DIR / f"{tid}.yaml"
    if f.exists():
        return web.json_response({"error": "already exists"}, status=409)
    _write_team_yaml(f, {
        "name": raw or tid,
        "description": data.get("description", ""),
        "members": data.get("members", []),
    })
    return web.json_response({"ok": True, "id": tid})


async def handle_team_put(request: web.Request) -> web.Response:
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if not f.exists():
        return web.json_response({"error": "not found"}, status=404)
    data = await request.json()
    current = _team_dict(f)
    payload = {
        "name":        data.get("name", current["name"]),
        "description": data.get("description", current["description"]),
        "members":     data.get("members", current["members"]),
    }
    _write_team_yaml(f, payload)
    return web.json_response({"ok": True})


async def handle_team_delete(request: web.Request) -> web.Response:
    tid = request.match_info["id"]
    f = TEAMS_DIR / f"{tid}.yaml"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


# ── Phase 3: Multi-Agent Sequential Execution ─────────────────────────────────

import uuid as _uuid

_team_runs:   dict[str, dict] = {}
_team_events: dict[str, list] = {}
_team_queues: dict[str, list] = {}


def _tr_emit(run_id: str, event: dict) -> None:
    _team_events.setdefault(run_id, []).append(event)
    for q in _team_queues.get(run_id, []):
        q.put_nowait(event)


async def _agent_run_capture(
    run_id: str, step_idx: int,
    agent_id: str, prompt: str,
    model: str, cwd: str
) -> str:
    agent_file = AGENTS_DIR / f"{agent_id}.md"
    agent_body = ""
    if agent_file.exists():
        try:
            raw_text = agent_file.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                agent_body = parts[2].strip() if len(parts) >= 3 else ""
            else:
                agent_body = raw_text
        except Exception:
            pass

    soul = get_concatenated_soul()
    full_prompt = prompt
    if agent_body:
        full_prompt = f"[代理人：{agent_id}]\n{agent_body}\n\n---\n\n{full_prompt}"
    if soul:
        full_prompt = f"[System Persona]\n{soul}\n\n{full_prompt}"

    cmd = [CLAUDE_BIN, "-p", full_prompt, "--output-format", "stream-json", "--verbose"]
    if model and model not in ("sonnet", ""):
        cmd += ["--model", model]

    env = {**os.environ}
    api_key = _resolve_api_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    safe_cwd = cwd if (cwd and Path(cwd).is_dir()) else str(Path.home())
    output_parts: list[str] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=safe_cwd,
            env=env,
        )
        async for line in proc.stdout:
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                chunk = ""
                if ev.get("type") == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunk += block["text"]
                elif ev.get("type") == "text":
                    chunk = ev.get("text", "")
                if chunk:
                    output_parts.append(chunk)
                    _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": chunk})
            except json.JSONDecodeError:
                pass
        await proc.wait()
    except Exception as e:
        err = f"\n[Error running {agent_id}: {e}]\n"
        output_parts.append(err)
        _tr_emit(run_id, {"type": "step_text", "step": step_idx, "text": err})

    return "".join(output_parts)


async def _execute_team_run(run_id: str, task: str, model: str, cwd: str) -> None:
    run = _team_runs[run_id]
    steps = run["steps"]
    team_id = run.get("team_id", "")
    all_member_ids = [s["agent"] for s in steps]
    prev_output = ""

    for i, step in enumerate(steps):
        if run.get("status") == "cancelled":
            break
        step["status"] = "running"
        _tr_emit(run_id, {"type": "step_start", "step": i,
                           "agent": step["agent"], "role": step["role"]})

        agent_id = step["agent"]
        agent_info = {}
        try:
            f_agent = AGENTS_DIR / f"{agent_id}.md"
            if f_agent.exists():
                agent_info = _agent_dict(f_agent)
        except Exception:
            pass

        # 分層 team memory 注入
        mem_ctx = build_team_memory_context(team_id, all_member_ids, agent_id, cwd)

        # 舊式 KV memory（向下相容，保留原有 memory 欄位讀取）
        legacy_memory: list[str] = []
        read_keys = agent_info.get("memory", [])
        mem_dir = _memory_dir()
        for key in read_keys:
            key_file = mem_dir / f"{key}.md"
            if key_file.exists():
                try:
                    content = key_file.read_text(encoding="utf-8")
                    legacy_memory.append(f"### {key}\n\n{content}")
                except Exception:
                    pass

        prompt_parts = []
        if mem_ctx:
            prompt_parts.append(f"[Memory Context]\n{mem_ctx}")
        if legacy_memory:
            prompt_parts.append("---\n## 相關 Memory 上下文\n\n" + "\n\n".join(legacy_memory))

        if i == 0:
            prompt_parts.append(f"---\n## 任務\n\n{task}")
        else:
            prompt_parts.append(
                f"---\n## 任務\n\n{task}\n\n"
                f"---\n## 前置 Agent（{steps[i-1]['agent']}）的輸出\n\n{prev_output}"
            )

        prompt = "\n\n".join(prompt_parts)

        output = await _agent_run_capture(run_id, i, agent_id, prompt, model, cwd)
        step["output"] = output
        step["status"] = "done"
        prev_output = output
        _tr_emit(run_id, {"type": "step_done", "step": i})

        # 舊式 KV output_memory（向下相容）
        write_keys = agent_info.get("output_memory", [])
        if write_keys:
            mem_dir.mkdir(parents=True, exist_ok=True)
            for key in write_keys:
                try:
                    (mem_dir / f"{key}.md").write_text(output, encoding="utf-8")
                except Exception:
                    pass

    if run.get("status") != "cancelled":
        run["status"] = "done"
        summary_parts = [
            f"### {s['agent']}（{s['role']}）\n\n{s['output']}" for s in steps
        ]
        run["summary"] = "\n\n---\n\n".join(summary_parts)
        _tr_emit(run_id, {"type": "done", "summary": run["summary"]})

        # Team Run 完成後自動更新 team project memory
        if team_id and cwd:
            slug = _encode_slug(cwd)
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            proj_summary = (
                f"# Team Run 記錄 — {timestamp}\n\n"
                f"## 任務\n\n{task}\n\n"
                f"## 成員\n\n" +
                "\n".join(f"- {mid}" for mid in all_member_ids) +
                f"\n\n## 執行摘要\n\n{run['summary'][:1800]}"
            )
            _write_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md", proj_summary)


async def handle_team_run_post(request: web.Request) -> web.Response:
    data    = await request.json()
    team_id = data.get("team_id", "")
    task    = data.get("task", "").strip()
    model   = data.get("model", "")
    cwd     = data.get("cwd", "")
    team_payload = data.get("team", None)

    if not task:
        return web.json_response({"error": "task required"}, status=400)

    if team_payload:
        team = team_payload
    else:
        f = TEAMS_DIR / f"{team_id}.yaml"
        if not f.exists():
            return web.json_response({"error": "team not found"}, status=404)
        team = _team_dict(f)

    if not team.get("members"):
        return web.json_response({"error": "team has no members"}, status=400)

    run_id = _uuid.uuid4().hex[:8]
    _team_runs[run_id] = {
        "id":      run_id,
        "team_id": team.get("id", team_id),
        "name":    team.get("name", "Auto Team"),
        "task":    task,
        "status":  "running",
        "steps": [
            {"agent": m["agent"], "role": m["role"], "status": "pending", "output": ""}
            for m in team["members"]
        ],
        "summary": "",
    }
    _team_events[run_id] = []
    _team_queues[run_id] = []

    asyncio.create_task(_execute_team_run(run_id, task, model, cwd))
    return web.json_response({"ok": True, "run_id": run_id})


async def handle_team_run_get(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    run = _team_runs.get(run_id)
    if not run:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(run)


async def handle_team_run_stream(request: web.Request) -> web.StreamResponse:
    run_id = request.match_info["run_id"]
    if run_id not in _team_runs:
        return web.Response(status=404)

    response = web.StreamResponse(headers={
        "Content-Type":      "text/event-stream",
        "Cache-Control":     "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    q: asyncio.Queue = asyncio.Queue()
    _team_queues.setdefault(run_id, []).append(q)

    for ev in _team_events.get(run_id, []):
        await response.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
        if ev.get("type") in ("done", "error"):
            _team_queues[run_id].remove(q)
            return response

    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await response.write(b'data: {"type":"ping"}\n\n')
                continue
            await response.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            if ev.get("type") in ("done", "error"):
                break
    finally:
        queues = _team_queues.get(run_id, [])
        if q in queues:
            queues.remove(q)

    return response


async def handle_team_run_cancel(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    run = _team_runs.get(run_id)
    if run:
        run["status"] = "cancelled"
        _tr_emit(run_id, {"type": "error", "text": "cancelled"})
    return web.json_response({"ok": True})


async def handle_memory(request: web.Request) -> web.Response:
    files = []
    mem_dir = _memory_dir()
    if mem_dir.exists():
        for f in mem_dir.glob("*.md"):
            try:
                files.append({
                    "key": f.stem,
                    "content": f.read_text(encoding="utf-8")
                })
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
    mem_dir = _memory_dir()
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / f"{key}.md").write_text(content, encoding="utf-8")
    return web.json_response({"ok": True})

async def handle_memory_delete(request: web.Request) -> web.Response:
    key = request.match_info["key"]
    f = _memory_dir() / f"{key}.md"
    if f.exists():
        f.unlink()
    return web.json_response({"ok": True})


# ── 分層記憶 API ──────────────────────────────────────────────────────────────

async def handle_mem_user_get(request: web.Request) -> web.Response:
    content = _read_md(_global_memory_dir() / "user" / "profile.md")
    return web.json_response({"content": content or ""})

async def handle_mem_user_put(request: web.Request) -> web.Response:
    data = await request.json()
    _write_md(_global_memory_dir() / "user" / "profile.md", data.get("content", ""))
    return web.json_response({"ok": True})


async def handle_mem_system_get(request: web.Request) -> web.Response:
    content = _read_md(_global_memory_dir() / "system" / "state.md")
    return web.json_response({"content": content or ""})

async def handle_mem_system_put(request: web.Request) -> web.Response:
    data = await request.json()
    _write_md(_global_memory_dir() / "system" / "state.md", data.get("content", ""))
    return web.json_response({"ok": True})


async def handle_mem_agents_list(request: web.Request) -> web.Response:
    base = _global_memory_dir() / "agents"
    agents = []
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir():
                has_identity = (d / "identity.md").exists()
                proj_dir = d / "projects"
                project_count = len(list(proj_dir.glob("*.md"))) if proj_dir.exists() else 0
                agents.append({"id": d.name, "has_identity": has_identity, "project_count": project_count})
    return web.json_response(agents)

async def handle_mem_agent_get(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    content = _read_md(_agent_memory_dir(agent_id) / "identity.md")
    return web.json_response({"agent_id": agent_id, "content": content or ""})

async def handle_mem_agent_put(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    data = await request.json()
    _write_md(_agent_memory_dir(agent_id) / "identity.md", data.get("content", ""))
    return web.json_response({"ok": True})

async def handle_mem_agent_projects_list(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    proj_dir = _agent_memory_dir(agent_id) / "projects"
    projects = []
    if proj_dir.exists():
        for f in sorted(proj_dir.glob("*.md")):
            try:
                projects.append({"slug": f.stem, "size": f.stat().st_size, "mtime": int(f.stat().st_mtime)})
            except Exception:
                pass
    return web.json_response(projects)

async def handle_mem_agent_project_get(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    slug     = request.match_info["slug"]
    content  = _read_md(_agent_memory_dir(agent_id) / "projects" / f"{slug}.md")
    return web.json_response({"agent_id": agent_id, "slug": slug, "content": content or ""})

async def handle_mem_agent_project_put(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    slug     = request.match_info["slug"]
    data     = await request.json()
    _write_md(_agent_memory_dir(agent_id) / "projects" / f"{slug}.md", data.get("content", ""))
    return web.json_response({"ok": True})


async def handle_mem_teams_list(request: web.Request) -> web.Response:
    base = _global_memory_dir() / "teams"
    teams = []
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir():
                has_shared = (d / "shared.md").exists()
                proj_dir = d / "projects"
                project_count = len(list(proj_dir.glob("*.md"))) if proj_dir.exists() else 0
                teams.append({"id": d.name, "has_shared": has_shared, "project_count": project_count})
    return web.json_response(teams)

async def handle_mem_team_get(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    content = _read_md(_team_memory_dir(team_id) / "shared.md")
    return web.json_response({"team_id": team_id, "content": content or ""})

async def handle_mem_team_put(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    data    = await request.json()
    _write_md(_team_memory_dir(team_id) / "shared.md", data.get("content", ""))
    return web.json_response({"ok": True})

async def handle_mem_team_projects_list(request: web.Request) -> web.Response:
    team_id  = request.match_info["id"]
    proj_dir = _team_memory_dir(team_id) / "projects"
    projects = []
    if proj_dir.exists():
        for f in sorted(proj_dir.glob("*.md")):
            try:
                projects.append({"slug": f.stem, "size": f.stat().st_size, "mtime": int(f.stat().st_mtime)})
            except Exception:
                pass
    return web.json_response(projects)

async def handle_mem_team_project_get(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    slug    = request.match_info["slug"]
    content = _read_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md")
    return web.json_response({"team_id": team_id, "slug": slug, "content": content or ""})

async def handle_mem_team_project_put(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    slug    = request.match_info["slug"]
    data    = await request.json()
    _write_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md", data.get("content", ""))
    return web.json_response({"ok": True})

async def handle_mem_overview(request: web.Request) -> web.Response:
    """回傳整個記憶體系的結構概覽，供前端瀏覽器使用。"""
    base = _global_memory_dir()

    user_content    = _read_md(base / "user" / "profile.md")
    system_content  = _read_md(base / "system" / "state.md")

    agents = []
    agents_dir = base / "agents"
    if agents_dir.exists():
        for d in sorted(agents_dir.iterdir()):
            if not d.is_dir():
                continue
            proj_dir = d / "projects"
            projects = []
            if proj_dir.exists():
                for f in sorted(proj_dir.glob("*.md")):
                    projects.append({"slug": f.stem, "mtime": int(f.stat().st_mtime)})
            agents.append({
                "id": d.name,
                "identity": _read_md(d / "identity.md"),
                "projects": projects,
            })

    teams = []
    teams_dir = base / "teams"
    if teams_dir.exists():
        for d in sorted(teams_dir.iterdir()):
            if not d.is_dir():
                continue
            proj_dir = d / "projects"
            projects = []
            if proj_dir.exists():
                for f in sorted(proj_dir.glob("*.md")):
                    projects.append({"slug": f.stem, "mtime": int(f.stat().st_mtime)})
            teams.append({
                "id": d.name,
                "shared": _read_md(d / "shared.md"),
                "projects": projects,
            })

    proj_mem = []
    mem_dir = _memory_dir()
    if mem_dir.exists():
        for f in sorted(mem_dir.glob("*.md")):
            proj_mem.append({"key": f.stem, "mtime": int(f.stat().st_mtime)})

    return web.json_response({
        "user":    {"content": user_content or ""},
        "system":  {"content": system_content or ""},
        "agents":  agents,
        "teams":   teams,
        "project": proj_mem,
    })

async def handle_mem_preview(request: web.Request) -> web.Response:
    """
    Debug 端點：預覽 build_memory_context / build_team_memory_context 的輸出。
    GET /api/mem/preview?agent=Pi&cwd=/path/to/project
    GET /api/mem/preview?mode=team&team_id=MyTeam&members=Pi,CodeReviewer&cwd=/path
    """
    mode     = request.rel_url.query.get("mode", "agent")
    agent_id = request.rel_url.query.get("agent", "")
    cwd      = request.rel_url.query.get("cwd", "")
    team_id  = request.rel_url.query.get("team_id", "")
    members  = [m.strip() for m in request.rel_url.query.get("members", "").split(",") if m.strip()]

    if mode == "team":
        ctx = build_team_memory_context(team_id, members or [agent_id], agent_id, cwd)
    else:
        ctx = build_memory_context(agent_id, cwd)

    sections = [s.split("\n")[0] for s in ctx.split("\n\n---\n\n")] if ctx else []
    return web.json_response({
        "mode":        mode,
        "agent":       agent_id,
        "team_id":     team_id,
        "cwd":         cwd,
        "slug":        _encode_slug(cwd) if cwd else "",
        "sections":    sections,
        "char_count":  len(ctx),
        "context":     ctx,
    })

# ── End 分層記憶 API ───────────────────────────────────────────────────────────


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
    # 支援中文檔名，但需排除非法檔名字元與路徑穿越
    if ".." in sid or "/" in sid or "\\" in sid or not sid.strip() or any(c in sid for c in '<>:"/\\|?*'):
        return web.json_response({"error": "invalid name"}, status=400)
    data = await request.json()
    content = data.get("content", "")
    SOULS_DIR.mkdir(parents=True, exist_ok=True)
    (SOULS_DIR / f"{sid}.md").write_text(content, encoding="utf-8")
    return web.json_response({"ok": True})

async def handle_soul_rename(request: web.Request) -> web.Response:
    old_id = request.match_info["id"]
    if old_id.lower().endswith(".md"):
        old_id = old_id[:-3]
    data = await request.json()
    new_id = data.get("new_name", "").strip()
    if new_id.lower().endswith(".md"):
        new_id = new_id[:-3]
    new_id = new_id.strip()
    if not new_id:
        return web.json_response({"error": "empty name"}, status=400)
    old_file = SOULS_DIR / f"{old_id}.md"
    new_file = SOULS_DIR / f"{new_id}.md"
    if not old_file.exists():
        return web.json_response({"error": "not found"}, status=404)
    if new_file.exists() and old_id != new_id:
        return web.json_response({"error": "already exists"}, status=409)
    old_file.rename(new_file)
    return web.json_response({"ok": True, "id": new_id})

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
    
    cron = await _natural_to_cron(cron)
    
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
    asyncio.create_task(run_schedule_prompt(target))
    target["last_run"] = datetime.now().isoformat()
    save_schedules(schedules)
    return web.json_response({"ok": True})

async def handle_schedules_parse_cron(request: web.Request) -> web.Response:
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return web.json_response({"cron": ""})

    prompt = f"""You are a Cron translation assistant. Convert the user's natural language time description into a standard 5-field Cron expression (minute hour day-of-month month day-of-week).
Only output the Cron expression itself, without any other explanation, markdown markup or extra characters.
Examples:
Input: 每天早上九點
Output: 0 9 * * *
Input: 每週一到週五的下午五點半
Output: 30 17 * * 1-5
Input: 每 5 分鐘
Output: */5 * * * *

Now convert this: {text}"""

    cron_result = ""
    api_key = _resolve_api_key()
    if api_key:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": prompt}]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        cron_result = res["content"][0]["text"].strip()
        except Exception as e:
            print(f"[parse-cron] HTTP API failed: {e}")

    if not cron_result:
        # Fallback to CLAUDE_BIN
        cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json"]
        try:
            env = {**os.environ}
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env
            )
            stdout, _ = await proc.communicate()
            text_parts = []
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line.strip())
                    if event.get("type") == "text":
                        text_parts.append(event.get("text", ""))
                except Exception:
                    pass
            cron_result = "".join(text_parts).strip()
        except Exception as e:
            print(f"[parse-cron] CLAUDE_BIN failed: {e}")

    if cron_result:
        cron_result = cron_result.replace("`", "").replace("'", "").replace("\"", "").strip()
        if "output:" in cron_result.lower():
            cron_result = cron_result.split(":")[-1].strip()

    return web.json_response({"cron": cron_result})

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


# ── #19 LINE Bot Webhook ──────────────────────────────────────────────────────

def _load_pi_soul() -> str:
    soul_path = CLAUDE_HOME / "souls" / "Pi.md"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return "You are Pi, a professional and efficient AI butler."

def _verify_line_signature(body: bytes, signature: str) -> bool:
    secret = _load_config().get("lineChannelSecret", "").strip()
    if not secret:
        return True  # skip verification if not configured
    import hmac, hashlib, base64
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(expected, signature)

async def _line_reply(reply_token: str, text: str) -> None:
    token = _load_config().get("lineChannelAccessToken", "").strip()
    if not token:
        return
    chunks = [text[i:i+4500] for i in range(0, len(text), 4500)]
    messages = [{"type": "text", "text": c} for c in chunks[:5]]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.line.me/v2/bot/message/reply",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"replyToken": reply_token, "messages": messages},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _log(f"[line] reply error {resp.status}: {await resp.text()}")
    except Exception as e:
        _log(f"[line] reply exception: {e}")

async def _line_run_claude(user_message: str) -> str:
    soul = _load_pi_soul()
    prompt = f"<instructions>\n{soul}\n</instructions>\n\n{user_message}"
    env = os.environ.copy()
    key = _resolve_api_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    try:
        proc = await asyncio.create_subprocess_exec(
            CLAUDE_BIN, "-p", prompt, "--output-format", "json",
            "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
        output = json.loads(stdout.decode("utf-8", errors="replace"))
        return output.get("result", "[Pi 無回應]").strip()
    except Exception as e:
        _log(f"[line] claude call exception: {e}")
        return "[Pi 暫時無法回應，請稍後再試]"

async def handle_line_webhook(request: web.Request) -> web.Response:
    body = await request.read()
    sig  = request.headers.get("X-Line-Signature", "")
    if not _verify_line_signature(body, sig):
        return web.Response(status=400, text="invalid signature")
    try:
        data = json.loads(body)
    except Exception:
        return web.Response(status=400, text="invalid json")
    allowed = _load_config().get("lineAllowedUsers", [])
    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        sender_id = event.get("source", {}).get("userId", "")
        if allowed and sender_id not in allowed:
            _log(f"[line] blocked user: {sender_id}")
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue
        text        = msg.get("text", "").strip()
        reply_token = event.get("replyToken", "")
        if text and reply_token:
            asyncio.create_task(_process_line_message(text, reply_token))
    return web.Response(status=200, text="OK")

async def _process_line_message(text: str, reply_token: str) -> None:
    _log(f"[line] received: {text[:50]}")
    reply = await _line_run_claude(text)
    await _line_reply(reply_token, reply)
    _log(f"[line] replied, length={len(reply)}")


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
    _init_db()
    _migrate_db()
    _backfill_project_paths()
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
        ("PATCH",  "/api/souls/{id}",     handle_soul_rename),
        ("DELETE", "/api/souls/{id}",  handle_soul_delete),
        ("GET",    "/api/sessions",                      handle_sessions),
        ("POST",   "/api/sessions/resume",              handle_resume_session),
        ("DELETE", "/api/sessions/{id}",               handle_session_delete),
        ("PATCH",  "/api/sessions/{id}",               handle_session_rename),
        ("POST",   "/api/sessions/{id}/auto-title",    handle_session_auto_title),
        ("GET",    "/api/agents",             handle_agents),
        ("GET",    "/api/agents/registry",     handle_agents_registry),
        ("POST",   "/api/agents",             handle_agent_post),
        ("GET",    "/api/agents/{id}",         handle_agent_get),
        ("PUT",    "/api/agents/{id}",         handle_agent_put),
        ("DELETE", "/api/agents/{id}",         handle_agent_delete),
        ("GET",    "/api/skills",              handle_skills),
        ("GET",    "/api/skills/{id}",         handle_skill_get),
        ("PUT",    "/api/skills/{id}",         handle_skill_put),
        ("POST",   "/api/skills/generate",     handle_skill_generate),
        ("GET",    "/api/teams",               handle_teams),
        ("POST",   "/api/teams",               handle_team_post),
        ("GET",    "/api/teams/{id}",          handle_team_get),
        ("PUT",    "/api/teams/{id}",          handle_team_put),
        ("DELETE", "/api/teams/{id}",          handle_team_delete),
        ("POST",   "/api/team/run",               handle_team_run_post),
        ("GET",    "/api/team/run/{run_id}",       handle_team_run_get),
        ("GET",    "/api/team/run/{run_id}/stream",handle_team_run_stream),
        ("DELETE", "/api/team/run/{run_id}",       handle_team_run_cancel),
        ("POST",   "/api/hr/dispatch",         handle_hr_dispatch),
        ("GET",    "/api/memory",          handle_memory),
        ("GET",    "/api/schedules",       handle_schedules_get),
        ("POST",   "/api/schedules",       handle_schedules_post),
        ("POST",   "/api/schedules/parse-cron", handle_schedules_parse_cron),
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
        ("POST",   "/api/line/webhook",       handle_line_webhook),
        ("GET",    "/api/debug-dump",         handle_debug_dump),
        # 分層記憶 API
        ("GET",    "/api/mem/overview",                              handle_mem_overview),
        ("GET",    "/api/mem/preview",                               handle_mem_preview),
        ("GET",    "/api/mem/user",                                  handle_mem_user_get),
        ("PUT",    "/api/mem/user",                                  handle_mem_user_put),
        ("GET",    "/api/mem/system",                                handle_mem_system_get),
        ("PUT",    "/api/mem/system",                                handle_mem_system_put),
        ("GET",    "/api/mem/agents",                                handle_mem_agents_list),
        ("GET",    "/api/mem/agents/{id}",                           handle_mem_agent_get),
        ("PUT",    "/api/mem/agents/{id}",                           handle_mem_agent_put),
        ("GET",    "/api/mem/agents/{id}/projects",                  handle_mem_agent_projects_list),
        ("GET",    "/api/mem/agents/{id}/projects/{slug}",           handle_mem_agent_project_get),
        ("PUT",    "/api/mem/agents/{id}/projects/{slug}",           handle_mem_agent_project_put),
        ("GET",    "/api/mem/teams",                                 handle_mem_teams_list),
        ("GET",    "/api/mem/teams/{id}",                            handle_mem_team_get),
        ("PUT",    "/api/mem/teams/{id}",                            handle_mem_team_put),
        ("GET",    "/api/mem/teams/{id}/projects",                   handle_mem_team_projects_list),
        ("GET",    "/api/mem/teams/{id}/projects/{slug}",            handle_mem_team_project_get),
        ("PUT",    "/api/mem/teams/{id}/projects/{slug}",            handle_mem_team_project_put),
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
                    asyncio.create_task(run_schedule_prompt(sc))
            except Exception:
                pass
        if changed:
            save_schedules(schedules)


async def _send_line_message(to: str, text: str) -> None:
    token = _load_config().get("lineChannelAccessToken", "").strip()
    if not token:
        print("[schedule] lineChannelAccessToken not set in claude-desktop-config.json")
        return
    if not to:
        print("[schedule] LINE recipient (to) is empty")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    chunks = [text[i:i+4500] for i in range(0, len(text), 4500)]
    messages = [{"type": "text", "text": chunk} for chunk in chunks[:5]]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json={"to": to, "messages": messages}) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[schedule] LINE API error {resp.status}: {body}")
                else:
                    print(f"[schedule] LINE message sent to {to}")
    except Exception as e:
        print(f"[schedule] Failed to send LINE message: {e}")


async def run_schedule_prompt(schedule: dict) -> None:
    prompt = schedule["prompt"] if isinstance(schedule, dict) else schedule
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
        )
        stdout, _ = await proc.communicate()
        result_text = ""
        try:
            output = json.loads(stdout.decode("utf-8", errors="replace"))
            result_text = output.get("result", "")
        except Exception:
            pass
        print(f"[schedule] Prompt finished, result length: {len(result_text)}")
        if isinstance(schedule, dict):
            delivery = schedule.get("delivery", {})
            if delivery.get("channel") == "line" and result_text:
                await _send_line_message(delivery.get("to", ""), result_text)
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
    web.run_app(app, host="0.0.0.0", port=8765)
