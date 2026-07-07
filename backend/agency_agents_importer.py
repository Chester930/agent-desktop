import sys
import os
import json
import re
import urllib.request
import urllib.error
from pathlib import Path

# Resolve CLAUDE_HOME
_DEFAULT_CLAUDE_HOME = Path.home() / ".claude"
CONFIG_FILE = _DEFAULT_CLAUDE_HOME / "claude-desktop-config.json"

def get_claude_home() -> Path:
    try:
        if CONFIG_FILE.exists():
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            raw = config.get("claudeHome", "").strip()
            if raw:
                p = Path(raw).expanduser()
                if p.is_dir():
                    return p
    except Exception:
        pass
    return _DEFAULT_CLAUDE_HOME

def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (ClaudeDesktop Importer)'}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))

def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (ClaudeDesktop Importer)'}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode('utf-8', errors='replace')

def _is_safe_id(name: str) -> bool:
    """div_key 會被拿去拼 team_id/team_file 檔名（見下方 run_import）。這個值
    來自釘死版本的上游 GitHub repo，正常情況下受信任，但如果上游 repo 或
    這支 script 之後改成接受使用者提供的來源，一個惡意的 divisions.json key
    （例如 "../../../../etc"）就能造成路徑穿越寫入。防禦深度：擋掉。"""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def run_import(dry_run=False) -> dict:
    # 1. Fetch divisions.json
    print("Fetching divisions...")
    divisions_data = fetch_json("https://raw.githubusercontent.com/msitarzewski/agency-agents/main/divisions.json")
    divisions = {k: v for k, v in divisions_data.get("divisions", {}).items() if _is_safe_id(k)}
    
    # 2. Fetch repo file list (recursive tree)
    print("Fetching file list from GitHub repository...")
    tree_data = fetch_json("https://api.github.com/repos/msitarzewski/agency-agents/git/trees/main?recursive=1")
    tree = tree_data.get("tree", [])
    
    # 3. Filter markdown files belonging to active divisions
    agent_paths = []
    for item in tree:
        path = item.get("path", "")
        if path.endswith(".md") and "/" in path:
            parts = path.split("/")
            div_key = parts[0]
            # Must be a valid division key and not in ignored directories
            if div_key in divisions and div_key not in ("strategy", "integrations", "docs", "examples", "scripts"):
                agent_paths.append((div_key, path))
                
    print(f"Found {len(agent_paths)} potential agents in {len(divisions)} divisions.")
    
    # Setup directories
    claude_home = get_claude_home()
    agents_dir = claude_home / "agents"
    souls_dir = claude_home / "souls"
    teams_dir = claude_home / "teams"
    
    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
        souls_dir.mkdir(parents=True, exist_ok=True)
        teams_dir.mkdir(parents=True, exist_ok=True)
        
    # Track which agents were successfully imported for each division (to construct Teams)
    division_members = {k: [] for k in divisions.keys()}
    imported_agents_count = 0
    
    # Let's import PyYAML
    import yaml
    
    for div_key, path in agent_paths:
        file_stem = Path(path).stem
        # e.g. path: engineering/engineering-frontend-developer.md
        # agent_id: engineering-frontend-developer
        agent_id = file_stem
        
        print(f"Processing agent: {agent_id}...")
        try:
            raw_url = f"https://raw.githubusercontent.com/msitarzewski/agency-agents/main/{path}"
            raw_content = fetch_text(raw_url)
            
            # Parse YAML frontmatter
            if raw_content.startswith("---"):
                parts = raw_content.split("---", 2)
                fm_data = yaml.safe_load(parts[1]) if len(parts) >= 2 else {}
                body = parts[2].strip() if len(parts) >= 3 else ""
            else:
                fm_data = {}
                body = raw_content.strip()
                
            name = fm_data.get("name", agent_id)
            description = fm_data.get("description", "")
            
            # Write agent configuration
            agent_md_content = f"""---
name: {name}
description: {description}
tools: Read, Grep, Glob
skills: []
memory: []
mcp: []
output_memory: []
---

## {name}

{description}
"""
            if not dry_run:
                (agents_dir / f"{agent_id}.md").write_text(agent_md_content, encoding="utf-8")
                (souls_dir / f"{agent_id}.md").write_text(body, encoding="utf-8")
                
            division_members[div_key].append({
                "id": agent_id,
                "name": name,
                "description": description
            })
            imported_agents_count += 1
        except Exception as e:
            print(f"Error importing {agent_id}: {e}", file=sys.stderr)
            
    # Build Teams
    imported_teams_count = 0
    for div_key, agents in division_members.items():
        if not agents:
            continue
            
        div_info = divisions[div_key]
        team_name = f"{div_info.get('label', div_key)} Team"
        team_id = f"{div_key}-team"
        team_desc = f"Division Team for {div_info.get('label', div_key)} from agency-agents catalog."
        
        # Determine leader
        leader_id = ""
        for a in agents:
            lower_id = a["id"].lower()
            lower_name = a["name"].lower()
            if any(x in lower_id or x in lower_name for x in ("lead", "manager", "chief", "director", "architect")):
                leader_id = a["id"]
                break
        if not leader_id and agents:
            leader_id = agents[0]["id"]
            
        members_list = []
        for a in agents:
            # Clean role length and handle None description
            role_desc = (a["description"] or a["name"])
            if len(role_desc) > 100:
                role_desc = role_desc[:97] + "..."
            members_list.append({
                "agent": a["id"],
                "role": role_desc
            })
            
        team_data = {
            "name": team_name,
            "description": team_desc,
            "leader": leader_id,
            "members": members_list,
            "execution_mode": "parallel"
        }
        
        print(f"Creating team {team_id} with {len(members_list)} members (Leader: {leader_id})...")
        if not dry_run:
            team_file = teams_dir / f"{team_id}.yaml"
            with open(team_file, "w", encoding="utf-8") as tf:
                yaml.dump(team_data, tf, allow_unicode=True, default_flow_style=False)
            
        imported_teams_count += 1
        
    # Write flag file
    if not dry_run and imported_agents_count > 0:
        flag_file = claude_home / "agency_imported.flag"
        flag_file.write_text(f"Imported at: {flag_file.stat().st_mtime if flag_file.exists() else 'now'}\nAgents: {imported_agents_count}\nTeams: {imported_teams_count}", encoding="utf-8")
        
    return {
        "ok": True,
        "agents_count": imported_agents_count,
        "teams_count": imported_teams_count,
        "message": f"Successfully imported {imported_agents_count} agents and established {imported_teams_count} teams."
    }

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    try:
        res = run_import(dry_run=dry_run)
        print(res["message"])
    except Exception as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)
