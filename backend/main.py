import asyncio
import base64
import html
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
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

try:
    from claude_agent_sdk import (
        ClaudeSDKClient, ClaudeAgentOptions,
        AssistantMessage, ResultMessage, TextBlock, UserMessage,
        PermissionResultAllow, PermissionResultDeny,
    )
    from session_pool import SessionPool
    HAS_AGENT_SDK = True
except ImportError:
    HAS_AGENT_SDK = False

try:
    import database as _db_mod
except ImportError:
    import backend.database as _db_mod

import sys
import platform

import types

class _CustomModule(types.ModuleType):
    def __setattr__(self, name, value):
        # 同步設定給所有 database 模組實體
        for mod_name, mod in list(sys.modules.items()):
            if mod_name == "database" or mod_name == "backend.database" or mod_name.endswith(".database"):
                if hasattr(mod, name) or name in ("CLAUDE_HOME", "AGENTS_DIR", "SKILLS_DIR", "TEAMS_DIR", "SOULS_DIR", "CONFIG_FILE", "_INDEX_DB"):
                    setattr(mod, name, value)
                    if name == "CLAUDE_HOME" and hasattr(mod, "update_paths"):
                        mod.update_paths(value)
                        
        # 當 CLAUDE_HOME 被修改時，也重新計算並綁定所有衍生路徑
        if name == "CLAUDE_HOME":
            self.__dict__["AGENTS_DIR"] = value / "agents"
            self.__dict__["SKILLS_DIR"] = value / "skills"
            self.__dict__["TEAMS_DIR"] = value / "teams"
            self.__dict__["SESSIONS_DIR"] = value / "sessions"
            self.__dict__["SCHEDULES_FILE"] = value / "schedules.json"
            self.__dict__["SESSION_NAMES_FILE"] = value / "session_names.json"
            self.__dict__["SOUL_FILE"] = value / "soul.md"
            self.__dict__["SOULS_DIR"] = value / "souls"

        super().__setattr__(name, value)

sys.modules[__name__].__class__ = _CustomModule

_curr = sys.modules[__name__]
for _k in dir(_db_mod):
    if not _k.startswith("__"):
        setattr(_curr, _k, getattr(_db_mod, _k))



# ─────────────────────────────────────────────────────────────────────────────

_SESSIONS_FILE = CLAUDE_HOME / "active_sessions.json"
_SESSIONS_META_FILE = CLAUDE_HOME / "active_sessions_meta.json"
_SESSION_MAX_AGE = 7 * 24 * 3600

class _PersistentSessions(dict):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._touched = {}

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._touched[k] = time.time()
        self._prune()
        self._save()

    def pop(self, k, default=None):
        result = super().pop(k, default)
        self._touched.pop(k, None)
        self._save()
        return result

    def _prune(self):
        now = time.time()
        stale = [k for k in list(self.keys()) if now - self._touched.get(k, now) > _SESSION_MAX_AGE]
        for k in stale:
            super().pop(k, None)
            self._touched.pop(k, None)

    def _save(self):
        try:
            _safe_write_text(_SESSIONS_FILE, json.dumps(dict(self), ensure_ascii=False))
            _safe_write_text(_SESSIONS_META_FILE, json.dumps(self._touched, ensure_ascii=False))
        except Exception:
            pass

active_sessions: dict[str, str] = _PersistentSessions()   # client_id -> claude session_id
if _SESSIONS_FILE.exists():
    try:
        active_sessions.update(json.loads(_SESSIONS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
if _SESSIONS_META_FILE.exists():
    try:
        active_sessions._touched.update(json.loads(_SESSIONS_META_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
_now = time.time()
for _k in active_sessions.keys():
    active_sessions._touched.setdefault(_k, _now)
active_sessions._prune()

active_procs:    dict[str, asyncio.subprocess.Process] = {}  # client_id -> proc

_team_pool = SessionPool() if HAS_AGENT_SDK else None  # persistent ClaudeSDKClient pool for team chat/execute
_mcp_procs:      dict[str, asyncio.subprocess.Process] = {}  # mcp name -> proc
_mcp_logs:       dict[str, list[str]] = {}                   # mcp name -> log lines
pending_permissions: dict[str, dict] = {}                    # request_id -> dict with process, event, etc.

# Usage API 快取（5 分鐘）
_usage_cache: dict = {"data": None, "expires": 0.0}
_codex_usage_cache: dict = {"data": None, "expires": 0.0}

# Local MCP config (Docker metadata, compose paths, etc.)

from helpers import _read_agent_body, _read_skills_content, _team_dict, _agent_dict, _parse_yaml_simple, safe_kill_process, wrap_cmd

import atexit
import signal

def cleanup_subprocesses():
    print("[cleanup] Python backend exiting... cleaning up all child processes", flush=True)
    for k in list(active_procs.keys()):
        proc = active_procs.pop(k, None)
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
    for k in list(_mcp_procs.keys()):
        proc = _mcp_procs.pop(k, None)
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
    try:
        import routes.teams
        for k in list(routes.teams._team_run_processes.keys()):
            proc = routes.teams._team_run_processes.pop(k, None)
            if proc:
                try:
                    safe_kill_process(proc)
                except Exception:
                    pass
    except Exception:
        pass

atexit.register(cleanup_subprocesses)

def signal_handler(signum, frame):
    cleanup_subprocesses()
    sys.exit(0)

try:
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
except Exception:
    pass

_CLI_NOISE_PATTERNS = ("no stdin data received",)

def _is_cli_noise(raw: str) -> bool:
    low = raw.lower()
    return any(p in low for p in _CLI_NOISE_PATTERNS)

def load_session_names() -> dict:
    if SESSION_NAMES_FILE.exists():
        try: return json.loads(SESSION_NAMES_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def save_session_names(names: dict) -> None:
    _safe_write_text(SESSION_NAMES_FILE, json.dumps(names, ensure_ascii=False, indent=2))

def migrate_soul():
    if not SOULS_DIR.exists():
        SOULS_DIR.mkdir(parents=True, exist_ok=True)
        if SOUL_FILE.exists():
            try:
                shutil.copy(SOUL_FILE, SOULS_DIR / "default.md")
            except Exception:
                pass

def get_agent_soul(agent_id: str) -> str:
    """Soul 與 Agent 為 1:1：只回傳該 agent 專屬的 soul 內容；沒有指定 agent 就不注入任何 soul。"""
    if not agent_id:
        return ""
    f = SOULS_DIR / f"{agent_id}.md"
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        return ""


async def _resolve_agent_engine_and_key(agent_id: str, request_engine: str = ""):
    """比照 routes/teams.py::_agent_run_capture() 已經驗證過的模式：讀
    agent 自己 frontmatter 宣告的 engine:，解析成實際引擎模組，並決定
    api_key 要不要傳（resolve_key() 只解析 Anthropic key，非 claude 引擎
    一律傳空字串，避免誤植進 CODEX_API_KEY 蓋掉正常的 codex login 憑證）。

    handle_chat／handle_team_chat／handle_team_execute 這三個進入點原本
    完全沒有讀取 agent 的 engine: 欄位，寫死呼叫 Claude——這個 helper是
    補上這個缺口用的共用邏輯，避免三處各自重複實作。沒有 agent_id（沒有
    activated agent）時解析成 request_engine（Settings 的「兩者都開放時
    的預設引擎」），沒有 request_engine 時才落到 DEFAULT_ENGINE_NAME
    （目前是 "codex"）。

    2026-07-13：request_engine 原本寫死空字串，代表這三個進入點完全沒讀
    過使用者在 Settings 選的「兩者都開放時的預設引擎」，畫面上那個下拉
    選單對這三處毫無作用——這裡補上，呼叫端要從 request body 讀
    agent_engine 欄位傳進來（比照 routes/teams.py::_agent_run_capture()
    早就在用的同一個參數名稱）。

    resolve_engine_name_gated() 算出來的偏好引擎，還會再疊加一層
    apply_availability_fallback()（engines/availability.py）：偏好引擎
    現在真的可用就直接通過（notice 為 None，行為跟這次改動之前完全一樣）；
    不可用就切到另一個可用的引擎並回傳一句可以直接顯示給使用者的提示；
    兩個都不可用會丟出 NoEngineAvailableError，呼叫端要自行處理。

    這裡是 Settings 的「執行引擎範圍」鎖定（database.get_engine_mode()）
    第一次真正生效的地方——這三個進入點原本完全沒有任何限制邏輯，agent
    自己的 engine: 宣告無條件生效；鎖定成 'claude'／'codex' 時，
    resolve_engine_name_gated() 會直接收斂成那個值，agent_own_engine
    完全不看，apply_availability_fallback() 也不會偷偷切去被鎖定排除的
    那個引擎墊背。
    """
    from database import get_engine_mode
    from engines.registry import resolve_engine_name_gated, get_engine
    from engines.availability import apply_availability_fallback

    agent_own_engine = ""
    if agent_id:
        agent_file = AGENTS_DIR / f"{agent_id}.md"
        if agent_file.exists():
            try:
                agent_own_engine = _agent_dict(agent_file).get("engine", "")
            except Exception:
                pass
    mode = get_engine_mode()
    allowed = frozenset({mode}) if mode in ("claude", "codex") else frozenset({"claude", "codex"})
    preferred_name = resolve_engine_name_gated(agent_own_engine, request_engine, mode)
    final_name, notice = await apply_availability_fallback(preferred_name, allowed)
    engine = get_engine(final_name)
    # 三選一：Claude 用 _resolve_api_key()、Codex 用 _resolve_codex_api_key()，
    # 其餘引擎一律傳空字串——兩個 resolver 各自只解析自己的 config key，
    # 完全不共用邏輯，避免哪個引擎的 key 誤植進另一邊的環境變數。
    engine_api_key = (
        _resolve_api_key() if engine.name == "claude"
        else _resolve_codex_api_key() if engine.name == "codex"
        else ""
    )
    return engine, engine_api_key, notice


def load_schedules() -> list:
    if SCHEDULES_FILE.exists():
        try:
            return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def save_schedules(data: list) -> None:
    _safe_write_text(SCHEDULES_FILE, json.dumps(data, ensure_ascii=False, indent=2))

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
    proc = None
    try:
        cmd = wrap_cmd(CLAUDE_BIN, ["-p", prompt, "--output-format", "text"])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
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

# ── Locate codex executable ───────────────────────────
def find_codex() -> str:
    found = shutil.which("codex")
    if found:
        return found
    candidates = [
        # Windows
        Path.home() / "AppData" / "Roaming" / "npm" / "codex.cmd",
        Path.home() / "AppData" / "Local" / "nvm" / "nodejs" / "codex.cmd",
        # macOS (Homebrew + npm global)
        Path("/usr/local/bin/codex"),
        Path("/opt/homebrew/bin/codex"),
        Path.home() / ".npm-global" / "bin" / "codex",
        # Linux
        Path("/usr/bin/codex"),
        Path.home() / ".local" / "bin" / "codex",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "codex"   # fallback, let OS resolve

CODEX_BIN = find_codex()

def _detect_claude_version() -> str:
    try:
        import subprocess
        r = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True, text=True, timeout=5)
        v = r.stdout.strip().split()[-1] if r.returncode == 0 and r.stdout.strip() else ""
        return v or "2.1.196"
    except Exception:
        return "2.1.196"

CLAUDE_VERSION = _detect_claude_version()


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


def _resolve_codex_api_key() -> str:
    """跟 _resolve_api_key() 幾乎一樣，只是讀 codexApiKeyCmd 這個獨立的
    config key、回傳的值只會被拿去設 CODEX_API_KEY——刻意跟 Claude 那把
    key 的 resolver 完全分開，不共用任何邏輯，避免哪天改壞了互相污染。"""
    cfg = _load_config()
    cmd = cfg.get("codexApiKeyCmd", "").strip()
    if not cmd:
        return ""
    import subprocess
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[codexApiKeyCmd] error: {e}")
        return ""


def build_memory_context(agent_id: str, cwd: str, query: str = "") -> str:
    """
    使用 MemoryAgent 進行動態分層加載與智能 Context 裁剪與 RAG 召回。
    """
    from memory_agent import MemoryAgent
    slug = _encode_slug(cwd) if cwd else ""
    agent_dir = _agent_memory_dir(agent_id) if agent_id else None
    
    agent = MemoryAgent(
        global_mem_dir=_global_memory_dir(),
        agent_mem_dir=agent_dir,
        cwd_slug=slug
    )
    return agent.build_smart_context(agent_id=agent_id, max_chars=16000, query=query)


def build_team_memory_context(
    team_id: str,
    all_member_ids: list[str],
    current_agent_id: str,
    cwd: str,
    members_meta: "list[dict] | None" = None,
    query: str = "",
) -> str:
    """
    組裝 Team Run 的記憶 context。
    使用 MemoryAgent 來對當前成員的 Identity 與 Project 內部日誌進行智能 Paging 與 RAG 相似度檢索！
    """
    from memory_agent import MemoryAgent
    slug = _encode_slug(cwd) if cwd else ""
    agent_dir = _agent_memory_dir(current_agent_id) if current_agent_id else None
    
    agent_mem = MemoryAgent(
        global_mem_dir=_global_memory_dir(),
        agent_mem_dir=agent_dir,
        team_mem_dir=_team_memory_dir(team_id) if team_id else None,
        cwd_slug=slug
    )
    
    # 1. 取得 MemoryAgent 對此 agent 構建的智能記憶上下文 (包含 RAG 檢索)
    agent_ctx = agent_mem.build_smart_context(agent_id=current_agent_id, max_chars=12000, query=query)
    
    sections = []
    if agent_ctx:
        sections.append(agent_ctx)
        
    # 2. 注入 Team 層級的記憶 (Team Shared + Team Project)
    team_shared = _read_md(_team_memory_dir(team_id) / "shared.md")
    if team_shared:
        sections.append(f"[Team Memory — {team_id}]\n{team_shared}")

    if slug:
        team_proj = _read_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md")
        if team_proj:
            sections.append(f"[Team Project Memory — {team_id} / {slug}]\n{team_proj}")

    # 3. 成員角色清單
    peer_lines: list[str] = []
    if members_meta:
        for m in members_meta:
            mid = m.get("agent", "")
            role = m.get("role", "")
            if mid and mid != current_agent_id:
                peer_lines.append(f"- @{mid}（{role}）")
    else:
        for mid in all_member_ids:
            if mid != current_agent_id:
                peer_lines.append(f"- @{mid}")
    if peer_lines:
        sections.append("[Team 成員]\n" + "\n".join(peer_lines))

    return "\n\n---\n\n".join(sections) if sections else ""


async def handle_chat(request: web.Request) -> web.StreamResponse:
    data      = await request.json()
    message      = data.get("message", "")
    client_id    = data.get("client_id", "default")
    agent        = data.get("agent", "")
    cwd_override = data.get("cwd", "")
    bin_override = data.get("claude_bin", "")
    codex_bin_override = data.get("codex_bin", "")
    agent_engine = data.get("agent_engine", "")
    attachments  = data.get("attachments", [])
    model        = data.get("model", "")
    effort       = data.get("effort", "")
    permission_mode = data.get("permission_mode", "")
    team_id      = data.get("team_id", "")

    claude_bin = bin_override if bin_override else CLAUDE_BIN
    cwd        = cwd_override if (cwd_override and Path(cwd_override).is_dir()) else str(Path.home())

    team_info = None
    if team_id:
        f_team = TEAMS_DIR / f"{team_id}.yaml"
        if f_team.exists():
            try:
                team_info = _team_dict(f_team)
            except Exception:
                pass

    async def _build_full_message() -> str:
        """組裝首 Turn 用的完整 prompt（soul + team + memory + agent def）。"""
        soul = get_agent_soul(agent)
        fm = f"[System Persona]\n{soul}\n\n{message}" if soul else message

        if team_id and team_info:
            all_members = [m["agent"] for m in team_info.get("members", [])]
            mem_ctx = await asyncio.to_thread(
                build_team_memory_context, team_id, all_members, agent, cwd,
                members_meta=team_info.get("members", []), query=message
            )
            team_name = team_info.get("name", team_id)
            members_str = "\n".join([f"- @{m['agent']} (職責: {m['role']})" for m in team_info.get("members", [])])
            team_prompt = (
                f"[團隊組長身分指引]\n"
                f"你現在是團隊「{team_name}」的組長（Team Leader）。\n"
                f"你的團隊成員如下：\n{members_str}\n"
                f"當使用者交辦任務時，請以團隊組長的角色進行回覆與規畫。你可以運用其他組員的專長來協助引導對話與思考。\n\n"
            )
            fm = team_prompt + fm
        else:
            mem_ctx = await asyncio.to_thread(build_memory_context, agent, cwd, query=message)

        if mem_ctx:
            fm = f"[Memory Context]\n{mem_ctx}\n\n---\n\n{fm}"

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
                        fm = f"[代理人：{agent}]\n{body}\n\n---\n\n{fm}"
                    # Skill 內容以前只當 metadata 標籤存在，從沒被讀出來
                    # 塞進 prompt，真正生效與否完全依賴底層 CLI 自己原生
                    # 的 slash-skill 機制。這裡改成 app 自己讀內容手動折
                    # 進去，讓 skill 對兩個引擎都真正生效。
                    skills_content = _read_skills_content(SKILLS_DIR, _agent_dict(agent_file).get("skills", []))
                    if skills_content:
                        fm = f"[Skills]\n{skills_content}\n\n---\n\n{fm}"
                except Exception:
                    pass
        return fm

    response = web.StreamResponse(headers={
        "Content-Type":    "text/event-stream",
        "Cache-Control":   "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    has_persisted = client_id in active_sessions
    in_pool_now = HAS_AGENT_SDK and _team_pool is not None and _team_pool.has(client_id)
    use_pool = HAS_AGENT_SDK and _team_pool is not None and not attachments

    async def _run_pooled(prompt_to_send: str, resume_target: "str | None") -> None:
        in_pool = _team_pool.has(client_id)
        opts = None
        if not in_pool:
            env = {**os.environ}
            api_key = _resolve_api_key()
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            opts = ClaudeAgentOptions(
                cwd=cwd,
                env=env,
                model=(model if model and model not in ("sonnet", "") else None),
                effort=(effort if effort and effort != "medium" else None),
                permission_mode=(permission_mode if permission_mode and permission_mode not in ("default", "") else None),
                resume=resume_target,
            )

        client = await _team_pool.get_or_create(client_id, opts)
        try:
            await client.query(prompt_to_send)
            async for message_obj in client.receive_response():
                if isinstance(message_obj, AssistantMessage):
                    text_blocks = [b.text for b in message_obj.content if isinstance(b, TextBlock)]
                    if text_blocks:
                        env_msg = {"type": "assistant", "message": {"content": [{"type": "text", "text": t} for t in text_blocks]}}
                        await response.write(f"data: {json.dumps(env_msg)}\n\n".encode())
                    for b in message_obj.content:
                        if type(b).__name__ == "ToolUseBlock":
                            env_msg = {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                            await response.write(f"data: {json.dumps(env_msg)}\n\n".encode())
                elif isinstance(message_obj, UserMessage):
                    content = message_obj.content if isinstance(message_obj.content, list) else []
                    result_blocks = [b for b in content if type(b).__name__ == "ToolResultBlock"]
                    if result_blocks:
                        env_msg = {
                            "type": "user",
                            "message": {"content": [
                                {"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content}
                                for b in result_blocks
                            ]}
                        }
                        await response.write(f"data: {json.dumps(env_msg)}\n\n".encode())
                elif isinstance(message_obj, ResultMessage):
                    active_sessions[client_id] = message_obj.session_id
                    usage = message_obj.usage or {}
                    env_msg = {
                        "type": "result",
                        "total_cost_usd": message_obj.total_cost_usd or 0,
                        "usage": {
                            "input_tokens": usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
                            "output_tokens": usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
                        },
                    }
                    await response.write(f"data: {json.dumps(env_msg)}\n\n".encode())
        except Exception:
            # force=True：這是已知壞掉的連線要立刻清掉，不是機會性的 idle 回收，
            # 不該被「還在使用中」的 busy 檢查擋下（此時 busy 尚未被下面的
            # finally release）。
            await _team_pool.evict(client_id, force=True)
            raise
        finally:
            _team_pool.release(client_id)

    async def _run_legacy(full_message: str) -> None:
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
        if client_id in active_sessions:
            cmd += ["--resume", active_sessions[client_id]]

        proc = None
        try:
            env = {**os.environ}
            api_key = _resolve_api_key()
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            cmd = wrap_cmd(cmd[0], cmd[1:])
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
                if _is_cli_noise(raw):
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
        finally:
            active_procs.pop(client_id, None)
            if proc and proc.returncode is None:
                safe_kill_process(proc)

    async def _run_engine_turn(engine, engine_api_key: str, full_message: str) -> None:
        """Agent 自己宣告了非 Claude 的 engine: 時走這裡——完全跳過
        SessionPool/ClaudeSDKClient（Anthropic 自家 SDK，其他引擎沒有對應
        物），直接呼叫 engines/<name>_engine.py 的 run_turn()。事件格式
        沿用 _run_pooled() 已經在用、前端也已經在消費的 envelope，前端
        完全不用改。"""
        async def _on_text(chunk: str) -> None:
            env_msg = {"type": "assistant", "message": {"content": [{"type": "text", "text": chunk}]}}
            await response.write(f"data: {json.dumps(env_msg)}\n\n".encode())

        def _on_process(proc) -> None:
            active_procs[client_id] = proc

        try:
            result = await engine.run_turn(
                prompt=full_message, cwd=cwd, model=model, permission_mode=permission_mode,
                resume_session_id=active_sessions.get(client_id), api_key=engine_api_key,
                on_text=_on_text, on_process=_on_process, attachments=attachments,
                bin_override=(codex_bin_override if engine.name == "codex" else ""),
            )
        finally:
            active_procs.pop(client_id, None)

        if result.session_id:
            active_sessions[client_id] = result.session_id
        if result.error:
            payload = json.dumps({"type": "error", "text": result.error})
            await response.write(f"data: {payload}\n\n".encode())

    try:
        engine, engine_api_key, engine_notice = await _resolve_agent_engine_and_key(agent, agent_engine)
        if engine_notice:
            notice_msg = {"type": "assistant", "message": {"content": [{"type": "text", "text": engine_notice}]}}
            await response.write(f"data: {json.dumps(notice_msg)}\n\n".encode())
        if engine.name != "claude":
            full_message = message if client_id in active_sessions else await _build_full_message()
            await _run_engine_turn(engine, engine_api_key, full_message)
        elif use_pool:
            needs_full_rebuild = not (in_pool_now or has_persisted)
            prompt_to_send = await _build_full_message() if needs_full_rebuild else message
            resume_target = None if (in_pool_now or needs_full_rebuild) else active_sessions.get(client_id)
            try:
                await _run_pooled(prompt_to_send, resume_target)
            except ConnectionError:
                # 客戶端已斷線，pool 連線已在 _run_pooled 內 evict；不重跑，避免重複副作用
                return response
            except Exception:
                active_sessions.pop(client_id, None)
                await _run_legacy(await _build_full_message())
        else:
            full_message = message if client_id in active_sessions else await _build_full_message()
            await _run_legacy(full_message)
        await response.write(b'data: {"type":"done"}\n\n')
    except Exception as e:
        try:
            payload = json.dumps({"type": "error", "text": str(e)})
            await response.write(f"data: {payload}\n\n".encode())
        except Exception:
            pass

    return response


async def handle_team_chat(request: web.Request) -> web.StreamResponse:
    data      = await request.json()
    message      = data.get("message", "")
    client_id    = data.get("client_id", "default")
    team_id      = data.get("team_id", "")
    cwd_override = data.get("cwd", "")
    bin_override = data.get("claude_bin", "")
    codex_bin_override = data.get("codex_bin", "")
    agent_engine = data.get("agent_engine", "")
    attachments  = data.get("attachments", [])
    model        = data.get("model", "")
    effort       = data.get("effort", "")
    permission_mode = data.get("permission_mode", "")

    claude_bin = bin_override if bin_override else CLAUDE_BIN
    cwd        = cwd_override if (cwd_override and Path(cwd_override).is_dir()) else str(Path.home())

    team_info = None
    if team_id:
        f_team = TEAMS_DIR / f"{team_id}.yaml"
        if f_team.exists():
            try:
                team_info = _team_dict(f_team)
            except Exception:
                pass

    if not team_info:
        # fallback to minimal JSON error response
        response = web.StreamResponse(headers={
            "Content-Type":    "text/event-stream",
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        payload = json.dumps({"type": "error", "text": "team not found or invalid"})
        await response.write(f"data: {payload}\n\n".encode())
        await response.write(b'data: {"type":"done"}\n\n')
        return response

    members = team_info.get("members", [])
    if not members:
        response = web.StreamResponse(headers={
            "Content-Type":    "text/event-stream",
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        payload = json.dumps({"type": "error", "text": "team has no members"})
        await response.write(f"data: {payload}\n\n".encode())
        await response.write(b'data: {"type":"done"}\n\n')
        return response
    
    leader_agent_id = team_info.get("leader", "") or members[0]["agent"]
    member_agent_ids = [m["agent"] for m in members]

    response = web.StreamResponse(headers={
        "Content-Type":    "text/event-stream",
        "Cache-Control":   "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    async def run_single_agent(
        agent_id: str,
        prompt_text: str,
        is_leader: bool,
        is_first_turn: bool = True,
    ) -> tuple[str, str]:
        """
        呼叫單一 agent。優先透過 session_pool 重用長駐連線（不重開 subprocess）；
        沒有 SDK 或帶附件時退回舊的一次性 subprocess + --resume 模式。
        """
        import hashlib
        cwd_hash = hashlib.md5(cwd.encode("utf-8")).hexdigest()[:8] if cwd else "default"
        session_key = f"{client_id}_{agent_id}_{cwd_hash}"

        async def _build_full_prompt(hist: str) -> str:
            """組裝首 Turn 用的完整 prompt。"""
            mem_ctx = await asyncio.to_thread(
                build_team_memory_context, team_id, member_agent_ids, agent_id, cwd,
                members_meta=members,
                query=prompt_text
            )
            team_name   = team_info.get("name", team_id)
            members_str = "\n".join([f"- @{m['agent']} (職責: {m['role']})" for m in members])

            if is_leader:
                persona = (
                    f"[團隊組長身分指引]\n"
                    f"你現在是團隊「{team_name}」的組長（Team Leader）代號 @{agent_id}。\n"
                    f"你的團隊成員如下：\n{members_str}\n"
                    f"你的職責是協調整個團隊。當使用者交辦任務時：\n"
                    f"1. 請先在回覆中與相關成員對話以討論方案。如果你需要某位成員發言，請在你的回覆中明確 @成員代號（例如 @{members[0]['agent']} 你的想法是什麼？）。\n"
                    f"2. 討論完畢且有了明確的實作規劃後，請在你的回覆中加入 `[CREATE_PROJECT: 專案名稱]` 這個標籤（其中 專案名稱 請使用小寫英文底線，如 `python_spider`），系統會自動為此建立目錄並進行後續的多 Agent 分工協作執行。\n"
                    f"3. 請注意，你在發言中只能 @ 成員列表中的人，不要 @ 不存在的成員。每一次回覆最多只 @ 一位成員提問討論。\n\n"
                )
            else:
                persona = (
                    f"[團隊成員身分指引]\n"
                    f"你現在是團隊「{team_name}」的成員，代號 @{agent_id}。\n"
                    f"你的團隊成員如下：\n{members_str}\n"
                    f"你的組長為 @{leader_agent_id}。現在組長（或團隊）向你提問，請針對提問以你的角色進行回覆，給出專業的意見與討論。請回覆得簡短而專業，不需要 @ 其他人。\n\n"
                )

            fp = persona
            if mem_ctx:
                fp = f"[Memory Context]\n{mem_ctx}\n\n---\n\n{fp}"
            fp = f"{fp}\n\n任務/討論歷史：\n{hist}"

            soul = get_agent_soul(agent_id)
            if soul:
                fp = f"[System Persona]\n{soul}\n\n{fp}"

            agent_file_path = AGENTS_DIR / f"{agent_id}.md"
            if agent_file_path.exists():
                body = _read_agent_body(agent_file_path)
                if body:
                    fp = f"[代理人定義：{agent_id}]\n{body}\n\n---\n\n{fp}"
                skills_content = _read_skills_content(SKILLS_DIR, _agent_dict(agent_file_path).get("skills", []))
                if skills_content:
                    fp = f"[Skills]\n{skills_content}\n\n---\n\n{fp}"
            return fp

        async def _exec_cmd(fp: str, resume_sid: "str | None") -> tuple[list[str], str, bool]:
            """執行 claude CLI（舊模式，一次性 subprocess），回傳 (collected_text, new_session_id, resume_failed)。"""
            cmd = [claude_bin, "-p", fp, "--output-format", "stream-json", "--verbose"]
            if model and model not in ("sonnet", ""):
                cmd += ["--model", model]
            if effort and effort != "medium":
                cmd += ["--effort", effort]
            if permission_mode and permission_mode not in ("default", ""):
                cmd += ["--permission-mode", permission_mode]
            for att in attachments:
                if Path(att).exists():
                    cmd += ["--input-file", att]
            if resume_sid:
                cmd += ["--resume", resume_sid]

            await response.write(f"data: {json.dumps({'type': 'agent_start', 'agent': agent_id})}\n\n".encode())

            env = {**os.environ}
            api_key = _resolve_api_key()
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key

            cmd = wrap_cmd(cmd[0], cmd[1:])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=cwd,
                env=env,
            )
            active_procs[client_id] = proc

            collected: list[str] = []
            new_sid   = ""
            resume_failed = False

            try:
                async for line in proc.stdout:
                    raw = line.decode("utf-8", errors="replace").strip()
                    if not raw:
                        continue
                    if _is_cli_noise(raw):
                        continue
                    if resume_sid and (
                        "session not found" in raw.lower()
                        or "invalid session" in raw.lower()
                        or "no such session" in raw.lower()
                    ):
                        resume_failed = True
                    try:
                        event = json.loads(raw)
                        if event.get("type") == "result" and "session_id" in event:
                            new_sid = event["session_id"]
                            active_sessions[session_key] = new_sid
                        if event.get("type") == "assistant" and event.get("message", {}).get("content"):
                            for block in event["message"]["content"]:
                                if block.get("type") == "text":
                                    collected.append(block["text"])
                                    await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': block['text']})}\n\n".encode())
                        elif event.get("type") == "text":
                            collected.append(event.get("text", ""))
                            await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': event.get('text', '')})}\n\n".encode())
                    except json.JSONDecodeError:
                        collected.append(raw)
                        await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': raw})}\n\n".encode())
                await proc.wait()
            finally:
                active_procs.pop(client_id, None)

            await response.write(f"data: {json.dumps({'type': 'agent_done', 'agent': agent_id})}\n\n".encode())
            return collected, new_sid, resume_failed

        async def _exec_pooled(prompt_to_send: str, resume_target: "str | None") -> tuple[list[str], str]:
            """透過 session_pool 送出，重用長駐連線（不重開 subprocess）。連線斷開/resume 失敗會拋出供外層 fallback。"""
            await response.write(f"data: {json.dumps({'type': 'agent_start', 'agent': agent_id})}\n\n".encode())

            in_pool = _team_pool.has(session_key)
            opts = None
            if not in_pool:
                env = {**os.environ}
                api_key = _resolve_api_key()
                if api_key:
                    env["ANTHROPIC_API_KEY"] = api_key
                opts = ClaudeAgentOptions(
                    cwd=cwd,
                    env=env,
                    model=(model if model and model not in ("sonnet", "") else None),
                    effort=(effort if effort and effort != "medium" else None),
                    permission_mode=(permission_mode if permission_mode and permission_mode not in ("default", "") else None),
                    resume=resume_target,
                )

            client = await _team_pool.get_or_create(session_key, opts)
            collected: list[str] = []
            new_sid = ""
            try:
                await client.query(prompt_to_send)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                collected.append(block.text)
                                await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': block.text})}\n\n".encode())
                    elif isinstance(message, ResultMessage):
                        new_sid = message.session_id
                        active_sessions[session_key] = new_sid
            except Exception:
                await _team_pool.evict(session_key, force=True)
                raise
            finally:
                _team_pool.release(session_key)

            await response.write(f"data: {json.dumps({'type': 'agent_done', 'agent': agent_id})}\n\n".encode())
            return collected, new_sid

        async def _exec_engine_turn(fp: str, resume_sid: "str | None") -> tuple[list[str], str]:
            """Agent 自己宣告了非 Claude 的 engine: 時走這裡——完全跳過
            SessionPool/ClaudeSDKClient（Anthropic 自家 SDK，其他引擎沒有
            對應物），直接呼叫 engines/<name>_engine.py 的 run_turn()。
            事件格式沿用 _exec_cmd() 既有的 SSE envelope，前端不用改。"""
            await response.write(f"data: {json.dumps({'type': 'agent_start', 'agent': agent_id})}\n\n".encode())
            collected: list[str] = []

            async def _on_text(chunk: str) -> None:
                collected.append(chunk)
                await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': chunk})}\n\n".encode())

            def _on_process(proc) -> None:
                active_procs[client_id] = proc

            try:
                result = await engine.run_turn(
                    prompt=fp, cwd=cwd, model=model, permission_mode=permission_mode,
                    resume_session_id=resume_sid, api_key=engine_api_key,
                    on_text=_on_text, on_process=_on_process, attachments=attachments,
                    bin_override=(codex_bin_override if engine.name == "codex" else ""),
                )
            finally:
                active_procs.pop(client_id, None)

            if result.session_id:
                active_sessions[session_key] = result.session_id
            if result.error:
                err_text = f"[Error: {result.error}]"
                collected.append(err_text)
                await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': err_text})}\n\n".encode())

            await response.write(f"data: {json.dumps({'type': 'agent_done', 'agent': agent_id})}\n\n".encode())
            return collected, result.session_id

        has_persisted = session_key in active_sessions
        in_pool_now = HAS_AGENT_SDK and _team_pool is not None and _team_pool.has(session_key)
        use_pool = HAS_AGENT_SDK and _team_pool is not None and not attachments
        engine, engine_api_key, engine_notice = await _resolve_agent_engine_and_key(agent_id, agent_engine)
        if engine_notice:
            await response.write(f"data: {json.dumps({'type': 'text', 'agent': agent_id, 'text': engine_notice})}\n\n".encode())

        if engine.name != "claude":
            if not has_persisted or is_first_turn:
                full_prompt = await _build_full_prompt(prompt_text)
                resume_sid  = None
            else:
                full_prompt = prompt_text
                resume_sid  = active_sessions.get(session_key)
            collected_list, new_session_id = await _exec_engine_turn(full_prompt, resume_sid)
            return "".join(collected_list), new_session_id

        if use_pool:
            needs_full_rebuild = not (in_pool_now or has_persisted)
            prompt_to_send = await _build_full_prompt(prompt_text) if needs_full_rebuild else prompt_text
            resume_target = None if (in_pool_now or needs_full_rebuild) else active_sessions.get(session_key)
            try:
                collected_list, new_session_id = await _exec_pooled(prompt_to_send, resume_target)
                return "".join(collected_list), new_session_id
            except ConnectionError:
                # 客戶端已斷線，pool 連線已在 _exec_pooled 內 evict；不重跑，避免重複副作用
                return "", ""
            except Exception:
                active_sessions.pop(session_key, None)
                full_fallback = await _build_full_prompt(prompt_text)
                collected_list, new_session_id, _ = await _exec_cmd(full_fallback, None)
                return "".join(collected_list), new_session_id

        if not has_persisted or is_first_turn:
            full_prompt = await _build_full_prompt(prompt_text)
            resume_sid  = None
        else:
            full_prompt = prompt_text
            resume_sid  = active_sessions.get(session_key)

        collected_list, new_session_id, resume_failed = await _exec_cmd(full_prompt, resume_sid)
        if resume_failed:
            active_sessions.pop(session_key, None)
            full_fallback = await _build_full_prompt(prompt_text)
            collected_list, new_session_id, _ = await _exec_cmd(full_fallback, None)
        return "".join(collected_list), new_session_id

    import re
    try:
        discussion_history = f"使用者：{message}\n"
        last_increment     = discussion_history  # 每 Turn 傳給後續呼叫的增量部分

        current_agent = leader_agent_id
        is_leader     = True

        for loop_idx in range(10):
            is_first    = loop_idx == 0
            import hashlib
            cwd_hash = hashlib.md5(cwd.encode("utf-8")).hexdigest()[:8] if cwd else "default"
            session_key = f"{client_id}_{current_agent}_{cwd_hash}"
            has_session = session_key in active_sessions

            # 後續 Turn 且有 session：只傳最新增量；否則傳完整 history
            prompt_to_send = last_increment if (has_session and not is_first) else discussion_history

            agent_output, sid = await run_single_agent(
                current_agent, prompt_to_send, is_leader, is_first_turn=is_first
            )
            new_line           = f"@{current_agent}：{agent_output}\n"
            discussion_history += new_line

            if is_leader:
                proj_match = re.search(r"\[CREATE_PROJECT:\s*([a-zA-Z0-9_-]+)\]", agent_output)
                if proj_match:
                    project_name = proj_match.group(1).strip()
                    proj_dir = Path(cwd) / project_name
                    try:
                        proj_dir.mkdir(parents=True, exist_ok=True)
                        await response.write(f"data: {json.dumps({'type': 'project_created', 'project_name': project_name, 'project_path': str(proj_dir)})}\n\n".encode())
                    except Exception as ex:
                        err_text = f"\n[專案目錄建立失敗: {ex}]\n"
                        await response.write(f"data: {json.dumps({'type': 'text', 'agent': leader_agent_id, 'text': err_text})}\n\n".encode())

            next_agent = None
            if is_leader:
                matches = re.findall(r"@([a-zA-Z0-9_-]+)", agent_output)
                for m_id in matches:
                    if m_id in member_agent_ids and m_id != leader_agent_id:
                        next_agent = m_id
                        break

            if next_agent:
                current_agent   = next_agent
                is_leader       = False
                notify_line     = f"\n系統通知：@{leader_agent_id} 請 @{next_agent} 發表意見。\n"
                discussion_history += notify_line
                last_increment  = new_line + notify_line
            else:
                if not is_leader:
                    current_agent   = leader_agent_id
                    is_leader       = True
                    notify_line     = f"\n系統通知：@{current_agent} 請繼續彙整討論並給出結論。\n"
                    discussion_history += notify_line
                    last_increment  = new_line + notify_line
                else:
                    break

        await response.write(b'data: {"type":"done"}\n\n')
    except Exception as e:
        payload = json.dumps({"type": "error", "text": str(e)})
        await response.write(f"data: {payload}\n\n".encode())

    return response


def launch_windows_terminal_monitor(project_path: str, members: list):
    if not members:
        return
    # wt/powershell only exist on native Windows -- this backend usually runs inside
    # a Linux Docker container (dev mode), where the command below fails every call.
    if platform.system() != "Windows":
        return
    
    # 修正 1：先預建所有 log 檔案，防止 PowerShell Get-Content -Wait 因為檔案不存在而報錯
    for m in members:
        agent_id = m["agent"]
        log_file = Path(project_path) / f".agent_{agent_id}.log"
        if not log_file.exists():
            try:
                log_file.write_text("", encoding="utf-8")
            except Exception:
                pass

    parts = []
    first_agent = members[0]["agent"]
    parts.append(
        f'wt -d "{project_path}" powershell -NoExit -Command "'
        f'Clear-Host; '
        f'Write-Host \">>> @{first_agent} 監控中...\" -ForegroundColor Magenta; '
        f'Get-Content -Path .agent_{first_agent}.log -Wait -Tail 20"'
    )
    
    for i, m in enumerate(members[1:], start=1):
        agent_id = m["agent"]
        split_flag = "-V" if i % 2 == 1 else "-H"
        color = "Green" if i % 3 == 1 else "Cyan" if i % 3 == 2 else "Yellow"
        parts.append(
            f'split-pane {split_flag} -d "{project_path}" powershell -NoExit -Command "'
            f'Clear-Host; '
            f'Write-Host \">>> @{agent_id} 監控中...\" -ForegroundColor {color}; '
            f'Get-Content -Path .agent_{agent_id}.log -Wait -Tail 20"'
        )
    
    # 修正 2：用 ' ";" ' 連接，確保 shell 不會截斷指令，讓 wt 順利處理 split-pane 參數
    full_cmd = ' ";" '.join(parts)
    try:
        import subprocess
        subprocess.Popen(full_cmd, shell=True)
    except Exception as e:
        print(f"[wt launch error] {e}")


async def handle_team_execute(request: web.Request) -> web.StreamResponse:
    data         = await request.json()
    client_id    = data.get("client_id", "default")
    team_id      = data.get("team_id", "")
    project_path = data.get("project_path", "")
    task         = data.get("task", "")
    bin_override = data.get("claude_bin", "")
    codex_bin_override = data.get("codex_bin", "")
    agent_engine = data.get("agent_engine", "")
    model        = data.get("model", "")
    effort       = data.get("effort", "")
    permission_mode = data.get("permission_mode", "")

    claude_bin = bin_override if bin_override else CLAUDE_BIN

    if not project_path or not Path(project_path).is_dir():
        response = web.StreamResponse(headers={
            "Content-Type":    "text/event-stream",
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        payload = json.dumps({"type": "error", "text": f"invalid project path: {project_path}"})
        await response.write(f"data: {payload}\n\n".encode())
        await response.write(b'data: {"type":"done"}\n\n')
        return response

    team_info = None
    if team_id:
        f_team = TEAMS_DIR / f"{team_id}.yaml"
        if f_team.exists():
            try:
                team_info = _team_dict(f_team)
            except Exception:
                pass

    if not team_info:
        response = web.StreamResponse(headers={
            "Content-Type":    "text/event-stream",
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        payload = json.dumps({"type": "error", "text": "team not found"})
        await response.write(f"data: {payload}\n\n".encode())
        await response.write(b'data: {"type":"done"}\n\n')
        return response

    members = team_info.get("members", [])
    if not members:
        response = web.StreamResponse(headers={
            "Content-Type":    "text/event-stream",
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)
        payload = json.dumps({"type": "error", "text": "team has no members"})
        await response.write(f"data: {payload}\n\n".encode())
        await response.write(b'data: {"type":"done"}\n\n')
        return response

    response = web.StreamResponse(headers={
        "Content-Type":    "text/event-stream",
        "Cache-Control":   "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)

    async def run_agent_executor(agent_id: str, role: str, agent_task: str) -> str:
        """
        執行單一 agent 的任務。優先透過 session_pool 重用長駐連線（不重開 subprocess，
        權限核准改用 SDK 的 can_use_tool callback）；沒有 SDK 時退回舊的一次性 subprocess + stdin y/n 模式。
        """
        exec_key = f"exec_{team_id}_{agent_id}"
        proc_key = f"exec_{client_id}_{agent_id}"
        log_file = Path(project_path) / f".agent_{agent_id}.log"

        def _build_full_exec_prompt() -> str:
            agent_file = AGENTS_DIR / f"{agent_id}.md"
            agent_body = _read_agent_body(agent_file) if agent_file.exists() else ""
            prompt = (
                f"你現在在專案目錄 {project_path} 下執行任務。\n"
                f"你是團隊成員 @{agent_id}，你的職責是「{role}」。\n"
                f"以下是團隊需要共同完成的專案實作任務：\n{agent_task}\n"
                f"請以你個人的職責，獨立對此專案目錄下的代碼進行修改、創建 or 測試，以達成任務要求。"
                f"有任何產出請直接在此目錄中創建。請使用工具執行，並將你的執行過程簡要回報。\n"
            )
            if agent_body:
                prompt = f"[你的代理人特徵與能力]\n{agent_body}\n\n---\n\n{prompt}"
            if agent_file.exists():
                skills_content = _read_skills_content(SKILLS_DIR, _agent_dict(agent_file).get("skills", []))
                if skills_content:
                    prompt = f"[Skills]\n{skills_content}\n\n---\n\n{prompt}"
            soul = get_agent_soul(agent_id)
            if soul:
                prompt = f"[System Persona]\n{soul}\n\n{prompt}"
            return prompt

        async def _legacy_exec(prompt: str, resume_sid: "str | None") -> str:
            """舊模式：一次性 subprocess + stdin y/n 權限核准。"""
            cmd = [claude_bin, "-p", prompt, "--output-format", "stream-json", "--verbose"]
            if model and model not in ("sonnet", ""):
                cmd += ["--model", model]
            if effort and effort != "medium":
                cmd += ["--effort", effort]
            if permission_mode and permission_mode not in ("default", ""):
                cmd += ["--permission-mode", permission_mode]
            if resume_sid:
                cmd += ["--resume", resume_sid]

            env = {**os.environ}
            api_key = _resolve_api_key()
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key

            await response.write(f"data: {json.dumps({'type': 'exec_start', 'agent': agent_id})}\n\n".encode())

            collected_output: list[str] = []
            proc = None

            try:
                cmd = wrap_cmd(cmd[0], cmd[1:])
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.PIPE,
                    cwd=project_path,
                    env=env,
                )
                active_procs[proc_key] = proc

                try:
                    log_file.write_text("", encoding="utf-8")
                except Exception:
                    pass

                async for line in proc.stdout:
                    raw = line.decode("utf-8", errors="replace").strip()
                    if not raw:
                        continue
                    if _is_cli_noise(raw):
                        continue

                    try:
                        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                            f.write(raw + "\n")
                    except Exception:
                        pass

                    is_perm_req = False
                    command_to_show = ""

                    try:
                        event = json.loads(raw)
                        if event.get("type") == "permission_request":
                            is_perm_req = True
                            command_to_show = event.get("command") or event.get("description") or "敏感操作"
                        elif event.get("type") == "tool_use" and event.get("requires_approval"):
                            is_perm_req = True
                            command_to_show = f"呼叫工具 {event.get('name')}"
                    except json.JSONDecodeError:
                        pass

                    raw_lower = raw.lower()
                    if ("do you want to" in raw_lower or "permission required" in raw_lower or "allow" in raw_lower) and ("[y/n]" in raw_lower or "y/n" in raw_lower or "?" in raw_lower):
                        is_perm_req = True
                        command_to_show = raw

                    if is_perm_req:
                        req_id = uuid.uuid4().hex[:8]
                        evt = asyncio.Event()
                        pending_permissions[req_id] = {
                            "process": proc,
                            "agent": agent_id,
                            "command": command_to_show,
                            "event": evt,
                            "decision": None
                        }
                        await response.write(f"data: {json.dumps({'type': 'permission_request', 'agent': agent_id, 'request_id': req_id, 'command': command_to_show})}\n\n".encode())

                        try:
                            await asyncio.wait_for(evt.wait(), timeout=600.0)
                            decision = pending_permissions[req_id]["decision"]
                        except asyncio.TimeoutError:
                            decision = "reject"
                            text_val = f"\n[授權超時：自動拒絕 @{agent_id} 的操作]\n"
                            try:
                                await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': text_val})}\n\n".encode())
                            except Exception:
                                pass

                        if decision == "approve":
                            proc.stdin.write(b"y\n")
                            await proc.stdin.drain()
                        else:
                            proc.stdin.write(b"n\n")
                            await proc.stdin.drain()
                        pending_permissions.pop(req_id, None)
                        continue

                    try:
                        event = json.loads(raw)
                        if event.get("type") == "result" and "session_id" in event:
                            active_sessions[exec_key] = event["session_id"]
                        if event.get("type") == "assistant" and event.get("message", {}).get("content"):
                            for block in event["message"]["content"]:
                                if block.get("type") == "text":
                                    collected_output.append(block["text"])
                                    await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': block['text']})}\n\n".encode())
                        elif event.get("type") == "text":
                            collected_output.append(event.get("text", ""))
                            await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': event.get('text', '')})}\n\n".encode())
                    except json.JSONDecodeError:
                        collected_output.append(raw)
                        await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': raw})}\n\n".encode())

                await proc.wait()
            except Exception as e:
                await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': f'[Error: {e}]'})}\n\n".encode())
            finally:
                active_procs.pop(proc_key, None)
                if proc and proc.returncode is None:
                    safe_kill_process(proc)

            await response.write(f"data: {json.dumps({'type': 'exec_done', 'agent': agent_id})}\n\n".encode())
            return "".join(collected_output)

        async def _pooled_exec(prompt_to_send: str, resume_target: "str | None") -> str:
            """透過 session_pool 送出，重用長駐連線；權限核准改用 can_use_tool callback。"""
            await response.write(f"data: {json.dumps({'type': 'exec_start', 'agent': agent_id})}\n\n".encode())
            try:
                log_file.write_text("", encoding="utf-8")
            except Exception:
                pass

            async def can_use_tool(tool_name, tool_input, context):
                req_id = uuid.uuid4().hex[:8]
                evt = asyncio.Event()
                command_to_show = f"呼叫工具 {tool_name}"
                pending_permissions[req_id] = {
                    "agent": agent_id,
                    "command": command_to_show,
                    "event": evt,
                    "decision": None
                }
                await response.write(f"data: {json.dumps({'type': 'permission_request', 'agent': agent_id, 'request_id': req_id, 'command': command_to_show})}\n\n".encode())
                try:
                    await asyncio.wait_for(evt.wait(), timeout=600.0)
                    decision = pending_permissions[req_id]["decision"]
                except asyncio.TimeoutError:
                    decision = "reject"
                    text_val = f"\n[授權超時：自動拒絕 @{agent_id} 的操作]\n"
                    try:
                        await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': text_val})}\n\n".encode())
                    except Exception:
                        pass
                pending_permissions.pop(req_id, None)
                if decision == "approve":
                    return PermissionResultAllow(updated_input=tool_input)
                return PermissionResultDeny(message="使用者拒絕此操作")

            in_pool = _team_pool.has(proc_key)
            opts = None
            if not in_pool:
                env = {**os.environ}
                api_key = _resolve_api_key()
                if api_key:
                    env["ANTHROPIC_API_KEY"] = api_key
                opts = ClaudeAgentOptions(
                    cwd=project_path,
                    env=env,
                    model=(model if model and model not in ("sonnet", "") else None),
                    effort=(effort if effort and effort != "medium" else None),
                    permission_mode=(permission_mode if permission_mode and permission_mode not in ("default", "") else None),
                    resume=resume_target,
                    can_use_tool=can_use_tool,
                )

            client = await _team_pool.get_or_create(proc_key, opts)
            collected: list[str] = []
            try:
                await client.query(prompt_to_send)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                collected.append(block.text)
                                try:
                                    with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                                        f.write(block.text + "\n")
                                except Exception:
                                    pass
                                await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': block.text})}\n\n".encode())
                    elif isinstance(message, ResultMessage):
                        active_sessions[exec_key] = message.session_id
            except Exception:
                await _team_pool.evict(proc_key, force=True)
                raise
            finally:
                _team_pool.release(proc_key)

            await response.write(f"data: {json.dumps({'type': 'exec_done', 'agent': agent_id})}\n\n".encode())
            return "".join(collected)

        async def _exec_engine_turn(prompt: str, resume_sid: "str | None") -> str:
            """Agent 自己宣告了非 Claude 的 engine: 時走這裡——完全跳過
            SessionPool/ClaudeSDKClient。這裡的即時權限核准 UI
            （pending_permissions／can_use_tool callback）對 Codex 沒有
            對應物，Codex-routed 的團隊成員會跳過這個即時核准流程，只能靠
            --sandbox <mode> 控制，這是既有、已接受的權衡（同一個權衡
            _agent_run_capture 早就接受過）。事件格式沿用 _legacy_exec()
            既有的 exec_text/exec_start/exec_done SSE envelope，前端不用改。
            """
            await response.write(f"data: {json.dumps({'type': 'exec_start', 'agent': agent_id})}\n\n".encode())
            try:
                log_file.write_text("", encoding="utf-8")
            except Exception:
                pass

            collected: list[str] = []

            async def _on_text(chunk: str) -> None:
                collected.append(chunk)
                try:
                    with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                        f.write(chunk + "\n")
                except Exception:
                    pass
                await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': chunk})}\n\n".encode())

            def _on_process(proc) -> None:
                active_procs[proc_key] = proc

            try:
                # handle_team_execute 本來就沒有 attachments 概念（跟
                # handle_chat／handle_team_chat 不一樣），這裡不用傳。
                result = await engine.run_turn(
                    prompt=prompt, cwd=project_path, model=model, permission_mode=permission_mode,
                    resume_session_id=resume_sid, api_key=engine_api_key,
                    on_text=_on_text, on_process=_on_process,
                    bin_override=(codex_bin_override if engine.name == "codex" else ""),
                )
            finally:
                active_procs.pop(proc_key, None)

            if result.session_id:
                active_sessions[exec_key] = result.session_id
            if result.error:
                err_text = f"[Error: {result.error}]"
                collected.append(err_text)
                await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': err_text})}\n\n".encode())

            await response.write(f"data: {json.dumps({'type': 'exec_done', 'agent': agent_id})}\n\n".encode())
            return "".join(collected)

        has_persisted = exec_key in active_sessions
        in_pool_now = HAS_AGENT_SDK and _team_pool is not None and _team_pool.has(proc_key)
        use_pool = HAS_AGENT_SDK and _team_pool is not None

        from engines.availability import NoEngineAvailableError
        try:
            engine, engine_api_key, engine_notice = await _resolve_agent_engine_and_key(agent_id, agent_engine)
        except NoEngineAvailableError as e:
            # 這個函式外層（sequential 迴圈的 await／parallel 模式的
            # asyncio.gather）完全沒有 try/except 包住——沒接住的例外會
            # 整個炸掉這次 team execute 的 SSE 串流，不是只掛掉這個成員，
            # 所以這裡要自己補一個局部防護，回傳空字串維持既有 contract。
            await response.write(f"data: {json.dumps({'type': 'exec_start', 'agent': agent_id})}\n\n".encode())
            await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': f'[Error: {e}]'})}\n\n".encode())
            await response.write(f"data: {json.dumps({'type': 'exec_done', 'agent': agent_id})}\n\n".encode())
            return ""
        if engine_notice:
            await response.write(f"data: {json.dumps({'type': 'exec_text', 'agent': agent_id, 'text': engine_notice})}\n\n".encode())

        if engine.name != "claude":
            if not has_persisted:
                prompt = _build_full_exec_prompt()
                resume_sid = None
            else:
                prompt = f"請繼續完成上述任務。追加說明：\n{agent_task}"
                resume_sid = active_sessions.get(exec_key)
            return await _exec_engine_turn(prompt, resume_sid)

        if use_pool:
            needs_full_rebuild = not (in_pool_now or has_persisted)
            if needs_full_rebuild:
                prompt_to_send = _build_full_exec_prompt()
            else:
                prompt_to_send = f"請繼續完成上述任務。追加說明：\n{agent_task}"
            resume_target = None if (in_pool_now or needs_full_rebuild) else active_sessions.get(exec_key)
            try:
                return await _pooled_exec(prompt_to_send, resume_target)
            except ConnectionError:
                # 客戶端已斷線，pool 連線已在 _pooled_exec 內 evict；不重跑，避免重複副作用
                return ""
            except Exception:
                active_sessions.pop(exec_key, None)
                return await _legacy_exec(_build_full_exec_prompt(), None)

        if not has_persisted:
            prompt = _build_full_exec_prompt()
            resume_sid = None
        else:
            prompt = f"請繼續完成上述任務。追加說明：\n{agent_task}"
            resume_sid = active_sessions.get(exec_key)

        return await _legacy_exec(prompt, resume_sid)

    # 自動彈出已依照團隊人數拆分 Pane 的 Windows Terminal 監控視窗
    try:
        launch_windows_terminal_monitor(project_path, members)
    except Exception:
        pass

    execution_mode = team_info.get("execution_mode", "parallel")

    if execution_mode == "sequential":
        # 串行模式：前一個 agent 的產出附加到下一個 agent 的 task
        # （只傳「前一位」，不累加全部歷史——避免 token 隨成員數量呈平方成長）
        previous_output = ""
        for m in members:
            agent_task = task
            if previous_output:
                agent_task += f"\n\n[前一位成員的產出]\n{previous_output}"
            previous_output = await run_agent_executor(m["agent"], m["role"], agent_task)
    else:
        # 並行模式（預設）
        exec_tasks = [run_agent_executor(m["agent"], m["role"], task) for m in members]
        await asyncio.gather(*exec_tasks)

    await response.write(b'data: {"type":"done"}\n\n')
    return response


async def handle_team_authorize(request: web.Request) -> web.Response:
    data       = await request.json()
    request_id = data.get("request_id", "")
    decision   = data.get("decision", "")

    if not request_id or request_id not in pending_permissions:
        return web.json_response({"error": "invalid or expired request_id"}, status=404)

    record = pending_permissions[request_id]
    record["decision"] = decision
    record["event"].set()

    return web.json_response({"ok": True})


async def handle_sessions(request: web.Request) -> web.Response:
    q      = request.rel_url.query.get("q", "").strip()
    offset = int(request.rel_url.query.get("offset", "0"))
    PAGE   = 30
    await asyncio.to_thread(_sync_index)
    custom_names = load_session_names()

    # 只保留最近 30 天的對話
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 24 * 3600

    def _proj_dir(file_path: str) -> str:
        if not file_path:
            return ""
        parts = Path(file_path).parent.name.split('--')
        return parts[-1] if parts else ""

    try:
        with _db_ctx() as c:
            if q and len(q) >= 3:
                # trigram tokenizer needs >=3 chars to form any trigram, so this
                # path only fires for queries long enough for FTS5 MATCH to work.
                # T44 健檢修復：snippet 會經前端 [innerHTML] 直接渲染，FTS5 的
                # snippet() 回傳的是 search_text 原始子字串（未跳脫），若對話
                # 內容剛好含有 <script> 之類字元就會被當成真的 HTML 執行。
                # 用不可能出現在一般文字裡的控制字元當標記，取回後先整段
                # html.escape() 再把標記換回真正的 <mark> 標籤，確保只有
                # 「刻意插入的」標記會被當成 HTML，其餘內容一律跳脫。
                raw_rows = c.execute("""
                    SELECT s.id, s.title, s.mtime, s.message_count, s.file_path, s.project_path,
                           snippet(sessions_fts, 2, ?, ?, '…', 12) AS snippet
                    FROM sessions_fts f
                    JOIN sessions s ON s.id = f.id
                    WHERE sessions_fts MATCH ?
                      AND s.mtime >= ?
                    ORDER BY s.mtime DESC
                """, ("\x01", "\x02", q, cutoff)).fetchall()
                rows = []
                for r in raw_rows:
                    safe_snippet = html.escape(r["snippet"] or "").replace("\x01", "<mark>").replace("\x02", "</mark>")
                    rows.append({**dict(r), "snippet": safe_snippet})
            elif q:
                # short (1-2 char) queries: trigram can't match these at all, so
                # fall back to LIKE -- table is small enough that this is cheap.
                like_rows = c.execute("""
                    SELECT id, title, mtime, message_count, file_path, project_path, search_text
                    FROM sessions
                    WHERE mtime >= ? AND (title LIKE ? OR search_text LIKE ?)
                    ORDER BY mtime DESC
                """, (cutoff, f"%{q}%", f"%{q}%")).fetchall()
                rows = []
                for r in like_rows:
                    text = r["search_text"] or ""
                    idx = text.find(q)
                    if idx >= 0:
                        start = max(0, idx - 30)
                        end = min(len(text), idx + len(q) + 30)
                        snippet = (
                            ("…" if start > 0 else "")
                            + html.escape(text[start:idx])
                            + "<mark>" + html.escape(q) + "</mark>"
                            + html.escape(text[idx + len(q):end])
                            + ("…" if end < len(text) else "")
                        )
                    else:
                        snippet = html.escape(text[:120])
                    rows.append({**dict(r), "snippet": snippet})
            else:
                raw_rows = c.execute("""
                    SELECT id, title, mtime, message_count, file_path, project_path,
                           substr(search_text, 1, 120) AS snippet
                    FROM sessions
                    WHERE mtime >= ?
                    ORDER BY mtime DESC
                """, (cutoff,)).fetchall()
                rows = [{**dict(r), "snippet": html.escape(r["snippet"] or "")} for r in raw_rows]
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


_RESTORE_MAX_ENTRY_BYTES = 20 * 1024 * 1024   # 單一項目解壓後上限 20MB
_RESTORE_MAX_TOTAL_BYTES = 50 * 1024 * 1024   # 整包解壓後上限 50MB

async def handle_restore(request: web.Request) -> web.Response:
    reader = await request.multipart()
    field  = await reader.next()
    data   = await field.read()
    buf    = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buf) as zf:
            # T30 健檢修復：zf.read() 會把整個項目解壓進記憶體，原本沒有任何
            # 大小檢查 —— 上傳的 zip 本身雖被 client_max_size（~20MB）限制，
            # 但壓縮比可以很誇張（zip bomb），解壓後可能是好幾 GB。用
            # ZipInfo.file_size（來自 zip 中央目錄的 metadata，讀取不需要
            # 真的解壓）先檢查每個項目與總計大小，超過上限就整包拒絕。
            total_size = 0
            for info in zf.infolist():
                if info.file_size > _RESTORE_MAX_ENTRY_BYTES:
                    return web.json_response({'error': f'{info.filename} 解壓後過大'}, status=400)
                total_size += info.file_size
            if total_size > _RESTORE_MAX_TOTAL_BYTES:
                return web.json_response({'error': '備份檔解壓後總大小超過上限'}, status=400)

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
    # 重新寫入預設 presets 靈魂（例如 Pi），防止重設後列表空白
    await asyncio.to_thread(_init_presets)
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
    if client_id:
        # 找出所有與該 client_id 相關的進程（包括單人、Team Chat 和背景 Team Execute 專案實作的進程）
        keys_to_kill = [
            k for k in list(active_procs.keys())
            if k == client_id or k.startswith(f"exec_{client_id}_") or k.startswith(f"{client_id}_")
        ]
        for k in keys_to_kill:
            proc = active_procs.pop(k, None)
            if proc:
                safe_kill_process(proc)

        if HAS_AGENT_SDK and _team_pool is not None:
            pool_keys_to_evict = [
                k for k in _team_pool.keys()
                if k == client_id or k.startswith(f"exec_{client_id}_") or k.startswith(f"{client_id}_")
            ]
            for k in pool_keys_to_evict:
                try:
                    await _team_pool.evict(k)
                except Exception:
                    pass
    return web.json_response({"ok": True})


async def handle_chat_clear(request: web.Request) -> web.Response:
    data = await request.json()
    client_id = data.get("client_id", "")
    if client_id:
        active_sessions.pop(client_id, None)
        for k in list(active_sessions.keys()):
            if k.startswith(f"{client_id}_"):
                active_sessions.pop(k, None)

        if HAS_AGENT_SDK and _team_pool is not None:
            pool_keys_to_evict = [
                k for k in _team_pool.keys()
                if k == client_id or k.startswith(f"exec_{client_id}_") or k.startswith(f"{client_id}_")
            ]
            for k in pool_keys_to_evict:
                try:
                    await _team_pool.evict(k)
                except Exception:
                    pass
    return web.json_response({"ok": True})


async def handle_session_delete(request: web.Request) -> web.Response:
    sid = request.match_info["id"]
    f = _find_session_file(sid)
    if f and f.exists():
        f.unlink()
    for k in [k for k, v in active_sessions.items() if v == sid]:
        active_sessions.pop(k, None)

    # 主動從 SQLite 數據庫中清除該 Session 索引，保持即時一致
    try:
        with _db_ctx() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))
            c.execute("DELETE FROM sessions_fts WHERE id=?", (sid,))
    except Exception as e:
        _log(f"[sqlite] delete error: {e}")

    return web.json_response({"ok": True})


async def handle_session_truncate(request: web.Request) -> web.Response:
    """POST /api/sessions/{id}/truncate — 截斷對話歷史，丟棄指定 count 之後的 user/assistant 訊息"""
    sid = request.match_info["id"]
    data = await request.json()
    count = data.get("count", 0)

    f = _find_session_file(sid)
    if not f:
        return web.json_response({"error": "session not found"}, status=404)

    try:
        lines = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        new_lines = []
        valid_count = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                t = ev.get("type", "")
                if t in ("user", "assistant"):
                    if valid_count >= count:
                        continue  # 丟棄 count 之後的對話
                    valid_count += 1
                new_lines.append(line)
            except Exception:
                new_lines.append(line)

        content = "\n".join(new_lines) + "\n" if new_lines else ""
        f.write_text(content, encoding="utf-8")
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"ok": True})


async def handle_resume_session(request: web.Request) -> web.Response:
    data = await request.json()
    client_id = data.get("client_id", "default")
    session_id = data.get("session_id", "")
    active_sessions[client_id] = session_id
    return web.json_response({"ok": True})


async def handle_session_messages(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/messages — 讀取 JSONL 回傳完整對話紀錄"""
    sid = request.match_info["id"]
    f = _find_session_file(sid)
    if not f:
        return web.json_response({"error": "session not found"}, status=404)

    messages = []
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        for line in lines:
            try:
                ev = json.loads(line)
                t = ev.get("type", "")
                if t not in ("user", "assistant"):
                    continue
                content = ev.get("message", {}).get("content", "")
                timestamp = ev.get("timestamp", "")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
                    text = "\n".join(parts)
                if text.strip():
                    messages.append({"role": t, "text": text, "time": timestamp})
            except Exception:
                pass
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({"messages": messages})


async def handle_usage(request: web.Request) -> web.Response:
    """GET /api/usage — 查詢 Claude Code 用量，快取 5 分鐘"""
    now = time.time()
    if _usage_cache["data"] and now < _usage_cache["expires"]:
        return web.json_response(_usage_cache["data"])

    # 讀取 OAuth token
    creds_file = CLAUDE_HOME / ".credentials.json"
    if not creds_file.exists():
        return web.json_response({"error": "credentials not found"}, status=404)
    try:
        creds = json.loads(creds_file.read_text(encoding="utf-8"))
        access_token = creds.get("claudeAiOauth", {}).get("accessToken", "")
        if not access_token:
            return web.json_response({"error": "no access token"}, status=401)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    # 呼叫 Anthropic usage API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "User-Agent": f"claude-code/{CLAUDE_VERSION}",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.json_response({"error": f"API {resp.status}: {text[:200]}"}, status=resp.status)
                data = await resp.json()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=502)

    _usage_cache["data"] = data
    _usage_cache["expires"] = now + 300  # 5 分鐘
    return web.json_response(data)


async def handle_codex_usage(request: web.Request) -> web.Response:
    """GET /api/usage/codex — query Codex app-server, cached for 5 minutes."""
    now = time.time()
    if _codex_usage_cache["data"] and now < _codex_usage_cache["expires"]:
        return web.json_response(_codex_usage_cache["data"])

    from codex_usage import CodexUsageError, fetch_codex_usage

    try:
        data = await fetch_codex_usage(CODEX_BIN)
    except CodexUsageError as exc:
        return web.json_response({"error": str(exc)}, status=502)

    _codex_usage_cache["data"] = data
    _codex_usage_cache["expires"] = now + 300
    return web.json_response(data)




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

_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

async def handle_upload(request: web.Request) -> web.Response:
    data = await request.json()
    b64  = data.get("data", "")
    name = data.get("name", "upload.bin")
    if not b64:
        return web.json_response({"error": "no data"}, status=400)
    if len(b64) > _UPLOAD_MAX_BYTES * 4 // 3 + 4:
        return web.json_response({"error": "file too large (max 20 MB)"}, status=413)
    raw_bytes = base64.b64decode(b64)
    if len(raw_bytes) > _UPLOAD_MAX_BYTES:
        return web.json_response({"error": "file too large (max 20 MB)"}, status=413)
    ext  = Path(name).suffix or ".bin"
    dest = UPLOAD_DIR / f"{uuid.uuid4()}{ext}"
    dest.write_bytes(raw_bytes)
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
    if not key.replace("-", "").replace("_", "").isalnum():
        return web.json_response({"error": "invalid key"}, status=400)
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
    if not agent_id or "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        return web.json_response({"error": "invalid agent_id"}, status=400)
    content = _read_md(_agent_memory_dir(agent_id) / "identity.md")
    return web.json_response({"agent_id": agent_id, "content": content or ""})

async def handle_mem_agent_put(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    if not agent_id or "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        return web.json_response({"error": "invalid agent_id"}, status=400)
    data = await request.json()
    _write_md(_agent_memory_dir(agent_id) / "identity.md", data.get("content", ""))
    return web.json_response({"ok": True})

async def handle_mem_agent_projects_list(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    if not agent_id or "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        return web.json_response({"error": "invalid agent_id"}, status=400)
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
    if not agent_id or "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        return web.json_response({"error": "invalid agent_id"}, status=400)
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return web.json_response({"error": "invalid slug"}, status=400)
    content  = _read_md(_agent_memory_dir(agent_id) / "projects" / f"{slug}.md")
    return web.json_response({"agent_id": agent_id, "slug": slug, "content": content or ""})

async def handle_mem_agent_project_put(request: web.Request) -> web.Response:
    agent_id = request.match_info["id"]
    slug     = request.match_info["slug"]
    if not agent_id or "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        return web.json_response({"error": "invalid agent_id"}, status=400)
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return web.json_response({"error": "invalid slug"}, status=400)
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
    if not team_id or "/" in team_id or "\\" in team_id or ".." in team_id:
        return web.json_response({"error": "invalid team_id"}, status=400)
    content = _read_md(_team_memory_dir(team_id) / "shared.md")
    return web.json_response({"team_id": team_id, "content": content or ""})

async def handle_mem_team_put(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    if not team_id or "/" in team_id or "\\" in team_id or ".." in team_id:
        return web.json_response({"error": "invalid team_id"}, status=400)
    data    = await request.json()
    _write_md(_team_memory_dir(team_id) / "shared.md", data.get("content", ""))
    return web.json_response({"ok": True})

async def handle_mem_team_projects_list(request: web.Request) -> web.Response:
    team_id  = request.match_info["id"]
    if not team_id or "/" in team_id or "\\" in team_id or ".." in team_id:
        return web.json_response({"error": "invalid team_id"}, status=400)
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
    if not team_id or "/" in team_id or "\\" in team_id or ".." in team_id:
        return web.json_response({"error": "invalid team_id"}, status=400)
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return web.json_response({"error": "invalid slug"}, status=400)
    content = _read_md(_team_memory_dir(team_id) / "projects" / f"{slug}.md")
    return web.json_response({"team_id": team_id, "slug": slug, "content": content or ""})

async def handle_mem_team_project_put(request: web.Request) -> web.Response:
    team_id = request.match_info["id"]
    slug    = request.match_info["slug"]
    if not team_id or "/" in team_id or "\\" in team_id or ".." in team_id:
        return web.json_response({"error": "invalid team_id"}, status=400)
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return web.json_response({"error": "invalid slug"}, status=400)
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
        ctx = await asyncio.to_thread(build_team_memory_context, team_id, members or [agent_id], agent_id, cwd)
    else:
        ctx = await asyncio.to_thread(build_memory_context, agent_id, cwd)

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
    if ".." in old_id or "/" in old_id or "\\" in old_id or not old_id.strip() or any(c in old_id for c in '<>:"/\\|?*'):
        return web.json_response({"error": "invalid old name"}, status=400)
    if ".." in new_id or "/" in new_id or "\\" in new_id or not new_id.strip() or any(c in new_id for c in '<>:"/\\|?*'):
        return web.json_response({"error": "invalid new name"}, status=400)
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
    if ".." in sid or "/" in sid or "\\" in sid or not sid.strip() or any(c in sid for c in '<>:"/\\|?*'):
        return web.json_response({"error": "invalid name"}, status=400)
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
    entry = {
        "id": str(uuid.uuid4()),
        "prompt": prompt,
        "cron": cron,
        "enabled": True
    }
    delivery = data.get("delivery")
    if delivery and isinstance(delivery, dict):
        entry["delivery"] = {
            "channel": delivery.get("channel", "").strip(),
            "to": delivery.get("to", "").strip()
        }
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
            "model": "claude-haiku-4-5-20251001",
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
            cmd = wrap_cmd(cmd[0], cmd[1:])
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
    await asyncio.to_thread(_sync_index)

    sessions_count = 0
    total_tokens   = 0
    daily_map: dict[str, int] = {}
    active_days_set: set[str] = set()

    try:
        with _db_ctx() as c:
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
    proc = None
    try:
        cmd = wrap_cmd(CLAUDE_BIN, ["-p", prompt, "--output-format", "json"])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        result = json.loads(raw.decode("utf-8", errors="replace"))
        skill_md = result.get("result", "") or result.get("content", "")
    except Exception as e:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
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
    home = Path.home()
    try:
        p = Path(raw).resolve() if raw else home
    except Exception:
        p = home
    
    # 移除了限制在 home 目錄之下的限制，以支援 Windows 上的多磁碟機（如 D 槽）專案目錄瀏覽
    if not p.exists() or not p.is_dir():
        p = home
    items = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith('.'):
                continue
            items.append({"name": child.name, "path": str(child), "isDir": child.is_dir()})
    except PermissionError:
        pass
    return web.json_response({"path": str(p), "parent": str(p.parent), "items": items})

_CLI_ALLOWLIST: dict[str, set[str] | None] = {
    "logout": None, "doctor": None, "update": None,
    "mcp": {"list", "remove"},
}

async def handle_cli(request: web.Request) -> web.Response:
    data = await request.json()
    args = data.get("args", [])
    if not isinstance(args, list) or not args:
        return web.json_response({"error": "args must be non-empty list"}, status=400)
    allowed_sub = _CLI_ALLOWLIST.get(args[0])
    if args[0] not in _CLI_ALLOWLIST:
        return web.json_response({"error": f"disallowed verb: {args[0]}"}, status=400)
    if allowed_sub is not None and (len(args) < 2 or args[1] not in allowed_sub):
        return web.json_response({"error": f"disallowed subcommand"}, status=400)
    proc = None
    try:
        cmd = wrap_cmd(CLAUDE_BIN, args)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path.home()),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return web.json_response({"output": out.decode("utf-8", errors="replace"), "code": proc.returncode})
    except asyncio.TimeoutError:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
        return web.json_response({"output": "[逾時]", "code": -1})
    except Exception as e:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
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
        p = None
        try:
            p = await asyncio.create_subprocess_exec(
                "docker", "logs", "--tail", "80", container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
            lines = out.decode("utf-8", errors="replace").splitlines()
        except Exception:
            if p:
                try:
                    safe_kill_process(p)
                except Exception:
                    pass

    return web.json_response({"name": name, "lines": lines[-100:]})


async def handle_local_mcp_config_get(request: web.Request) -> web.Response:
    """Return all local MCP Docker/compose metadata."""
    return web.json_response(_load_local_mcp_cfg())


def _is_safe_docker_ident(name: str) -> bool:
    """
    T2 加固：containerName/composeService 會被當成 `docker stop/start <name>`、
    `docker compose ... <service>` 的位置參數傳給 subprocess_exec（非 shell，
    無 shell injection 風險），但仍需擋掉會被 docker CLI 誤判成旗標的字串
    （如開頭 `-`）與路徑分隔符/`..`，避免打錯目標或被用來探測非預期容器。
    空字串代表未設定，視為合法。
    """
    if not name:
        return True
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name))


async def handle_local_mcp_config_put(request: web.Request) -> web.Response:
    """Save Docker/compose metadata for one MCP server."""
    name = request.match_info["name"]
    data = await request.json()

    container_name  = data.get("containerName", "")
    compose_service = data.get("composeService", "")
    compose_file    = data.get("composeFile", "")

    if not _is_safe_docker_ident(container_name):
        return web.json_response({"error": "Invalid containerName"}, status=400)
    if not _is_safe_docker_ident(compose_service):
        return web.json_response({"error": "Invalid composeService"}, status=400)
    if compose_file and not Path(compose_file).is_file():
        return web.json_response({"error": "composeFile does not exist"}, status=400)

    cfg  = _load_local_mcp_cfg()
    cfg[name] = {
        "containerName":  container_name,
        "composeFile":    compose_file,
        "composeService": compose_service,
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

    proc = None
    try:
        cmd = wrap_cmd(CLAUDE_BIN, ["-p", prompt, "--model", "claude-haiku-4-5-20251001", "--output-format", "text"])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        title = stdout.decode("utf-8", errors="replace").strip().splitlines()[0][:60]
    except Exception as e:
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
        return web.json_response({"error": str(e)}, status=500)

    if not title:
        return web.json_response({"error": "empty title"}, status=500)

    names = load_session_names()
    names[sid] = title
    _safe_write_text(SESSION_NAMES_FILE, json.dumps(names, ensure_ascii=False, indent=2))

    # Also update SQLite
    try:
        with _db_ctx() as c:
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

    # T2 加固：即使 config 是透過已驗證的 PUT 端點寫入，這裡對即將傳給
    # docker/docker compose subprocess 的值再檢查一次（防禦深度：涵蓋手動編輯
    # config 檔或舊版資料殘留的情況）。
    if not _is_safe_docker_ident(container) or not _is_safe_docker_ident(compose_svc):
        return web.json_response({"error": "Invalid containerName/composeService in local MCP config"}, status=400)
    if compose_f and not Path(compose_f).is_file():
        return web.json_response({"error": "composeFile does not exist"}, status=400)

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
                safe_kill_process(proc)

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
                mcp_cmd = wrap_cmd(mcp_cmd[0], mcp_cmd[1:])
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
    """Parse ~/.claude.json to find the stdio command for an MCP server."""
    config_path = CLAUDE_HOME.parent / ".claude.json"
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
                    if not line_s.startswith("data:"):
                        continue
                    payload = line_s[5:].strip()
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
    _safe_write_text(TELEGRAM_CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))

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
    cfg = _load_config()
    permission_mode = cfg.get("permissionMode", "")
    model = cfg.get("model", "")
    effort = cfg.get("effort", "")
    
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "text"]
    if permission_mode and permission_mode not in ("default", ""):
        cmd += ["--permission-mode", permission_mode]
    if model and model not in ("sonnet", ""):
        cmd += ["--model", model]
    if effort and effort != "medium":
        cmd += ["--effort", effort]

    env = os.environ.copy()
    key = _resolve_api_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    proc = None
    try:
        cmd = wrap_cmd(cmd[0], cmd[1:])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,  # 防止卡死
            env=env,
            cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        return stdout.decode("utf-8", errors="replace").strip() or "[no response]"
    except Exception as e:
        return f"[Error: {e}]"
    finally:
        if proc and proc.returncode is None:
            safe_kill_process(proc)

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
    # 健檢第二輪修復：LINE webhook 本質上就是要能被 LINE 伺服器從公開網際網路
    # 打進來（跟其他端點「同機/同網段可信任」的假設不同），簽章驗證是唯一的
    # 身分驗證機制。原本 lineChannelSecret 未設定時直接放行（fail-open），
    # 代表設定到一半、還沒填 secret 的期間，任何人都能偽造 webhook payload
    # 觸發 _line_run_claude（實際執行 Claude CLI）。改成 fail-closed：
    # 沒設定 secret 就一律拒絕，直到使用者完成設定為止。
    secret = _load_config().get("lineChannelSecret", "").strip()
    if not secret:
        return False
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
    proc = None
    try:
        cmd = wrap_cmd(CLAUDE_BIN, ["-p", prompt, "--output-format", "json"])
        env = os.environ.copy()
        key = _resolve_api_key()
        if key:
            env["ANTHROPIC_API_KEY"] = key
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass
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
                if "key" not in k.lower() and "token" not in k.lower()
                and "password" not in k.lower() and "secret" not in k.lower()}
    sqlite_stats: dict = {}
    try:
        with _db_ctx() as c:
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
    pool_size = len(_team_pool) if (HAS_AGENT_SDK and _team_pool is not None) else 0
    return web.json_response({"status": "ok", "active_sessions": len(active_sessions), "pool_size": pool_size, "claude_bin": CLAUDE_BIN})


async def handle_config_get(request: web.Request) -> web.Response:
    cfg = _load_config()
    cfg.setdefault("projectDir", "")
    cfg.setdefault("claudeHome", "")
    cfg.setdefault("registryHome", "")
    cfg.setdefault("engineMode", "both")
    cfg.setdefault("codexApiKeyCmd", "")
    cfg["_resolvedClaudeHome"] = str(CLAUDE_HOME)   # read-only info for UI
    import database
    cfg["_resolvedRegistryHome"] = str(database.REGISTRY_HOME)   # read-only info for UI
    # 健檢第二輪修復：這是驗證 LINE webhook 簽章的 HMAC secret，外洩等於能
    # 偽造合法 webhook 請求繞過簽章驗證。前端 settings 表單目前不會讀回這個
    # 欄位（只有 apiKeyCmd 會被讀回填入表單），拿掉不影響既有功能。
    cfg.pop("lineChannelSecret", None)
    # 這是呼叫 LINE Messaging API（傳訊息、驗證身分）用的 access token，
    # 外洩等於能冒充這個 bot 呼叫 LINE API——跟上面 lineChannelSecret 同樣
    # 嚴重，原本漏掉沒一起處理。前端目前沒有任何地方讀這個欄位（grep 過
    # frontend/src/app 確認），拿掉不影響既有功能。
    cfg.pop("lineChannelAccessToken", None)
    return web.json_response(cfg)


async def handle_config_put(request: web.Request) -> web.Response:
    global CLAUDE_HOME, AGENTS_DIR, SKILLS_DIR, SESSIONS_DIR, TEAMS_DIR, SOULS_DIR
    data = await request.json()
    cfg = _load_config()
    if "projectDir" in data:
        cfg["projectDir"] = data["projectDir"].strip()
    if "apiKeyCmd" in data:
        cfg["apiKeyCmd"] = data["apiKeyCmd"].strip()
    if "codexApiKeyCmd" in data:
        cfg["codexApiKeyCmd"] = data["codexApiKeyCmd"].strip()
    if "claudeHome" in data:
        cfg["claudeHome"] = data["claudeHome"].strip()
    if "registryHome" in data:
        cfg["registryHome"] = data["registryHome"].strip()
    if "engineMode" in data:
        mode = data["engineMode"]
        if mode not in ("claude", "codex", "both"):
            return web.json_response({"error": "invalid engineMode"}, status=400)
        cfg["engineMode"] = mode
    _save_config(cfg)
    # Re-resolve CLAUDE_HOME in case claudeHome changed
    CLAUDE_HOME  = _resolve_claude_home()
    AGENTS_DIR   = CLAUDE_HOME / "agents"
    SKILLS_DIR   = CLAUDE_HOME / "skills"
    TEAMS_DIR    = CLAUDE_HOME / "teams"
    SOULS_DIR    = CLAUDE_HOME / "souls"
    SESSIONS_DIR = CLAUDE_HOME / "sessions"
    
    # 同步更新 database 模組內部的所有路徑變數，防止其依然讀寫舊路徑
    import database
    database.update_paths(CLAUDE_HOME)
    
    # 確保新目錄複製了預設的 presets (Pi, HR 等)，防止切換目錄後介面空白
    await asyncio.to_thread(_init_presets)

    _log(f"Config updated: claudeHome={CLAUDE_HOME}  projectDir={cfg.get('projectDir','')!r}")
    return web.json_response({"ok": True, "slug": cfg.get("projectDir", "")})


def _init_presets() -> None:
    """如果 CLAUDE_HOME 下的 skills/agents/teams 目錄中沒有對應的 preset，就從專案 preset 目錄複製過去。
    同時將預設的 MCP 伺服器設定合併到 ~/.claude/claude.json 中。"""
    # 測試環境不執行
    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    try:
        # PyInstaller onefile：frozen 執行時 __file__ 落在暫時解壓目錄
        # sys._MEIPASS 裡（presets/ 透過 package.json 的
        # backend:compile:* 腳本以 --add-data "presets;presets" 一併
        # 打包進去），明確判斷 sys.frozen 避免依賴 PyInstaller 內部
        # __file__ 語意的偶然巧合。
        if getattr(sys, "frozen", False):
            presets_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "presets"
        else:
            presets_dir = Path(__file__).parent / "presets"
        if not presets_dir.exists():
            _log(f"Presets directory not found at: {presets_dir}")
            return

        # 確保 CLAUDE_HOME 底下的目標目錄存在
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        TEAMS_DIR.mkdir(parents=True, exist_ok=True)
        SOULS_DIR.mkdir(parents=True, exist_ok=True)

        # 1. 複製 skills
        p_skills = presets_dir / "skills"
        if p_skills.exists():
            for src in p_skills.iterdir():
                dest = SKILLS_DIR / src.name
                if not dest.exists():
                    if src.is_dir():
                        shutil.copytree(src, dest)
                    else:
                        shutil.copy2(src, dest)
                    _log(f"Copied preset skill: {src.name} -> {dest}")

        # 2. 複製 agents
        p_agents = presets_dir / "agents"
        if p_agents.exists():
            for src in p_agents.iterdir():
                dest = AGENTS_DIR / src.name
                if not dest.exists():
                    if src.is_dir():
                        shutil.copytree(src, dest)
                    else:
                        shutil.copy2(src, dest)
                    _log(f"Copied preset agent: {src.name} -> {dest}")

        # 3. 複製 teams
        p_teams = presets_dir / "teams"
        if p_teams.exists():
            for src in p_teams.iterdir():
                dest = TEAMS_DIR / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    _log(f"Copied preset team: {src.name} -> {dest}")

        # 4. 複製 souls（Soul 與 Agent 為 1:1，檔名對應 agent id）
        p_souls = presets_dir / "souls"
        if p_souls.exists():
            for src in p_souls.iterdir():
                dest = SOULS_DIR / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    _log(f"Copied preset soul: {src.name} -> {dest}")

        # 5. 合併 MCP servers (.claude.json)
        p_claude_json = presets_dir / "claude.json"
        if p_claude_json.exists():
            dest_claude_json = CLAUDE_HOME.parent / ".claude.json"
            
            # 讀取 preset mcpServers
            try:
                preset_data = json.loads(p_claude_json.read_text(encoding="utf-8"))
            except Exception as e:
                preset_data = {}
                _log(f"Error parsing preset claude.json: {e}")

            preset_mcp = preset_data.get("mcpServers", {})
            if preset_mcp:
                # 讀取使用者現有的 claude.json
                user_data = {}
                if dest_claude_json.exists():
                    try:
                        user_data = json.loads(dest_claude_json.read_text(encoding="utf-8"))
                    except Exception as e:
                        _log(f"Error parsing user claude.json, resetting: {e}")
                
                if not isinstance(user_data, dict):
                    user_data = {}
                if "mcpServers" not in user_data or not isinstance(user_data["mcpServers"], dict):
                    user_data["mcpServers"] = {}

                # 合併
                changed = False
                for k, v in preset_mcp.items():
                    if k not in user_data["mcpServers"]:
                        user_data["mcpServers"][k] = v
                        changed = True
                        _log(f"Added preset MCP server: {k}")

                if changed:
                    _safe_write_text(dest_claude_json, json.dumps(user_data, ensure_ascii=False, indent=2))

    except Exception as e:
        _log(f"Error initializing presets: {e}")


def _allowed_cors_origins() -> list[str]:
    """
    T2 加固：原本用 `"*"` 當 aiohttp_cors 的 defaults key，疊加 allow_credentials=True，
    等於對任意呼叫來源（含惡意網頁）都核發帶憑證的 CORS 許可，讓瀏覽器能對
    /api/mcp/{name}/{action}、/api/mcp-local-config/{name} 等會實際啟動/停止 Docker
    容器（透過掛載的 docker.sock）的端點發出跨站請求。改為明確白名單：
    只有前端實際會被載入的來源才給 CORS 許可，其餘來源一律不核發
    Access-Control-Allow-Origin，瀏覽器就會擋下 preflight，跨站請求送不出去。
    """
    origins = [
        "http://localhost:4200", "http://127.0.0.1:4200",  # ng serve / docker-compose 前端
        "null",  # 封裝後 Electron 從 file:// 載入頁面時的 Origin
    ]
    extra = os.environ.get("CLAUDE_DESKTOP_EXTRA_ORIGINS", "").strip()
    if extra:
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
    return origins


def build_app() -> web.Application:
    # _init_db() needs CLAUDE_HOME to already exist (sqlite3 won't create the
    # parent directory for its .db file). _init_presets() creates it further
    # below as a side effect of mkdir(parents=True) on its subdirectories, but
    # that runs AFTER _init_db() — on a genuinely fresh CLAUDE_HOME (new
    # install, or a docker volume mount pointing at an empty host directory)
    # this crashed at startup before ever reaching _init_presets().
    CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
    _init_db()
    _migrate_db()
    _migrate_fts_tokenizer()
    _backfill_project_paths()
    _init_presets()
    app = web.Application(client_max_size=_UPLOAD_MAX_BYTES + 1024)

    _cors_options = aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    cors = aiohttp_cors.setup(app, defaults={
        origin: _cors_options for origin in _allowed_cors_origins()
    })

    # Routes grouped by resource path to avoid double-CORS registration
    from routes.mcp_debugger import handle_mcp_rpc
    from routes.run_artifacts import handle_run_artifacts

    route_groups: dict[str, list[tuple[str, any]]] = {}
    for method, path, handler in [
        ("GET",    "/api/team/run/{run_id}/artifacts", handle_run_artifacts),
        ("POST",   "/api/mcp/rpc",         handle_mcp_rpc),
        ("GET",    "/api/usage",           handle_usage),
        ("GET",    "/api/usage/codex",     handle_codex_usage),
        ("POST",   "/api/chat",           handle_chat),
        ("POST",   "/api/team/chat",      handle_team_chat),
        ("POST",   "/api/team/execute",   handle_team_execute),
        ("POST",   "/api/team/authorize", handle_team_authorize),
        ("POST",   "/api/chat/stop",      handle_chat_stop),
        ("POST",   "/api/chat/clear",     handle_chat_clear),
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
        ("GET",    "/api/sessions/{id}/messages",      handle_session_messages),
        ("DELETE", "/api/sessions/{id}",               handle_session_delete),
        ("POST",   "/api/sessions/{id}/truncate",      handle_session_truncate),
        ("PATCH",  "/api/sessions/{id}",               handle_session_rename),
        ("POST",   "/api/sessions/{id}/auto-title",    handle_session_auto_title),
        ("POST",   "/api/skills/generate",     handle_skill_generate),
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

    # ── Modular Routes ──
    import sys
    sys.path.append(str(Path(__file__).parent))
    try:
        from routes import register_agent_routes, register_skill_routes, register_team_routes, register_mcp_server_routes, register_engine_routes, register_resource_sync_routes
    except ImportError:
        from backend.routes import register_agent_routes, register_skill_routes, register_team_routes, register_mcp_server_routes, register_engine_routes, register_resource_sync_routes

    register_agent_routes(app, cors.add)
    register_skill_routes(app, cors.add)
    register_team_routes(app, cors.add)
    register_mcp_server_routes(app, cors.add)
    register_engine_routes(app, cors.add)
    register_resource_sync_routes(app, cors.add)

    async def cleanup_processes(app_ref):
        _log("[cleanup] Shutting down, cleaning up all active processes...")
        for k in list(active_procs.keys()):
            proc = active_procs.pop(k, None)
            if proc:
                safe_kill_process(proc)
        for name in list(_mcp_procs.keys()):
            proc = _mcp_procs.pop(name, None)
            if proc:
                safe_kill_process(proc)

        if HAS_AGENT_SDK and _team_pool is not None:
            try:
                await _team_pool.evict_all()
            except Exception:
                pass

    app.on_cleanup.append(cleanup_processes)

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
        to = _load_config().get("lineAdminUserId", "").strip()
    if not to:
        print("[schedule] LINE recipient (to) is empty and lineAdminUserId is not set")
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


_SCHEDULE_TIMEOUT = 300  # 5 分鐘，防止 schedule 無限 hang

async def run_schedule_prompt(schedule: dict) -> None:
    prompt = schedule["prompt"] if isinstance(schedule, dict) else schedule
    
    cfg = _load_config()
    permission_mode = cfg.get("permissionMode", "")
    model = cfg.get("model", "")
    effort = cfg.get("effort", "")
    
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json"]
    if permission_mode and permission_mode not in ("default", ""):
        cmd += ["--permission-mode", permission_mode]
    if model and model not in ("sonnet", ""):
        cmd += ["--model", model]
    if effort and effort != "medium":
        cmd += ["--effort", effort]

    env = os.environ.copy()
    key = _resolve_api_key()
    if key:
        env["ANTHROPIC_API_KEY"] = key
    proc = None
    try:
        cmd = wrap_cmd(cmd[0], cmd[1:])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,  # 防止背景卡死
            env=env,
            cwd=str(Path.home()),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SCHEDULE_TIMEOUT)
        result_text = ""
        try:
            output = json.loads(stdout.decode("utf-8", errors="replace"))
            result_text = output.get("result", "")
        except Exception:
            pass
        print(f"[schedule] Prompt finished, result length: {len(result_text)}")
        if result_text:
            # 依使用者需求，預設直接推送至 LINE Admin 帳號
            to = _load_config().get("lineAdminUserId", "").strip()
            await _send_line_message(to, result_text)
    except asyncio.TimeoutError:
        print(f"[schedule] Prompt timed out after {_SCHEDULE_TIMEOUT}s")
        if proc:
            safe_kill_process(proc)
    except Exception as e:
        print(f"[schedule] Error running prompt: {e}")
        if proc:
            try:
                safe_kill_process(proc)
            except Exception:
                pass




async def on_startup(app: web.Application) -> None:
    global _tg_task
    _log(f"Backend started. Claude: {CLAUDE_BIN}")
    asyncio.create_task(run_schedule_runner())
    # 預熱引擎可用性 cache，避免開機後第一個真的用到的請求要付冷啟動的
    # subprocess spawn 成本（claude auth status / codex login status）。
    from engines.availability import get_status as _prime_engine_status
    asyncio.create_task(_prime_engine_status())
    if HAS_AGENT_SDK and _team_pool is not None:
        from session_pool import run_idle_pruner
        asyncio.create_task(run_idle_pruner(_team_pool))
    # Auto-start Telegram bot if configured
    tg_cfg = _load_tg_config()
    _tg_state.update({"token": tg_cfg.get("token",""), "enabled": tg_cfg.get("enabled", False)})
    if tg_cfg.get("enabled") and tg_cfg.get("token"):
        _tg_task = asyncio.create_task(_telegram_poll())


if __name__ == "__main__":
    # 健檢第二輪修復：這支後端沒有任何身分驗證層（所有 state-changing 端點
    # 都只靠 CORS + 「同機信任」假設），綁 0.0.0.0 等於讓同一個 LAN/VPN 上的
    # 任何主機都能直接 curl 到（CORS 只擋瀏覽器，不擋直接發送 HTTP 請求的用戶端）。
    # Electron 桌面版本身跑在 host 上，只需要 loopback 就夠；docker-compose
    # 部署下的容器需要接受同網段其他容器（frontend/ngrok）連線，透過
    # BACKEND_BIND_HOST=0.0.0.0（docker-compose.yml 已設定）明確選擇放寬。
    bind_host = os.environ.get("BACKEND_BIND_HOST", "127.0.0.1")
    print(f"Agent Desktop backend starting on http://{bind_host}:8765")
    app = build_app()
    app.on_startup.append(on_startup)
    web.run_app(app, host=bind_host, port=8765)
