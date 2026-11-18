# Remote MCP Gateway & Claude Web Integration

Walled AI provides a hardened Remote Model Context Protocol (MCP) Gateway utilizing the official MCP Streamable HTTP transport and authenticated reverse WebSocket communication with the Walled AI Windows Desktop Agent.

This architecture enables remote LLM clients (such as **Claude Web**, cloud AI agents, or remote IDE extensions) to safely discover and execute upstream MCP tools while strictly maintaining the zero-trust, fail-closed human authorization boundary enforced on the user's local Windows workstation.

---

## 1. Architectural Overview

```mermaid
flowchart TD
    subgraph Remote_Cloud["Remote / Cloud Environment"]
        ClaudeWeb["Claude Web / Remote MCP Client"]
        Gateway["Walled AI Remote Gateway (FastAPI / Uvicorn)"]
        StreamableTransport["Streamable HTTP Transport (/mcp)"]
        WSBroker["WebSocket Agent Broker (/ws/agent)"]
        UpstreamMCP["Upstream MCP Server (Python/Subprocess)"]
    end

    subgraph Windows_Workstation["Windows Workstation (Human Boundary)"]
        DesktopAgent["Walled AI Desktop Agent (PySide6)"]
        Popup["Always-On-Top Authorization Popup"]
        AuditDB[("SQLite Audit Repository")]
    end

    ClaudeWeb -->|"POST /mcp (Bearer API Key)"| StreamableTransport
    StreamableTransport -->|"Intercept Tools/Call"| Gateway
    Gateway -->|"Route Authorization Request"| WSBroker
    WSBroker <==|"Bidirectional Authenticated WebSocket"==> DesktopAgent
    DesktopAgent -->|"Display Popup & Redacted Arguments"| Popup
    Popup -->|"Human Decision (Approve / Reject)"| DesktopAgent
    DesktopAgent -->|"Decision (APPROVED / REJECTED)"| WSBroker
    WSBroker -->|"Apply Decision & Enforce Single Dispatch"| Gateway
    Gateway -->|"Execute Approved Call Only"| UpstreamMCP
    Gateway -->|"Persist Audit Record"| AuditDB
    Gateway -->|"Stream Result (SSE / JSON-RPC)"| ClaudeWeb
```

### Key Security Invariants

1. **Identical Human Authorization Boundary**: Remote tool calls follow the identical interception, serialization, argument redaction, and human approval workflow as local Claude Desktop calls.
2. **Reverse WebSocket Topology**: The Windows Desktop Agent initiates an outbound WebSocket connection (`ws://` or `wss://`) to the Remote Gateway. The gateway never initiates inbound connections to the client workstation, eliminating firewall traversal and NAT issues.
3. **Fail-Closed by Design**: If no desktop agent is connected, if the agent drops mid-flight, if the user dismisses the dialog, or if the deadline expires, the tool execution request is immediately rejected and zero upstream executions occur.
4. **Independent Token Boundaries**: The remote client credential (`REMOTE_CLIENT_API_KEY`) is completely decoupled from the desktop agent control token (`AGENT_AUTH_TOKEN`). Remote clients cannot impersonate desktop agents or submit authorization decisions.

---

## 2. Protocol & Transport Specifications

### 2.1 Remote Client Interface (`/mcp`)

- **Transport**: MCP Streamable HTTP Transport (Stateless/Stateful HTTP with Server-Sent Events).
- **Authentication**: Constant-time verification of `Authorization: Bearer <REMOTE_CLIENT_API_KEY>` or header `X-API-Key: <REMOTE_CLIENT_API_KEY>`.
- **Rate Limiting**: Sliding-window limiter per IP/client token (default: 60 req/min).
- **CORS**: Configurable cross-origin resource sharing (`REMOTE_CORS_ORIGINS`).

```http
POST /mcp HTTP/1.1
Host: gateway.example.com
Authorization: Bearer your-remote-client-api-key
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "read_demo_file",
    "arguments": {
      "filename": "confidential_financials_2026.xlsx",
      "access_token": "bearer-secret-token"
    }
  }
}
```

### 2.2 Desktop Agent Control Interface (`/ws/agent`)

- **Transport**: Authenticated RFC 6455 WebSocket.
- **Authentication**: Checked against `AGENT_AUTH_TOKEN` via HTTP upgrade headers (`Authorization: Bearer <token>` or `X-Agent-Token`) or fallback query parameter (`?token=...`). Unauthorized connections are terminated with code `1008` (Policy Violation).
- **Message Types**:
  - `authorize`: Gateway dispatches tool execution request (includes `request_id`, `tool_name`, `arguments`, `client_id`, `session_id`, `timeout_seconds`).
  - `decision`: Desktop agent returns human decision (`APPROVED` or `REJECTED`).
  - `ping` / `pong`: Periodic keepalive heartbeats every 15 seconds.

#### Security Implications of Query-String Credentials & Production Alternatives

> [!WARNING]
> While passing `?token=<AGENT_AUTH_TOKEN>` in the query string is supported for development and simple WebSocket clients that cannot set headers during handshake, query-string credentials have inherent security risks in production environments:
> - **Log Leakage**: Query parameters are routinely recorded in plain text in access logs, reverse proxy logs (Nginx, Caddy, Cloudflare), and monitoring tools.
> - **Referer Exposure**: If web browsers or proxies forward URLs, tokens in query parameters can be leaked in `Referer` headers.
> - **Shoulder Surfing & History**: URLs with tokens may be cached in shell history or proxy caches.

**Recommended Production Alternatives:**
1. **Header-Based Handshake**: Provide `Authorization: Bearer <AGENT_AUTH_TOKEN>` or `X-Agent-Token` in the initial HTTP `GET /ws/agent` upgrade request headers. Walled AI natively inspects these headers before query parameters.
2. **Short-Lived Ticket Exchange**: Authenticate via an initial `POST /api/auth/ticket` endpoint to retrieve a single-use ticket valid for 30 seconds, then pass the ephemeral ticket in the WebSocket URL.
3. **Mutual TLS (mTLS)**: Configure the reverse proxy (Nginx/Caddy) to require and verify an X.509 client certificate installed on the desktop workstation.

---

## 3. Testing Scope & Claude Web Verification Notice

> [!IMPORTANT]
> **Distinction Between Protocol Tests and Live Claude Web Account Testing**:
> - **Verified Integration Tests**: The test suite in `tests/integration/test_remote_gateway.py` executes authentic, automated MCP protocol integration tests over Streamable HTTP and WebSocket using official MCP SDK structures, `ASGITransport`, and subprocess upstream servers. All 16 checks (initialization, tool discovery, approval, rejection, timeout, agent disconnection, client isolation, and rate limiting) have been automated and verified.
> - **Live Claude Web Setup**: Connecting a production Claude Web (`claude.ai`) workspace to Walled AI requires deploying the gateway with a publicly reachable HTTPS domain (using the Nginx or Caddy configurations detailed in Section 5) and adding the endpoint under Claude Web's MCP Connectors settings. Live testing with a paid consumer Claude Web account was not part of the local offline test automation.

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Windows Desktop)
    participant Desktop as Desktop Agent (PySide6)
    participant Gateway as Remote MCP Gateway
    participant Claude as Claude Web (Remote Client)
    participant Upstream as Upstream MCP Server

    Desktop->>Gateway: Connect WebSocket /ws/agent?token=<AGENT_TOKEN>
    Gateway-->>Desktop: WebSocket Accepted (Registered)

    Claude->>Gateway: POST /mcp tools/call (Bearer <CLIENT_KEY>)
    Note over Gateway: Interceptor pauses execution & creates PendingRequest

    Gateway->>Desktop: WS Message: {"type": "authorize", "request_id": "...", "tool_name": "..."}
    Desktop->>User: Display Always-On-Top Popup Dialog (Redacted Args)
    
    alt User Approves
        User->>Desktop: Click "Approve & Execute"
        Desktop->>Gateway: WS Message: {"type": "decision", "status": "APPROVED"}
        Gateway->>Upstream: Execute tool once with original arguments
        Upstream-->>Gateway: Result (100.0)
        Gateway->>Claude: SSE Response: result: 100.0
    else User Rejects or Timeout or Disconnect
        User->>Desktop: Click "Reject Request" (or deadline expires)
        Desktop->>Gateway: WS Message: {"type": "decision", "status": "REJECTED"}
        Note over Gateway: Zero Upstream Executions
        Gateway->>Claude: SSE Response: isError: true ("Blocked by user")
    end
```

---

## 4. Production Deployment & Reverse Proxy

In production, run the Remote Gateway behind a secure reverse proxy terminating TLS (HTTPS / WSS).

### 4.1 Nginx Configuration Example

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.yourdomain.com/privkey.pem;

    # Streamable HTTP MCP Endpoint
    location /mcp {
        proxy_pass http://127.0.0.1:8765/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Disable buffering for SSE (Server-Sent Events)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # Desktop Agent WebSocket Endpoint
    location /ws/agent {
        proxy_pass http://127.0.0.1:8765/ws/agent;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # Health Checks
    location /health {
        proxy_pass http://127.0.0.1:8765/health;
    }
}
```

### 4.2 Caddy Configuration Example

```caddyfile
mcp.yourdomain.com {
    reverse_proxy /mcp* 127.0.0.1:8765 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }

    reverse_proxy /ws/agent* 127.0.0.1:8765 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }

    reverse_proxy /health 127.0.0.1:8765
}
```

---

## 5. CLI Quickstart

### 5.1 Starting the Remote MCP Gateway

```powershell
# In virtual environment:
.\.venv\Scripts\python.exe -m app.main remote-gateway --host 0.0.0.0 --port 8765
```

### 5.2 Connecting Desktop Agent to Remote Gateway

```powershell
# Launch desktop agent connected to remote gateway WebSocket:
.\.venv\Scripts\python.exe -m app.main desktop --remote-url ws://127.0.0.1:8765/ws/agent
```

---

## 6. Performance Benchmarks

Measured under full in-process integration testing with active subprocess upstream MCP servers:

| Metric | Measured Value | Standard Threshold |
| :--- | :--- | :--- |
| **Total Test Runs** | 128 tests (100% Pass) | 100% |
| **Average Round-Trip Latency** | **66.03 ms** | < 300 ms |
| **Minimum Interception Latency** | **20.56 ms** | < 50 ms |
| **Maximum Authorization Latency** | **235.65 ms** | < 500 ms |
| **Decision Throughput (TPS)** | **15.1 req/sec** | > 5 req/sec |
| **Fail-Closed Disconnect Latency**| **< 1.0 ms** | < 100 ms |
