# plan.md: Implementation Plan (Antigravity)

**Goal:** ship a GitHub repo satisfying every item in `requirements.md` by **Fri 20 Nov 2026 EOD IST**.
**Today:** Wed 18 Nov 2026 → 2 days left. Phases 0–5 are code-complete; remaining work is verification, recording and submission.

## Phase map
| # | Phase | Output | Status |
|---|---|---|---|
| 0 | Research & constraints | Desktop = stdio child process; Web = cloud → URL ⇒ two adapters | ✅ |
| 1 | Core protocol & models | `protocol.py`, `models.py`, `config.py`, `policy.py`, `audit.py` | ✅ |
| 2 | Approval daemon | `broker.py`, `daemon.py`, `client.py`, `ui_headless.py` | ✅ |
| 3 | Popup UI | `ui_tk.py` | ✅ code · 🟡 verify |
| 4 | Interception adapters | `stdio_proxy.py`, `http_gateway.py`, `install.py`, `cli.py` | ✅ |
| 5 | Tests & hardening | 22 stdlib tests green | ✅ |
| 6 | **Manual verification on real desktop** | checklist M-1…M-6 ticked | ⬜ **Day 1** |
| 7 | **Demo video** | 3–5 min recording, linked in README | ⬜ **Day 1–2** |
| 8 | **Repo polish & submission** | public repo, README badges/link, form submitted | ⬜ **Day 2 (before noon IST)** |

## Phase 6: manual verification (≈2 h)
1. `pip install -e .`; ensure `python -c "import tkinter"` works.
2. `python -m mcpguard daemon` in a terminal; confirm `status`.
3. Add `examples/demo_server.py` to Claude Desktop (see example config); restart Desktop.
4. Run checklist M-1…M-5 (`requirements.md §9`); fix any focus/topmost issue (macOS: first click may only activate window → acceptable, document).
5. Claude Web: run gateway with `--secret`, expose with cloudflared, add custom connector, run M-6.
6. Note any defects in `tasks.md`, fix, re-run `python -m unittest discover -s tests`.

## Phase 7: demo video script (target ≤ 5 min, screen + voice)
| t | Scene |
|---|---|
| 0:00 | 20 s: problem + architecture slide (two adapters; why Web needs a gateway) |
| 0:20 | Show `claude_desktop_config.json` wrapped entry (`mcpguard install`) and running daemon |
| 0:50 | Prompt "What time is it?" → popup above other apps → **Allow** → answer in Claude |
| 1:40 | Prompt "Send an email to … " → popup shows parameters → **Allow** → show `demo_server_actions.log` line |
| 2:30 | Prompt "Delete /tmp/x" → popup → **Block** → Claude explains denial → log has **no** new line |
| 3:10 | Ask for 3 things at once → 3 cascaded popups, decide independently |
| 3:50 | Claude Web via tunnel: one allow, one block |
| 4:30 | `python -m mcpguard log` audit trail; run test suite (22 passed); close |

## Phase 8: repo & submission checklist
- [ ] Public GitHub repo; push `src/`, `tests/`, `examples/`, `docs/`, `README.md`, `pyproject.toml`, `AGENTS.md`
- [ ] README: paste video link at top; add "Limitations" section (already present)
- [ ] `python -m unittest discover -s tests` green on a clean clone
- [ ] No secrets committed (`~/.mcpguard` is outside repo)
- [ ] Submit form: CV + repo URL + video URL **before Fri 20 Nov 2026 EOD IST** (aim for Thu night)

## Risks & mitigations
| Risk | Likelihood | Mitigation |
|---|---|---|
| Popup not foreground on macOS/Windows | Medium | topmost re-assert loop; document first-click; fallback `osascript`/`SetForegroundWindow` tweak |
| Claude Web connector rejects gateway (needs OAuth/HTTPS details) | Medium | secret-path URL over HTTPS tunnel; use authless upstream for demo; record Desktop flow as primary |
| Tool timeout shorter than approval time | Low | default 60 s; document; lower `timeout_seconds` |
| Tunnel instability on recording | Medium | record Web segment separately; fall back to curl-driven gateway demo |
| Reviewer expects "Web interception" locally | Medium | explain cloud architecture clearly in README + video intro |
