# architecture.md

## 1. Context
```
Claude Desktop ──stdio──► mcpguard stdio ──stdio──► real MCP server (local)
Claude Web ──HTTPS──► tunnel ──► mcpguard gateway ──HTTP(S)──► real MCP server (remote)
                                   │ (both adapters, per request, loopback TCP + token)
                                   ▼
                        mcpguard daemon: Broker → Policy → Popup UI → Decision
                                                   └──► audit.jsonl
```

## 2. Design decisions (ADR summary)
| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| 1 | Wrap launch command for Desktop | Desktop spawns local servers itself → deterministic, lossless interception | Sniffing pipes / `strace` / process injection (fragile, unsafe) |
| 2 | HTTP gateway for Web | Claude Web calls from the cloud; connector URL is the only controllable hop | TLS MITM / packet capture (cannot see cloud traffic; insecure) |
| 3 | One daemon owns all UI | One place for popups, single instance, central audit | Popup per proxy process (focus fights, no central log) |
| 4 | One TCP connection per request | Trivial concurrency, free cancellation on hang-up | Multiplexed protocol (more state, more bugs) |
| 5 | Fail closed | A security control must not silently open | Fail open (bypassable by killing daemon) |
| 6 | Denial as `result.isError` for tools/call | MCP-spec way; model can read and relay it | Protocol error (some clients show a generic failure) |
| 7 | stdlib only | Zero install friction, reviewable | aiohttp/PyQt (deps, packaging) |
| 8 | Tk on main thread, asyncio broker in worker thread, `queue.Queue` bridge | Tk is not thread-safe | Tk inside asyncio (blocking/loop conflicts) |
| 9 | Raw `os.read(0)` for stdin | Avoids shutdown abort on buffered-stdin lock | `sys.stdin.buffer` iteration (crashed in testing) |

## 3. Components
| Component | Module | Threading |
|---|---|---|
| Stdio proxy | `stdio_proxy.py` | asyncio loop + stdin reader thread |
| HTTP gateway | `http_gateway.py` | `ThreadingHTTPServer`, thread per request |
| Approval client | `client.py` | async (proxy) and sync with `select` (gateway) |
| Broker | `broker.py` | asyncio in worker thread |
| Popup UI | `ui_tk.py` | Tk main thread, `queue.Queue` bridge |
| Policy / Audit / Config / Models | `policy.py` `audit.py` `config.py` `models.py` | pure / lock-protected |

## 4. Wire protocol (client ↔ daemon, newline-delimited JSON)
Request: `{"token","op":"approve","source","server","method","tool","arguments","timeout"}`
Response: `{"decision":"allow|block","reason","by":"user|timeout|policy|error|disconnect|headless"}`
`op:"ping"` → `{"ok":true}`. Bad/missing token → `{"error":"unauthorized"}`.

## 5. Sequence: guarded request
```
Claude ─tools/call id=42─► adapter ──(park task)
adapter ─approve────────► broker ─policy?─► (match → decision)
                                   └──────► UI popup ─user─► Allow/Block/timeout
adapter ◄─decision──────── broker  (+ audit)
Allow: adapter ─original bytes─► server ─result─► Claude
Block: adapter ─isError result (id=42)─► Claude   (server never contacted)
```

## 6. State machine of a request
`RECEIVED → PENDING → {ALLOWED → FORWARDED | BLOCKED | TIMED_OUT(=BLOCKED) | CANCELLED}`
Cancellation sources: `notifications/cancelled`, HTTP disconnect, proxy shutdown.

## 7. Failure matrix
| Failure | Behaviour |
|---|---|
| Daemon not running | adapter auto-starts it; if that fails → fail-closed denial |
| Daemon crashes mid-popup | adapter sees EOF → fail-closed denial |
| Token missing/wrong | broker rejects → fail-closed |
| Client cancels / disconnects | popup dismissed, request dropped, audited as `disconnect` |
| MCP server exits | proxy exits with same code, pending cancelled |
| Claude closes pipe | cancel pending, close server stdin, terminate → kill |
| Upstream HTTP error | 502 + JSON-RPC error |
| Invalid config.json | defaults + logged error |
| Oversized args | truncated for wire/display, flagged |
| No display / no tkinter | daemon exits with clear message (use `--headless` for tests) |

## 8. Threat model
Protects against: a model or MCP server invoking tools you didn't intend; prompt-injection-driven tool calls; spoofed popup content; unauthenticated local clients; open public tunnels (secret path).
Does **not** protect against: malware running as your OS user (can read token/edit config); parser-differential tricks between Python's JSON parser and a server's (mitigated: allowed requests are forwarded byte-identical, unparseable lines are not treated as guarded but cannot be understood by the server either).

## 9. Extension points
New adapter (e.g. WebSocket/SSE-legacy) = implement "detect → `build_payload` → `decide_*` → forward/deny". New UI = implement `submit/cancel/run/stop`.
