# AGENTS.md: instructions for AI coding agents (Antigravity, Claude Code, etc.)

Read in this order before changing anything: `docs/requirements.md` → `docs/architecture.md` → `docs/plan.md` → `docs/tasks.md`.

## Project
`mcpguard`: per-request Allow/Block popup for MCP traffic from Claude Desktop (stdio proxy) and Claude Web (HTTP gateway). Python ≥ 3.10, **standard library only**.

## Hard rules
1. **Never write anything but MCP protocol bytes to stdout** in `stdio_proxy.py`. Logs go to stderr.
2. **Fail closed.** Any error on the approval path must result in a block unless `fail_mode == "allow"`.
3. Forward allowed requests **byte-identical**. Never re-serialise an allowed message (exception: legacy batches).
4. Only the popup UI or static policy may produce an `allow`. Never add a code path where request content can influence the decision.
5. Sanitise every string shown in the UI (`models.sanitize`).
6. Keep the daemon bound to `127.0.0.1` and token-authenticated.
7. No new third-party dependencies without asking. Tk must stay on the main thread; talk to it only through `ui_tk.TkUI` queue methods.
8. Do not break the 22 tests. New behaviour needs a test in `tests/` (use `helpers.Env` + headless daemon).

## Commands
```
pip install -e .
python -m unittest discover -s tests -v
python -m mcpguard daemon [--headless allow|block]
python -m mcpguard stdio --name X -- <cmd> [args]
python -m mcpguard gateway --upstream URL --secret S
python -m mcpguard install --dry-run | install | uninstall
```

## Style
Type hints, module docstrings that explain *why*, small functions, no globals, `logging` not `print` (except CLI output).

## Current focus
See `docs/tasks.md` → "Remaining": manual verification on a real desktop, demo video, submission.
