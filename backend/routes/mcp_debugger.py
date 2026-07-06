import json
import asyncio
from aiohttp import web
from pathlib import Path
from database import CLAUDE_HOME, _analyze_mcp_entry

def _is_safe_name(name: str) -> bool:
    return name and "/" not in name and "\\" not in name and ".." not in name

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
            if not data.get("authorized"):
                return web.json_response({
                    "status": "pending_authorization",
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
