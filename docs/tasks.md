# tasks.md: Task Board

Status: `[x]` done · `[ ]` todo · IDs link to `requirements.md`.

## Done
- [x] T01 JSON-RPC/MCP helpers, guarded-method detection (FR-1.3)
- [x] T02 Config, token, state dir (TR-3, NFR-1)
- [x] T03 Policy engine with glob rules
- [x] T04 Audit log (NFR-4)
- [x] T05 Broker: auth, policy, UI hand-off, hang-up cancellation (FR-3, FR-4.3)
- [x] T06 Headless UI for tests
- [x] T07 Tk popup: topmost, cascade, countdown, safe defaults (FR-2) — *code only*
- [x] T08 Approval clients (async + sync), daemon autostart, fail-closed (FR-4)
- [x] T09 Stdio proxy: pending tasks, cancel, shutdown, concurrency (FR-1.1, FR-3, FR-4.2)
- [x] T10 HTTP gateway: JSON + SSE streaming, batch, secret path, 502 (FR-1.2)
- [x] T11 `install` / `uninstall` with backup + atomic write (NFR-5)
- [x] T12 CLI (`daemon stdio gateway install uninstall status log`)
- [x] T13 Test suite: 22 tests (protocol, stdio e2e, gateway e2e)
- [x] T14 README + architecture + requirements + plan docs (D-2, D-3)
- [x] T15 Demo MCP server + example configs

## Remaining (priority order)
- [ ] T16 **Verify Tk popup on real desktop**, checklist M-1…M-3 (FR-2.*, FR-4.5)
- [ ] T17 **Verify with real Claude Desktop**, M-5 (FR-1.1)
- [ ] T18 **Verify Claude Web through gateway + tunnel**, M-6 (FR-1.2)
- [ ] T19 Record demo video (D-4), script in `plan.md`
- [ ] T20 Create GitHub repo, push, add video link to README (D-1…D-4)
- [ ] T21 Submit form with CV + repo + video (D-5)

## Nice-to-have (only if time remains)
- [ ] N1 "Allow for this session" checkbox (still exactly two buttons by default)
- [ ] N2 System-tray icon with pending count (pystray optional extra)
- [ ] N3 Guard server→client requests (sampling/elicitation)
- [ ] N4 Windows/macOS foreground helpers (`SetForegroundWindow`, `osascript`)
- [ ] N5 GitHub Actions: run unittest on Linux/macOS/Windows

## Definition of done
All `requirements.md` rows ✅; M-1…M-6 ticked; tests green on clean clone; video shows both an approval and a block; submitted before deadline.
