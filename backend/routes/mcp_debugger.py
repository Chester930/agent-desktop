import json
import asyncio
import secrets
import time
from aiohttp import web
from pathlib import Path
from database import CLAUDE_HOME, _analyze_mcp_entry

def _is_safe_name(name: str) -> bool:
    return name and "/" not in name and "\\" not in name and ".." not in name

# 敏感操作授權：pending_id 由伺服器產生並保存，不再信任呼叫方自報的 `authorized` 布林值。
# 單次使用（驗證通過即 pop），並設 TTL 避免無人領取的請求無限累積。
_PENDING_AUTH: dict[str, dict] = {}
_PENDING_AUTH_TTL_SECONDS = 120

def _cleanup_pending_auth() -> None:
    now = time.time()
    expired = [pid for pid, entry in _PENDING_AUTH.items() if entry["expires_at"] < now]
    for pid in expired:
        _PENDING_AUTH.pop(pid, None)

def _consume_pending_auth(pending_id: str, mcp_name: str, method: str, tool_name: str, params: dict) -> bool:
    """驗證並消耗一次 pending 授權。必須是伺服器先前核發、且對應同一筆請求內容。"""
    if not pending_id:
        return False
    entry = _PENDING_AUTH.pop(pending_id, None)
    if not entry or entry["expires_at"] < time.time():
        return False
    return (
        entry["mcp_name"] == mcp_name
        and entry["method"] == method
        and entry["tool_name"] == tool_name
        and entry["params"] == params
    )

async def handle_mcp_rpc(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    mcp_name = data.get("mcp_name", "")
    method = data.get("method", "")
    params = data.get("params", {})

    if not mcp_name or not method:
        return web.json_response({"error": "Missing 'mcp_name' or 'method'"}, status=400)

    if not _is_safe_name(mcp_name):
        return web.json_response({"error": "Invalid MCP name"}, status=400)

    # 敏感工具安全閘口 (Sensitive Tool Gatekeeper)
    if method == "tools/call":
        tool_name = params.get("name", "").lower()
        SENSITIVE_KEYWORDS = {"execute", "write", "delete", "remove", "install"}
        if any(kw in tool_name for kw in SENSITIVE_KEYWORDS):
            _cleanup_pending_auth()
            authorized = _consume_pending_auth(
                data.get("pending_id", ""), mcp_name, method, tool_name, params
            )
            if not authorized:
                new_pending_id = secrets.token_urlsafe(24)
                _PENDING_AUTH[new_pending_id] = {
                    "mcp_name": mcp_name,
                    "method": method,
                    "tool_name": tool_name,
                    "params": params,
                    "expires_at": time.time() + _PENDING_AUTH_TTL_SECONDS,
                }
                return web.json_response({
                    "status": "pending_authorization",
                    "pending_id": new_pending_id,
                    "error": f"敏感操作攔截：Agent 試圖呼叫具破壞性的敏感工具 '{params.get('name')}'。此操作已被系統自動掛起，請確認是否授權放行？"
                }, status=403)

    info = _analyze_mcp_entry(mcp_name)
    if not info or not info.get("command"):
        return web.json_response({"error": f"MCP server '{mcp_name}' not configured"}, status=404)

    cmd = info["command"]
    args = info.get("args", [])

    rpc_req = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": method,
        "params": params
    }
    req_str = json.dumps(rpc_req) + "\n"

    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
    except Exception as e:
        return web.json_response({"error": f"Failed to spawn MCP process: {str(e)}"}, status=500)

    try:
        # 1. 執行 MCP 標準 initialize 協議握手，保障與標準 MCP Server 的極致協議相容性
        if method != "initialize":
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-desktop-debugger", "version": "1.0.0"}
                }
            }
            try:
                proc.stdin.write((json.dumps(init_req) + "\n").encode("utf-8"))
                await proc.stdin.drain()
                # 讀取並忽略初始化響應
                await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
            except Exception:
                pass

        proc.stdin.write(req_str.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        try:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
            if not line_bytes:
                return web.json_response({"error": "MCP server returned empty response"}, status=502)
            
            resp_str = line_bytes.decode("utf-8").strip()
            resp_json = json.loads(resp_str)
            return web.json_response(resp_json)
        except asyncio.TimeoutError:
            return web.json_response({"error": "MCP server timeout (no response within 5s)"}, status=504)
        except Exception as e:
            return web.json_response({"error": f"Error reading response: {str(e)}"}, status=502)
    finally:
        try:
            proc.terminate()
            await proc.wait()
        except Exception:
            pass
