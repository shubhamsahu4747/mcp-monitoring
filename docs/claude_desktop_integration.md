# Walled AI – Claude Desktop Integration Guide

## Overview

This guide provides end-to-end instructions for integrating the **Walled AI MCP Security Gateway** with the official **Claude Desktop** application on Windows.

---

## 1. Prerequisites

- **Python Interpreter:** Python 3.12.10 64-bit located in the project's virtual environment:
  ```text
  E:\personal-project\mcp-assignment\.venv\Scripts\python.exe
  ```
- **Claude Desktop:** Installed on Windows (`%APPDATA%\Claude\claude_desktop_config.json`).
- **Dependencies:** All dependencies installed in the virtual environment.

---

## 2. Configuration Setup

Claude Desktop reads MCP server configuration from:
```text
%APPDATA%\Claude\claude_desktop_config.json
```

### Automatic Safe Installation

Run the automated installer CLI tool:

```powershell
.\.venv\Scripts\python.exe -m app.main install-claude-config
```

This command automatically:
1. **Creates a timestamped backup** of your existing configuration (e.g. `claude_desktop_config.json.bak.20261118_093554`).
2. **Preserves existing MCP servers** (e.g. filesystem, github, postgres).
3. **Injects the Walled AI entry** with absolute paths and unbuffered stdio transport.

### Configuration Format Reference

```json
{
  "mcpServers": {
    "walled-ai-gateway": {
      "command": "E:\\personal-project\\mcp-assignment\\.venv\\Scripts\\python.exe",
      "args": [
        "-u",
        "-m",
        "app.main",
        "local-proxy",
        "--upstream-cmd",
        "E:\\personal-project\\mcp-assignment\\.venv\\Scripts\\python.exe",
        "--upstream-args",
        "\"E:\\personal-project\\mcp-assignment\\examples\\mock_mcp_server.py\"",
        "--server-name",
        "Safe Mock MCP Server"
      ],
      "env": {
        "PYTHONPATH": "E:\\personal-project\\mcp-assignment",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

> **Important:** Claude Desktop does not reliably expand shell environment variables (e.g. `%APPDATA%`, `${VAR}`). Absolute paths must be used for `command`, `args`, and `PYTHONPATH`.

---

## 3. Configuration Validation & Rollback

### Validate Configuration

Verify that the configuration is syntactically valid, points to real files on disk, and has executable permissions:

```powershell
.\.venv\Scripts\python.exe -m app.main validate-claude-config
```

Expected output:
```text
[CLAUDE DESKTOP CONFIGURATION VALIDATION]
  PASS: Configuration file exists at 'C:\Users\hp\AppData\Roaming\Claude\claude_desktop_config.json'
  PASS: Configuration is valid JSON
  PASS: Found 'mcpServers' object with 1 server(s)
  PASS: Found server entry for 'walled-ai-gateway'
  PASS: Command executable verified: 'E:\personal-project\mcp-assignment\.venv\Scripts\python.exe'
  PASS: Python interpreter verified: Python 3.12.10
  PASS: Proxy command arguments verified ('local-proxy' present)
  PASS: PYTHONPATH directory verified on disk: 'E:\personal-project\mcp-assignment'
  INFO: Walled AI is the only configured MCP server

Result: VALID (Ready for Claude Desktop)
```

### Rollback Configuration

If you ever need to restore your previous configuration:

```powershell
.\.venv\Scripts\python.exe -m app.main rollback-claude-config
```

The system will locate the latest backup in `%APPDATA%\Claude\` and restore it cleanly.

---

## 4. Startup Order & Execution Workflow

To ensure seamless authorization and avoid fail-closed blocks, follow this startup order:

### Step 1: Start the Walled AI Desktop Agent

Open a terminal and start the PySide6 desktop GUI:

```powershell
.\.venv\Scripts\python.exe -m app.main desktop
```

- Binds the local authorization IPC listener on `http://127.0.0.1:8765`.
- Opens the Walled AI Security Gateway dashboard and system tray icon.
- Displays `Desktop Agent: Connected (Port 8765)` and `Upstream: Connected`.

### Step 2: Launch or Restart Claude Desktop

Open Claude Desktop.
- Claude Desktop reads `claude_desktop_config.json`.
- It spawns the Walled AI local proxy over stdio.
- The proxy establishes capability negotiation with Claude Desktop downstream and the Safe Mock MCP Server upstream.
- Diagnostics are routed strictly to `sys.stderr`, keeping `sys.stdout` clean for JSON-RPC messages.

---

## 5. End-to-End Interception Scenarios

### Scenario A: Tool Discovery (`tools/list`)
- Claude Desktop queries the proxy for available tools.
- The proxy queries the upstream server and returns:
  1. `get_system_info`: Returns host runtime environment.
  2. `calculate_sum`: Performs safe numerical addition.
  3. `read_demo_file`: Read-only access strictly confined to `data/demo_sandbox/`.
  4. `create_demo_file`: Safe write access confined to `data/demo_sandbox/`.

### Scenario B: Request Approval (`Approve & Execute`)
- User prompts Claude Desktop: *"Calculate 42 + 58"*.
- Claude Desktop issues `tools/call` for `calculate_sum`.
- The gateway intercepts the call and displays an always-on-top authorization dialog:
  - Displays tool name: `calculate_sum`.
  - Displays arguments: `{"a": 42.0, "b": 58.0}`.
- User clicks **`Approve & Execute`** (or presses `Enter`).
- The call forwards to the upstream server, computes `100.0`, and returns to Claude Desktop.
- Exactly 1 upstream execution is recorded with audit outcome `USER_APPROVED`.

### Scenario C: Request Rejection (`Reject Request`)
- User prompts Claude Desktop: *"Delete or overwrite confidential file"*.
- The authorization dialog appears.
- User clicks **`Reject Request`** (or presses `Escape`).
- The gateway immediately fails closed: returns an MCP tool error to Claude Desktop.
- Exactly 0 upstream executions occur. Audit outcome is recorded as `USER_REJECTED`.

### Scenario D: Authorization Timeout
- A request arrives but the user is away from keyboard.
- After countdown expires (default 30s), the gateway automatically fails closed.
- Zero upstream executions occur. Audit outcome is recorded as `BLOCKED_TIMEOUT`.

### Scenario E: Dialog Dismissal (Window Close [X])
- If the user clicks `[X]` to dismiss the popup without explicitly approving:
- The gateway fails closed. Zero upstream executions occur.
- Audit outcome is recorded as `USER_DISMISSED`.

### Scenario F: Desktop Agent Unavailable (Offline)
- If Claude Desktop invokes a tool while the desktop agent is not running:
- The proxy fails closed immediately.
- Returns controlled MCP error:
  `Tool execution was blocked: Walled AI Desktop Agent is unavailable (Walled AI Desktop Agent is not running on http://127.0.0.1:8765)`
- Zero upstream executions occur. Audit outcome is recorded as `BLOCKED_AGENT_UNAVAILABLE`.

---

## 6. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `BLOCKED: Walled AI Desktop Agent is not running` | Desktop GUI process was not launched before tool invocation | Run `.\.venv\Scripts\python.exe -m app.main desktop`. |
| Claude Desktop displays `Server disconnected` | Stdout pollution (logging to stdout rather than stderr) | Verify all loggers route to `sys.stderr`. Walled AI enforces this across all modules. |
| Tool invocation hangs | Python stdout buffering in child subprocess | Ensure `-u` flag and `PYTHONUNBUFFERED=1` are present in `claude_desktop_config.json`. |
| `HTTP 401 Unauthorized` | Invalid agent token passed between proxy and GUI | Verify `settings.agent_auth_token` matches between proxy and desktop agent processes. |
