"""Test runner simulating a Remote MCP Client (Claude Web) over Streamable HTTP.

Demonstrates:
1. Authenticated session initialization with the Remote Gateway (/mcp).
2. Upstream tool discovery (tools/list).
3. Intercepted tool call execution (tools/call) with live human authorization on Desktop.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Ensure project root is in sys.path when executed directly as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings


def extract_sse_json(body_text: str) -> Dict[str, Any]:
    """Parse JSON payload from text/event-stream Server-Sent Events."""
    for line in body_text.splitlines():
        if line.startswith("data:"):
            raw_json = line[len("data:") :].strip()
            if raw_json:
                try:
                    return json.loads(raw_json)
                except json.JSONDecodeError:
                    pass
    # Fallback to direct json parse if returned as application/json
    try:
        return json.loads(body_text)
    except json.JSONDecodeError:
        return {}


def run_web_test(
    gateway_url: str = "http://127.0.0.1:8766",
    tool_name: str = "calculate_sum",
    tool_args: Dict[str, Any] | None = None,
) -> int:
    """Execute end-to-end MCP Streamable HTTP test."""
    if tool_args is None:
        tool_args = {"a": 42.0, "b": 58.0}

    print("============================================================")
    print("  WALLED AI - REMOTE MCP GATEWAY (CLAUDE WEB) TEST RUNNER")
    print("============================================================")
    print(f"Target Gateway Endpoint : {gateway_url}/mcp")
    print(f"Client API Key          : Bearer {settings.remote_client_api_key}")
    print(f"Target Tool to Invoke   : {tool_name}")
    print(f"Tool Arguments          : {tool_args}")
    print("------------------------------------------------------------\n")

    client = httpx.Client(timeout=45.0)

    # 1. Health & Status Check
    print("[1/4] Checking Remote Gateway health...")
    try:
        health_resp = client.get(f"{gateway_url}/health")
        if health_resp.status_code != 200:
            print(f"[ERROR] Gateway returned non-200 health: {health_resp.status_code}")
            return 1
        data = health_resp.json()
        print(f"      Gateway is UP. Active Desktop Agents: {data.get('active_agents', 0)}")
        if data.get("active_agents", 0) == 0:
            print("\n[!] WARNING: 0 Desktop Agents are connected to the Gateway!")
            print("    Please ensure the Desktop Agent is running:")
            print("    python -m app.main desktop --remote-url ws://127.0.0.1:8766/ws/agent\n")
    except Exception as e:
        print(f"[ERROR] Could not connect to gateway at {gateway_url}: {e}")
        print("\nMake sure the Remote Gateway is running:")
        print("python -m app.main remote-gateway --host 127.0.0.1 --port 8766\n")
        return 1

    # 2. MCP Handshake / Initialize
    print("\n[2/4] Initializing Remote MCP session (POST /mcp)...")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {settings.remote_client_api_key}",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-web-simulation", "version": "1.0.0"},
        },
    }
    init_resp = client.post(f"{gateway_url}/mcp", json=init_payload, headers=headers)
    if init_resp.status_code != 200:
        print(f"[ERROR] MCP Initialize failed with HTTP {init_resp.status_code}: {init_resp.text}")
        return 1

    session_id = init_resp.headers.get("mcp-session-id")
    print(f"      Handshake successful! Session ID: {session_id}")
    if session_id:
        headers["mcp-session-id"] = session_id

    # 3. Discover Tools (tools/list)
    print("\n[3/4] Querying available tools (tools/list)...")
    list_resp = client.post(
        f"{gateway_url}/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )
    tools_data = extract_sse_json(list_resp.text)
    tools = tools_data.get("result", {}).get("tools", [])
    print(f"      Discovered {len(tools)} tools: {[t.get('name') for t in tools]}")

    # 4. Invoke Tool Call (tools/call)
    print(f"\n[4/4] Sending tool invocation '{tool_name}'...")
    print("      >>> REQUEST INTERCEPTED BY WALLED AI GATEWAY <<<")
    print("      [!] An authorization popup should now appear on your Windows Desktop.")
    print("      [!] Please click 'Approve & Execute' or 'Reject' on the popup...")
    start_t = time.time()

    call_payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args,
        },
    }
    call_resp = client.post(f"{gateway_url}/mcp", json=call_payload, headers=headers)
    duration = time.time() - start_t

    print("\n------------------------------------------------------------")
    print(f"GATEWAY RESPONSE RECEIVED ({duration:.2f}s, HTTP {call_resp.status_code}):")
    parsed_resp = extract_sse_json(call_resp.text)
    print(json.dumps(parsed_resp, indent=2))
    print("------------------------------------------------------------")

    is_error = parsed_resp.get("result", {}).get("isError")
    if is_error is False:
        print("RESULT: Tool execution was APPROVED and completed upstream successfully!\n")
    elif is_error is True:
        print("RESULT: Tool execution was REJECTED / BLOCKED by user authorization!\n")
    elif "error" in parsed_resp:
        print(f"RESULT: Gateway returned error: {parsed_resp.get('error')}\n")
    else:
        print("RESULT: Processed with status:", parsed_resp)

    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
    sys.exit(run_web_test(gateway_url=url))
