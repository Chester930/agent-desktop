"""
helpers.py — Pure helper functions (no mutable state).

Extracted from main.py to support modular route handlers.
All handlers in routes/ should import from here instead of main.
"""

import asyncio
import re as _re
from pathlib import Path


# ── Markdown / frontmatter helpers ───────────────────────────────────────────

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
                    return val.strip("\"'")
    return " ".join(x for x in buf if x)


def _desc_from_md_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        desc = _parse_frontmatter_desc(text)
        if desc:
            return desc
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


def _read_agent_body(agent_file: Path) -> str:
    """讀取 agent .md 的 body（跳過 YAML frontmatter）。"""
    try:
        text = agent_file.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            return parts[2].strip() if len(parts) >= 3 else text
        return text.strip()
    except Exception:
        return ""


def _read_skills_content(skills_dir: Path, skill_ids: list) -> str:
    """讀取 Agent 引用的 skill 實際內容（不只 metadata），折進 prompt 用。

    Agent 的 skills: [...] 欄位之前只當標籤存進 metadata，從沒有人把 skill
    檔案的實際內容讀出來——真正生效與否完全依賴底層 CLI 自己原生的
    slash-skill 機制（Claude 認得 ~/.claude/skills/，Codex 認的是不同路徑
    ~/.agents/skills/ 且已知目前是壞的）。這裡讓 app 自己讀內容、手動塞進
    prompt，比照 _read_agent_body() 對 agent body 的做法，讓 skill 對兩個
    引擎都真正生效，不再依賴任何一邊 CLI 的原生載入機制。
    """
    if not skill_ids or not skills_dir:
        return ""
    sections = []
    for sid in skill_ids:
        if not isinstance(sid, str) or not sid or "/" in sid or "\\" in sid or ".." in sid:
            continue
        f = skills_dir / f"{sid}.md"
        if not f.exists():
            d = skills_dir / sid
            found = None
            for c in (d / "SKILL.md", d / "README.md"):
                if c.exists():
                    found = c
                    break
            if found is None:
                continue
            f = found
        body = _read_agent_body(f)
        if body:
            sections.append(f"[Skill: {sid}]\n{body}")
    return "\n\n---\n\n".join(sections)


# ── YAML frontmatter parse / write ───────────────────────────────────────────

def _parse_yaml_list(lines: list, start: int):
    """Parse indented YAML list starting at index. Returns (items, next_index)."""
    items, i = [], start
    current_item = None
    base_indent = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        m_list_item = _re.match(r'^\s*-\s+(.*)', line)
        if m_list_item:
            if base_indent is None:
                base_indent = indent

            if current_item is not None:
                items.append(current_item)
                current_item = None

            rest = m_list_item.group(1).strip()
            m_kv = _re.match(r'^([\w][\w-]*):\s*(.*)', rest)
            if m_kv:
                k, v = m_kv.group(1), m_kv.group(2).strip().strip("\"'")
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                current_item = {k: v}
            else:
                items.append(rest.strip("\"'"))
            i += 1
        else:
            m_kv = _re.match(r'^\s*([\w][\w-]*):\s*(.*)', line)
            if current_item is not None and m_kv and base_indent is not None and indent > base_indent:
                k, v = m_kv.group(1), m_kv.group(2).strip().strip("\"'")
                if v.startswith("[") and v.endswith("]"):
                    v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                current_item[k] = v
                i += 1
            else:
                break

    if current_item is not None:
        items.append(current_item)

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


# ── Agent dict builder ────────────────────────────────────────────────────────

def _agent_dict(f: Path) -> dict:
    aid = f.stem
    fm = _parse_full_frontmatter(f)

    # P1-B1: soul reads from frontmatter; fallback to agent id for backward compat
    soul_val = fm.get("soul", "")
    if not soul_val or not isinstance(soul_val, str):
        soul_val = aid

    return {
        "id":            aid,
        "name":          fm.get("name", aid),
        "description":   fm.get("description", _desc_from_md_file(f)),
        "soul":          soul_val,
        "skills":        fm.get("skills", []) if isinstance(fm.get("skills"), list) else [],
        "memory":        fm.get("memory", []) if isinstance(fm.get("memory"), list) else [],
        "mcp":           fm.get("mcp", [])    if isinstance(fm.get("mcp"), list)    else [],
        "output_memory": fm.get("output_memory", []) if isinstance(fm.get("output_memory"), list) else [],
        "tools":         fm.get("tools", ""),
        # 可插拔 agent engine（見 engines/registry.py）：個別 agent 可以在
        # frontmatter 宣告要用哪個引擎執行（例如 "codex"），留空代表沒指定，
        # 由呼叫端的預設值或全域預設決定。
        "engine":        fm.get("engine", "") if isinstance(fm.get("engine"), str) else "",
        "favorite":      bool(fm.get("favorite", False)),
    }


async def _agent_dict_safe(f: Path) -> "dict | None":
    try:
        return await asyncio.to_thread(_agent_dict, f)
    except Exception:
        return None


# ── Skill dict builders ───────────────────────────────────────────────────────

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


# ── Team dict / YAML helpers ──────────────────────────────────────────────────

def _parse_yaml_simple(text: str) -> dict:
    """Parse team YAML using PyYAML, with fallback to regex."""
    if not text:
        return {}

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
        def _val_str(v) -> str:
            if isinstance(v, list):
                return "[" + ", ".join(str(x) for x in v) + "]"
            return str(v)

        lines: list[str] = []
        for key, val in data.items():
            if isinstance(val, list):
                if val:
                    lines.append(f"{key}:")
                    for item in val:
                        if isinstance(item, dict):
                            first = True
                            for k, v in item.items():
                                prefix = "  - " if first else "    "
                                lines.append(f"{prefix}{k}: {_val_str(v)}")
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
    if not isinstance(members_raw, list):
        members_raw = []
    members = []
    for m in members_raw:
        if isinstance(m, dict):
            # P2-B2: preserve input_memory / output_memory per member
            mem_in  = m.get("input_memory", [])
            mem_out = m.get("output_memory", [])
            members.append({
                "agent":         m.get("agent", ""),
                "role":          m.get("role", ""),
                "input_memory":  mem_in  if isinstance(mem_in,  list) else [],
                "output_memory": mem_out if isinstance(mem_out, list) else [],
            })
        elif isinstance(m, str):
            members.append({"agent": m, "role": "", "input_memory": [], "output_memory": []})

    default_leader = members[0]["agent"] if members else ""
    return {
        "id":             f.stem,
        "name":           raw.get("name", f.stem),
        "description":    raw.get("description", ""),
        "leader":         raw.get("leader", "") or default_leader,
        "members":        members,
        "execution_mode": raw.get("execution_mode", "parallel"),
    }


def safe_kill_process(proc) -> None:
    """Safely kill a process and its process tree, especially on Windows."""
    if proc is None:
        return
    import platform
    import subprocess
    try:
        if platform.system() == "Windows":
            # Forcefully terminate the process and all child processes it spawned
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000, # CREATE_NO_WINDOW
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

def wrap_cmd(bin_path: str, args: list[str]) -> list[str]:
    """Wrap command list on Windows if running a .cmd/.bat file to avoid WinError 193."""
    import platform
    import shutil
    cmd = [bin_path] + list(args)
    if platform.system() == "Windows":
        if bin_path.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c"] + cmd
        resolved = shutil.which(bin_path)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", resolved] + list(args)
    return cmd
