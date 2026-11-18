# requirements.md: MCP Communication Monitoring & User Authorization System

**Project:** `mcpguard` · **Language:** Python ≥ 3.10 · **Deadline:** Fri 20 Nov 2026, EOD IST
**Source:** Walled AI candidate assignment. Every line of the brief is mapped below to an ID, an acceptance criterion (AC), the implementing module, and the verifying test.

Legend: ✅ implemented + automated test · 🟡 implemented, needs manual verification on a real desktop · 📝 documentation/deliverable

---

## 1. Objective
A Python application that monitors MCP communication initiated by **Claude Desktop** and **Claude Web** and lets the user **Allow** or **Block** each individual MCP request before it executes.

## 2. Key constraint (state this in README and demo)
| Client | Real transport | Interception point |
|---|---|---|
| Claude Desktop, local servers | child process over stdio | launch command → stdio proxy |
| Claude Web (and Desktop remote connectors) | Anthropic **cloud** → connector URL | connector URL → HTTP gateway |

Claude Web traffic originates in Anthropic's cloud and cannot be observed by a local process; the gateway is the only controllable hop.

---

## 3. Functional requirements

### FR-1 MCP communication monitoring
| ID | Requirement | Acceptance criteria | Implementation | Verified by | Status |
|---|---|---|---|---|---|
| FR-1.1 | Monitor outgoing MCP requests from **Claude Desktop** | A local server configured in `claude_desktop_config.json` is launched through the proxy; every client→server JSON-RPC line is inspected | `stdio_proxy.py`, `install.py` | `test_stdio_e2e.*`, `test_install_roundtrip` | ✅ / 🟡 real Desktop |
| FR-1.2 | Monitor outgoing MCP requests from **Claude Web** | Gateway URL registered as custom connector; every POST body is inspected | `http_gateway.py` | `test_gateway_e2e.*` | ✅ / 🟡 real Web + tunnel |
| FR-1.3 | Detect a **new MCP communication / tool invocation** | A message with `method ∈ guarded_methods` **and** an `id` is classed as guarded; notifications, responses, `tools/list`, `initialize` are not | `protocol.is_guarded` | `test_guarded_detection`, `test_unguarded_methods_pass_without_approval` | ✅ |
| FR-1.4 | Capture **MCP server, tool name, parameters** | Popup + audit show server name, method, tool/URI, pretty-printed arguments | `protocol.describe`, `models.PendingRequest` | `test_describe`, audit assertions in `test_allow_executes…` | ✅ |

### FR-2 User confirmation mechanism
| ID | Requirement | Acceptance criteria | Implementation | Verified by | Status |
|---|---|---|---|---|---|
| FR-2.1 | Popup **on top of other active applications** | Window has `-topmost`, re-asserted every 1.5 s, focused | `ui_tk.py` | manual (M-1) | 🟡 |
| FR-2.2 | Popup shows info needed for an informed decision | Shows source, server, method, tool, request id, scrollable parameters, truncation notice, countdown | `ui_tk.py` | manual (M-1) | 🟡 |
| FR-2.3 | Exactly two options: **Allow** / **Block** | Two buttons; Esc and window-close = Block; Block is default focus; Allow enabled after 800 ms | `ui_tk.py` | manual (M-1, M-2) | 🟡 |
| FR-2.4 | Display cannot be spoofed by request content | Control/bidi chars stripped; length-limited | `models.sanitize` | `test_sanitize_strips_bidi_and_controls` | ✅ |

### FR-3 Request handling
| ID | Requirement | Acceptance criteria | Implementation | Verified by | Status |
|---|---|---|---|---|---|
| FR-3.1 | **Keep request pending** until decision | Guarded request is not written to the server/upstream until a decision arrives | `StdioProxy._guard`, `_Handler._check_approvals` | `test_cancel_notification…` (server never executes while pending) | ✅ |
| FR-3.2 | **Allow → forward** | Original bytes reach the server; real result returns to Claude | same | `test_allow_executes_and_passthrough_untouched`, `test_allow_forwards_json_and_sse` | ✅ |
| FR-3.3 | **Block → prevent + denial response** | Server never sees it; Claude gets `isError:true` result (tools/call) or JSON-RPC error `-32001` | `protocol.denial_response` | `test_block_never_reaches_server`, `test_block_returns_denial_and_upstream_untouched` | ✅ |
| FR-3.4 | **Multiple simultaneous requests** | 20 concurrent requests all answered with correct id↔result mapping; approvals overlap (not serialised); one window each | asyncio task / thread per request, one TCP conn per request | `test_many_concurrent_requests…`, `test_concurrent_requests` | ✅ |

### FR-4 Application reliability
| ID | Requirement | Acceptance criteria | Implementation | Verified by | Status |
|---|---|---|---|---|---|
| FR-4.1 | Runs **in the background** | `mcpguard daemon` is a long-lived service; proxies auto-start it detached; autostart-at-login documented | `daemon.py`, `client.spawn_daemon` | `status` cmd; manual (M-4) | ✅ / 🟡 |
| FR-4.2 | Handles **application termination** | Server exit → proxy exits with its code; client EOF → pending popups cancelled, server stdin closed, terminate→kill; no orphans | `StdioProxy.run/_stop_child` | `test_server_exit_terminates_proxy` | ✅ |
| FR-4.3 | Handles **communication failures** | Daemon unreachable → fail-closed denial with explanation; upstream down → HTTP 502 JSON-RPC error; requester hang-up dismisses popup | `client.py`, `broker.py` | `test_fails_closed_*` (×2), `test_upstream_down_gives_502` | ✅ |
| FR-4.4 | Handles **unexpected errors** | Bad client message never crashes daemon; bad command → exit 127; invalid config → defaults; audit-write failure ignored; unparseable JSON passed through | `broker._handle`, `config.load`, `audit.py` | `test_bad_server_command_reports_error` | ✅ |
| FR-4.5 | Undecided requests don't hang forever | Auto-block after `timeout_seconds` | `ui_tk.tick`, `broker._decide` | unit/manual (M-3) | 🟡 |

---

## 4. Technical requirements
| ID | Requirement | How it is met | Status |
|---|---|---|---|
| TR-1 | Language: Python | Python ≥ 3.10, stdlib only (tkinter for UI) | ✅ |
| TR-2 | Appropriate architecture for monitoring/intercepting MCP | Two interception adapters (stdio proxy, HTTP gateway) + shared approval daemon; see `architecture.md` | ✅ |
| TR-3 | Modular, maintainable, documented | 15 single-purpose modules, type hints, module docstrings, `AGENTS.md` conventions | ✅ |
| TR-4 | Install / configure / run instructions | README §2–§6 | 📝 |

## 5. Non-functional requirements
| ID | Requirement | Target |
|---|---|---|
| NFR-1 Security | Fail-closed; loopback-only daemon; 256-bit token (0600); decisions only via UI/static policy; secret-path gateway; sanitised UI | all ✅ |
| NFR-2 Latency | Un-guarded messages add < 5 ms; guarded add only human decision time | by design |
| NFR-3 Portability | macOS, Windows, Linux; no native deps | ✅ (Windows `npx.cmd` resolved via `shutil.which`) |
| NFR-4 Observability | JSONL audit of every decision with latency; logs to stderr only | ✅ |
| NFR-5 Reversibility | `uninstall` restores config exactly; timestamped backup; atomic write | ✅ `test_install_roundtrip` |
| NFR-6 Zero dependencies | `dependencies = []` | ✅ |

## 6. Deliverables (all on GitHub)
| ID | Deliverable | Location | Status |
|---|---|---|---|
| D-1 | Complete Python source | `src/mcpguard/` | ✅ |
| D-2 | README with setup & execution | `README.md` | 📝 ✅ |
| D-3 | Architecture & interception approach explanation | `README.md §1`, `docs/architecture.md` | 📝 ✅ |
| D-4 | Demo video: approval **and** blocking | link in README (record per `plan.md` Phase 6) | ⬜ to record |
| D-5 | Submission form: CV + repo link + video link | form | ⬜ |

## 7. Evaluation-criteria coverage
| Criterion | Where addressed |
|---|---|
| Correctness & reliability of interception | FR-1, FR-3; 22 tests incl. byte-for-byte pass-through |
| Compatibility with Claude Desktop **and** Web | stdio proxy + HTTP gateway; limitation explained |
| Effectiveness of confirmation mechanism | FR-2, popup safety features |
| Concurrency & edge cases | FR-3.4, FR-4, cancellation, batch, timeout |
| Code quality, architecture, docs | TR-3, `AGENTS.md`, this doc set |
| Security & reliability | NFR-1, threat model in `architecture.md §8` |

## 8. Out of scope / known limitations
Server→client requests (sampling, elicitation); guarding built-in first-party Claude integrations; Desktop `.mcpb` extensions (configure manually via config file); defending against malware running as the same OS user.

## 9. Manual verification checklist (needs a desktop; tkinter could not be run in the build sandbox)
- **M-1** Popup appears above a full-screen/maximised app with all fields populated.
- **M-2** Allow works after 800 ms; Esc / window close blocks; clicking Allow immediately after appearance does nothing.
- **M-3** Leaving the popup untouched auto-blocks at 0 s and Claude shows the denial.
- **M-4** Killing the daemon mid-session → next request auto-restarts it (or fails closed with message).
- **M-5** Real Claude Desktop: allow `get_time`, allow `send_email`, block `delete_file` (log shows no execution).
- **M-6** Real Claude Web via tunnel + `--secret`: same allow/block cycle.
