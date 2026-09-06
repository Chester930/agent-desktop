#!/usr/bin/env python3
"""Fake ACP agent for testing backend/engines/acp_engine.py.

Speaks minimal, spec-shaped ACP JSON-RPC (newline-delimited, protocol v1)
over stdio, standing in for a real `gemini --acp` process so
acp_engine.py's own session-driving and event-conversion logic can be
exercised against a real subprocess + real asyncio pipes (not a hand-rolled
duck-typed stream fake), without needing the actual Gemini CLI installed.

Usage: python fake_acp_agent.py --acp [scenario]
  scenario "normal" (default): agent_message_chunk + tool_call +
    tool_call_update(completed) + a permission_request, then end_turn.
  scenario "cancel": waits for session/cancel before responding to the
    in-flight session/prompt with stopReason "cancelled".
"""
import json
import sys
import time

SESSION_ID = "sid-fake-acp-1"


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    scenario = sys.argv[2] if len(sys.argv) > 2 else "normal"
    cancelled = False

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"sessionId": SESSION_ID}})
        elif method == "session/cancel":
            cancelled = True
        elif method == "session/prompt":
            if scenario == "cancel":
                for _ in range(100):
                    if cancelled:
                        break
                    time.sleep(0.05)
                _write({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "cancelled"}})
                continue

            _write({
                "jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": SESSION_ID, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello from fake acp agent"},
                }},
            })
            _write({
                "jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": SESSION_ID, "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-1", "title": "Bash", "rawInput": {"command": "echo hi"},
                }},
            })
            if scenario == "permission":
                _write({
                    "jsonrpc": "2.0", "id": 999, "method": "session/request_permission",
                    "params": {
                        "sessionId": SESSION_ID,
                        "toolCall": {"toolCallId": "tc-1"},
                        "options": [
                            {"optionId": "reject-1", "name": "Reject", "kind": "reject_once"},
                            {"optionId": "allow-always-1", "name": "Allow Always", "kind": "allow_always"},
                        ],
                    },
                })
                # Consume the client's response line before continuing.
                reply_line = sys.stdin.readline()
                json.loads(reply_line)
            _write({
                "jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": SESSION_ID, "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "tc-1", "status": "completed",
                    "content": [{"type": "content", "content": {"type": "text", "text": "hi\n"}}],
                }},
            })
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {"stopReason": "end_turn"}})


if __name__ == "__main__":
    main()
