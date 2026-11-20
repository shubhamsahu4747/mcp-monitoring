# Walled AI – MCP Communication Monitoring and User Authorization Gateway

**Walled AI** is a production-grade, zero-trust security gateway and authorization broker for the Model Context Protocol (MCP). It intercepts downstream LLM tool calls initiated by local clients (such as **Claude Desktop**) and remote clients (such as **Claude Web** or remote agents) and requires explicit human authorization through an always-on-top desktop dialog before any tool can execute on an upstream MCP server.

---

## 1. Project Overview & Objectives

LLMs equipped with MCP capabilities can execute local shell commands, mutate databases, access sensitive files, and exfiltrate private credentials. Walled AI establishes an unbypassable human verification boundary:

- **Active Protocol Interception**: Halts `tools/call` requests at the protocol layer before execution begins.
- **Fail-Closed Security**: Every execution path defaults to deny. If the desktop agent is offline, an authorization request times out, a network drop occurs, or the user dismisses the dialog, execution is aborted and zero upstream operations take place.
- **Strict Data Redaction**: Automatically identifies and masks sensitive keys (passwords, tokens, API keys, private keys, bearer credentials) in desktop popups, OS notifications, application logs, and SQLite audit records while preserving untouched original arguments for approved execution.
- **Dual-Mode Transport**: Supports both standard I/O (`stdio`) proxying for local Claude Desktop installations and official MCP Streamable HTTP transport with Server-Sent Events (SSE) for remote gateways.
- **Reverse WebSocket Topology**: Allows local Windows desktop agents behind restrictive NAT or corporate firewalls to broker authorization requests from remote cloud gateways via outbound WebSockets without inbound firewall rules.

---

## 2. Milestone Evolution & Features

| Milestone | Scope & Deliverables | Verification Status |
| :--- | :--- | :--- |
| **Milestone 1: Foundation & Security Primitives** | Strict Python 3.12 runtime enforcement, Pydantic v2 schemas, immutable request state machines, recursive parameter redaction, and strict stderr-only logging. | **Verified** (35 unit tests) |
| **Milestone 2: Protocol Proxy & Interception** | Genuine MCP protocol handling, dynamic upstream tool discovery via `tools/list`, atomic request gating, and fail-closed timeout handling. | **Verified** (9 integration tests) |
| **Milestone 3: Concurrency, Audit & Reliability** | Thread-safe concurrent request registries, at-most-once execution dispatch guards, persistent SQLite audit logging with `aiosqlite`, and chaos fault injection tests. | **Verified** (28 concurrency tests) |
| **Milestone 4: Windows Desktop Application** | High-performance PySide6 GUI coordinated via `qasync`, always-on-top authorization popups, countdown progress timers, system tray monitoring, and live audit views. | **Verified** (8 verification tests) |
| **Milestone 5: Claude Desktop Integration** | Stdio proxy with unbuffered I/O (`-u`), automated `%APPDATA%\Claude` configuration management with timestamped backups, validation, rollback, and dismissal semantics. | **Verified** (9 integration tests) |
| **Milestone 6: Remote MCP Gateway & Claude Web** | Official MCP Streamable HTTP transport (`/mcp`), reverse WebSocket broker (`/ws/agent`), rate limiting, client API key authentication, and remote performance benchmarks. | **Verified** (16 integration tests) |

---

## 3. System Architecture

```mermaid
flowchart TD
    subgraph Clients["Downstream LLM Clients"]
        CD["Claude Desktop (Windows App)"]
        CW["Claude Web / Cloud AI Agents"]
    end

    subgraph GatewayBoundary["Walled AI Gateway Boundary"]
        LocalProxy["Local stdio Proxy (app.gateway.local_proxy)"]
        RemoteGateway["Remote Streamable HTTP Gateway (app.remote.server)"]
        
        subgraph SecurityEngine["Core Security & Enforcement Engine"]
            Interceptor["Request Interceptor (app.interception)"]
            AuthManager["Authorization Manager (app.authorization)"]
            Registry["Pending Request Registry (Thread-Safe)"]
            Redactor["Sensitive Data Redactor (app.models)"]
            RateLimit["Sliding Window Rate Limiter (app.remote.auth)"]
        end

        WSBroker["WebSocket Agent Broker (/ws/agent)"]
        AuditDB[("SQLite Audit Repository (aiosqlite)")]
    end

    subgraph LocalWorkstation["Windows Workstation (Human Boundary)"]
        DesktopApp["Walled AI Desktop App (PySide6 / qasync)"]
        Popup["Always-On-Top Authorization Popup"]
        Tray["System Tray & Status Monitor"]
    end

    subgraph UpstreamBoundary["Upstream MCP Servers"]
        MockServer["Safe Mock MCP Server (Examples)"]
        ProdServers["Third-Party MCP Servers (Filesystem, DB, Shell)"]
    end

    CD -->|"stdio JSON-RPC (Unbuffered)"| LocalProxy
    CW -->|"HTTPS POST /mcp (Bearer API Key)"| RemoteGateway

    LocalProxy --> Interceptor
    RemoteGateway --> RateLimit
    RateLimit --> Interceptor
    Interceptor --> Redactor
    Interceptor --> AuthManager
    AuthManager --> Registry

    LocalProxy -.->|"Local IPC (Port 8765)"| DesktopApp
    WSBroker ---|"Authenticated Reverse WebSocket"| DesktopApp

    DesktopApp --> Popup
    DesktopApp --> Tray
    Popup -->|"Human Decision"| DesktopApp

    AuthManager -->|"Record Outcome"| AuditDB
    Interceptor -->|"Approved Tool Calls Only (At-Most-Once)"| UpstreamBoundary
    UpstreamBoundary --> MockServer
    UpstreamBoundary --> ProdServers
```

---

## 4. Local vs. Remote MCP Architecture

### 4.1 Local stdio MCP Architecture (Claude Desktop)

- **Protocol**: Standard I/O streams using JSON-RPC 2.0 frames.
- **Execution Guard**: Claude Desktop launches `python -u -m app.main local-proxy`.
- **Channel Isolation**: Diagnostic and operational logs are strictly written to `sys.stderr`. Standard output (`sys.stdout`) is reserved 100% for valid MCP JSON-RPC protocol messages.
- **IPC Link**: Communicates with the background `WalledAIDesktopApp` over loopback IPC HTTP/WebSocket on `127.0.0.1:8765`.

### 4.2 Remote Streamable HTTP Architecture (Claude Web & Remote Agents)

- **Protocol**: MCP Streamable HTTP transport combining HTTP POST requests and Server-Sent Events (SSE).
- **Endpoint**: Mounted at `/mcp` with direct path rewriting to eliminate client 307 redirects.
- **Session Management**: Backed by `StreamableHTTPSessionManager`, tracking client sessions via `mcp-session-id` headers and managing SSE event streams.
- **Security Boundary**: Enforces rate limiting (default: 60 req/min) and client authentication (`REMOTE_CLIENT_API_KEY`) before any request is processed.

---

## 5. Desktop Agent & Reverse WebSocket Topology

Traditional remote webhooks require the local desktop workstation to open inbound ports or configure dynamic DNS, which is often blocked by corporate firewalls and carrier-grade NAT (CGNAT).

Walled AI resolves this using an **Authenticated Reverse WebSocket** architecture:

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Windows Workstation)
    participant Agent as Desktop Agent (PySide6)
    participant Gateway as Remote Gateway (FastAPI)
    participant Client as Remote Client (Claude Web)
    participant Upstream as Upstream MCP Server

    Agent->>Gateway: Outbound WebSocket Connect: /ws/agent (Bearer Token)
    Gateway-->>Agent: Handshake Accepted (Agent Registered)

    Client->>Gateway: POST /mcp (tools/call calculate_sum)
    Note over Gateway: Intercepted: Execution paused
    
    Gateway->>Agent: WS Frame: authorize request (request_id, arguments)
    Agent->>User: Display Always-On-Top Popup (Redacted Preview)

    alt Explicit User Approval
        User->>Agent: Click Approve & Execute
        Agent->>Gateway: WS Frame: decision APPROVED
        Gateway->>Upstream: Single Dispatch: call_tool(calculate_sum)
        Upstream-->>Gateway: Result (100.0)
        Gateway->>Client: SSE Frame: result 100.0
    else Explicit User Rejection / Dismissal / Timeout / Drop
        User->>Agent: Click Reject / Close Dialog / Timeout Expires
        Agent->>Gateway: WS Frame: decision REJECTED
        Note over Gateway: Upstream server is NEVER contacted (0 calls)
        Gateway->>Client: SSE Frame: error (Blocked by user)
    end
```

---

## 6. Authorization Lifecycle & State Machine

Every intercepted tool call is governed by a strict, concurrency-safe state machine:

```mermaid
stateDiagram-v2
    [*] --> PENDING: Tool Intercepted
    PENDING --> APPROVED: User approves
    PENDING --> REJECTED: User rejects
    PENDING --> REJECTED: Popup dismissed
    PENDING --> TIMED_OUT: Timeout expires fail-closed
    PENDING --> CANCELLED: Gateway or Agent shutdown

    APPROVED --> DISPATCHED: Exactly-once upstream dispatch
    DISPATCHED --> [*]: Upstream execution finished
    REJECTED --> [*]: Blocked 0 upstream executions
    TIMED_OUT --> [*]: Blocked 0 upstream executions
    CANCELLED --> [*]: Blocked 0 upstream executions
```

### Categorized Audit Outcomes

Audit records persisted to SQLite normalize all terminal states:

- `USER_APPROVED`: Explicit human approval.
- `USER_REJECTED`: Explicit human rejection.
- `USER_DISMISSED`: User dismissed or closed the authorization popup without deciding.
- `BLOCKED_TIMEOUT`: The authorization timer expired before receiving a decision.
- `BLOCKED_AGENT_UNAVAILABLE`: Tool call arrived while no desktop agent was connected.
- `BLOCKED_AUTHORIZATION_ERROR`: Authentication error, invalid token, or duplicate decision attempt.

---

## 7. Security Boundaries & Fail-Closed Invariants

1. **Zero-Bypass Policy**: Tool execution routines cannot bypass `RequestInterceptor`. Upstream execution is only callable from within the interceptor after confirming `status == APPROVED`.
2. **At-Most-Once Dispatch Guard**: Once an `APPROVED` request is dispatched upstream, `request.mark_dispatched()` locks the record. Any subsequent dispatch attempt raises `AlreadyDispatchedError`.
3. **Duplicate Decision Prevention**: The WebSocket broker and authorization manager maintain resolved request tracking. Duplicate submissions raise `DuplicateDecisionError` or return `HTTP 409 Conflict`.
4. **Agent Disconnect Handling**: If the WebSocket connection drops while requests are pending, all orphaned requests are immediately resolved as `REJECTED` (`BLOCKED_AGENT_UNAVAILABLE`).
5. **Constant-Time Secret Comparison**: Client and agent authentication tokens are verified using `secrets.compare_digest` to prevent timing attacks.

---

## 8. WebSocket Authentication & Production Security Considerations

Walled AI accepts agent credentials during the initial WebSocket handshake via:

1. `Authorization: Bearer <AGENT_AUTH_TOKEN>` (Header)
2. `X-Agent-Token: <AGENT_AUTH_TOKEN>` (Header)
3. `?token=<AGENT_AUTH_TOKEN>` (Query string parameter)

### Security Implications of Query-String Credentials

> [!WARNING]
> While passing credentials via `?token=...` is provided for broad client compatibility, **query-string credentials pose security risks in production environments**:
>
> - **Access Log Exposure**: Standard reverse proxies (Nginx, Caddy, Cloudflare, AWS ALB) routinely log query strings to access logs in plain text.
> - **Referer Leakage**: URLs containing query parameters may be forwarded in HTTP `Referer` headers if navigation occurs.
> - **Process & History Snooping**: URLs with tokens can be captured in shell command histories, process monitoring tools, or proxy caches.

### Recommended Production Alternatives

For internet-facing deployments:

1. **Header-Based Authentication**: Configure clients to pass `Authorization: Bearer <token>` in the initial HTTP upgrade request headers.
2. **Short-Lived Ticket Exchange**: Request an ephemeral single-use token via an authenticated `POST /api/auth/ticket` endpoint before establishing the WebSocket connection.
3. **Mutual TLS (mTLS)**: Configure the reverse proxy to mandate and verify an X.509 client certificate installed on the desktop agent workstation.

---

## 9. Installation & Windows Setup Guide

### 9.1 Prerequisites

- **OS**: Windows 10/11 (64-bit)
- **Python**: Python 3.12 (Python 3.15 is explicitly unsupported)

Verify Python version:

```powershell
py -3.12 --version
```

### 9.2 Virtual Environment & Dependencies

```powershell
# Navigate to project root
cd mcp-assignment

# Create virtual environment
py -3.12 -m venv .venv

# Activate environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install dependencies
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

### 9.3 Environment Configuration

Copy the template and inspect configuration:

```powershell
Copy-Item .env.example .env
```

Key environment variables:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `AUTH_TIMEOUT_SECONDS` | `30.0` | Deadline in seconds before unanswered requests fail closed. |
| `REDACT_KEYS` | `password,secret,token...` | Comma-separated parameter names redacted in logs and GUI. |
| `DATABASE_PATH` | `data/audit.db` | Local SQLite database file location. |
| `REMOTE_HOST` | `127.0.0.1` | Bind host for remote gateway or local IPC. |
| `REMOTE_PORT` | `8765` | Bind port for remote gateway or local IPC. |
| `AGENT_AUTH_TOKEN` | `<generate-secure-token>` | Secret token required for Desktop Agent connection. |
| `REMOTE_CLIENT_API_KEY` | `<generate-secure-token>` | Bearer token required for remote clients (Claude Web). |
| `RATE_LIMIT_PER_MINUTE` | `60` | Maximum allowed requests per minute per remote client IP. |
| `LOG_LEVEL` | `INFO` | Logging verbosity (written strictly to stderr). |

---

## 10. CLI Usage Reference

All features are accessible via `python -m app.main`:

```powershell
# 1. Display verified environment diagnostics
.\.venv\Scripts\python.exe -m app.main info

# 2. Launch PySide6 Desktop Authorization Agent
.\.venv\Scripts\python.exe -m app.main desktop

# 3. Launch Desktop Agent connected to a Remote Gateway
.\.venv\Scripts\python.exe -m app.main desktop --remote-url ws://127.0.0.1:8765/ws/agent

# 4. Run Local stdio MCP Proxy (Claude Desktop entrypoint)
.\.venv\Scripts\python.exe -m app.main local-proxy

# 5. Launch Remote MCP Gateway (Streamable HTTP for Claude Web)
.\.venv\Scripts\python.exe -m app.main remote-gateway --host 0.0.0.0 --port 8765

# 6. Interactive test client for live interception testing
.\.venv\Scripts\python.exe -m app.main test-client --tool calculate_sum
```

---

## 11. Claude Desktop Integration Guide

Walled AI includes built-in tools to manage `%APPDATA%\Claude\claude_desktop_config.json` safely:

### 11.1 Automated Safe Installation

```powershell
.\.venv\Scripts\python.exe -m app.main install-claude-config
```

- Creates a timestamped backup before writing (e.g. `claude_desktop_config.json.bak.20261118_143000`).
- Preserves all pre-existing third-party MCP servers intact.
- Adds `walled-ai-gateway` pointing to the Python 3.12 virtual environment with `-u` unbuffered transport.

### 11.2 Validate Configuration

```powershell
.\.venv\Scripts\python.exe -m app.main validate-claude-config
```

Validates JSON syntax, executable path existence, Python 3.12 compatibility, and working directory validity.

### 11.3 Rollback Configuration

```powershell
.\.venv\Scripts\python.exe -m app.main rollback-claude-config
```

Restores configuration from the most recent backup file.

Detailed documentation: [claude_desktop_integration.md](docs/claude_desktop_integration.md)

---

## 12. Remote Gateway Deployment Guide

For production deployments serving remote clients like Claude Web:

### 12.1 Start the Gateway

```powershell
.\.venv\Scripts\python.exe -m app.main remote-gateway --host 127.0.0.1 --port 8765
```

### 12.2 Reverse Proxy Configuration (TLS Termination)

#### Nginx Configuration Example

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
        
        # Disable buffering for Server-Sent Events (SSE)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # Desktop Agent Reverse WebSocket Endpoint
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

    location /health {
        proxy_pass http://127.0.0.1:8765/health;
    }
}
```

Detailed deployment instructions: [remote_mcp_gateway.md](docs/remote_mcp_gateway.md)

---

## 13. Testing Scope & Claude Web Verification Notice

> [!IMPORTANT]
> **Distinction Between Protocol Integration Tests and Live Claude Web Account Testing**:
>
> - **Automated MCP Protocol Tests (`tests/integration/test_remote_gateway.py`)**: Executes 16 automated end-to-end integration tests over the authentic Streamable HTTP transport and WebSocket broker using official MCP SDK structures, `ASGITransport`, and safe mock upstream servers. All 16 checks (initialization, tool discovery, approval, rejection, timeout, agent disconnection, client isolation, and rate limiting) are fully automated and verified.
> - **Live Consumer Account Testing**: Connecting a live Claude Web consumer account (`claude.ai`) requires registering the gateway endpoint via Claude Web's MCP Connectors UI over a public HTTPS domain. Live testing with a paid consumer Claude Web account was not part of the local offline automated test suite.

---

## 14. Testing & Code Quality Results

### 14.1 Test Suite Execution

```powershell
# Run full project test suite
.\.venv\Scripts\python.exe -m pytest -v

# Run with test summary
.\.venv\Scripts\python.exe -m pytest -q
```

**Results:**

```text
128 passed in 82.29s (100% pass rate across all 6 milestones)
```

Test Distribution:

- `tests/unit/test_foundation.py`: 35 tests (Pydantic validation, state transitions, redaction, CLI)
- `tests/unit/test_interception.py`: 5 tests (Parser, interceptor flow, error handling)
- `tests/unit/test_desktop_app.py`: 12 tests (UI components, presenter, tray, dismissals)
- `tests/unit/test_storage_and_mock.py`: 4 tests (SQLite CRUD, migrations, mock server)
- `tests/unit/test_fault_injection.py`: 5 tests (Upstream crash, database failure, hook faults)
- `tests/integration/test_mcp_proxy.py`: 9 tests (Local stdio protocol proxying)
- `tests/integration/test_stress_and_performance.py`: 19 tests (Concurrency, registry locks, timers)
- `tests/integration/test_desktop_proxy_integration.py`: 2 tests (Desktop approval / rejection flows)
- `tests/integration/test_ipc_multiprocess.py`: 6 tests (Loopback IPC, WebSocket communication)
- `tests/integration/test_milestone4_verification.py`: 8 tests (Popup UX, redaction, at-most-once)
- `tests/integration/test_claude_desktop_integration.py`: 9 tests (Claude config, rollback, unbuffered stdio)
- `tests/integration/test_remote_gateway.py`: 16 tests (Streamable HTTP, WebSocket broker, rate limits)

### 14.2 Code Quality & Static Analysis

```powershell
# Ruff Linting & Formatting
.\.venv\Scripts\python.exe -m ruff check app tests

# Mypy Static Type Checking
.\.venv\Scripts\python.exe -m mypy app
```

- **Ruff**: `All checks passed! (0 errors)`
- **Mypy**: `Success: no issues found in 36 source files`

---

## 15. Performance Benchmark Methodology & Limitations

Performance metrics were measured during live Streamable HTTP request cycles with an active upstream MCP subprocess (`examples/mock_mcp_server.py`) and an automated WebSocket decision responder:

```text
[PERFORMANCE METRICS - REMOTE MCP GATEWAY]
  Total Calls Evaluated     : 5
  Average Round-Trip Latency: 66.03 ms
  Minimum Latency           : 20.56 ms
  Maximum Latency           : 235.65 ms
  Estimated Decision TPS    : 15.1 req/sec
```

### Methodology & Limitations

- **In-Process Loopback**: Benchmarks were measured using in-memory ASGI transports and loopback subprocess IPC. Real-world WAN network latency (typically 20–80 ms for TLS handshakes) will add to total round-trip time.
- **Human Factor**: In production, human approval latency (typically 2–10 seconds) dominates total response time. The gateway overhead (< 70 ms) represents a negligible fraction of the decision lifecycle.

---

## 16. Known Limitations & Future Roadmap

1. **Single-Agent Decision Routing**: The current WebSocket broker routes requests to the first active registered desktop agent. A multi-operator environment with role-based routing (e.g. routing database tools to DBAs and shell tools to DevOps) is a planned enhancement.
2. **Ephemeral Rate Limiting**: The built-in sliding window rate limiter stores client state in memory. For horizontal gateway clustering behind load balancers, an external cache (e.g. Redis) should be used.
3. **mTLS Management**: Currently, mutual TLS must be terminated at the reverse proxy. Native mTLS support within the Uvicorn ASGI server is scheduled for future releases.

---

## 17. Documentation Index

- [Architecture Guide](docs/architecture.md)
- [Claude Desktop Integration Guide](docs/claude_desktop_integration.md)
- [Remote MCP Gateway & Claude Web Guide](docs/remote_mcp_gateway.md)
- [System Requirements Specification](docs/requirements.md)
- [Verification & Test Plan](docs/test-plan.md)
