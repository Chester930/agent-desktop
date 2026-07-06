import os
from aiohttp import web
from pathlib import Path
from routes.teams import _team_runs

def _is_safe_id(run_id: str) -> bool:
    return run_id and "/" not in run_id and "\\" not in run_id and ".." not in run_id

async def handle_run_artifacts(request: web.Request) -> web.Response:
    run_id = request.match_info.get("run_id", "")
    if not _is_safe_id(run_id):
        return web.json_response({"error": "Invalid run_id"}, status=400)

    if run_id not in _team_runs:
        return web.json_response({"error": f"Team run '{run_id}' not found"}, status=404)

    run_data = _team_runs[run_id]
    cwd = run_data.get("cwd", "")
    
    base_dir = Path(cwd).resolve() if (cwd and Path(cwd).is_dir()) else Path.cwd().resolve()
    
    artifact_paths = run_data.get("artifacts", [])
    results = []
    
    for rel_path in artifact_paths:
        full_path = base_dir / rel_path
        try:
            # 確保檔案路徑確實位於專案根目錄下，杜絕 Directory Traversal 目錄逃逸漏洞
            # （用 is_relative_to 而非字串前綴比對，避免 /proj-evil 這種同層兄弟目錄誤判通過）
            resolved_full = full_path.resolve()
            if not resolved_full.is_relative_to(base_dir):
                continue
        except Exception:
            continue

        if full_path.exists() and full_path.is_file():
            try:
                stat = full_path.stat()
                ext = full_path.suffix.lower()
                
                preview_type = "text"
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                    preview_type = "image"
                elif ext in (".pdf",):
                    preview_type = "pdf"
                elif ext in (".json", ".yaml", ".yml"):
                    preview_type = "data"
                
                results.append({
                    "filename": full_path.name,
                    "relative_path": rel_path,
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "preview_type": preview_type
                })
            except Exception:
                pass
                
    return web.json_response({
        "run_id": run_id,
        "workspace": str(base_dir),
        "artifacts": results
    })
