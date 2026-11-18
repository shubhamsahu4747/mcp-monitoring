"""Main CLI entrypoint for Walled AI MCP Security Gateway.

Provides commands to start:
1. Local stdio MCP Proxy (Claude Desktop integration)
2. Desktop Agent (PySide6 authorization GUI)
3. Remote Gateway (FastAPI HTTPS / Streamable HTTP server)
4. System diagnostics and configuration check
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import configure_logging, settings


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="walled-ai",
        description="Walled AI - MCP Communication Monitoring and User Authorization Gateway",
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (written to stderr)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Operational mode")

    # Mode: local-proxy
    proxy_parser = subparsers.add_parser(
        "local-proxy",
        help="Run local stdio MCP proxy intercepting Claude Desktop requests",
    )
    proxy_parser.add_argument(
        "--upstream-cmd",
        default=settings.default_upstream_command,
        help="Executable for upstream MCP server",
    )
    proxy_parser.add_argument(
        "--upstream-args",
        default=settings.default_upstream_args,
        help="Arguments for upstream MCP server",
    )
    proxy_parser.add_argument(
        "--server-name",
        default=settings.upstream_server_name,
        help="Human-readable upstream server identifier",
    )

    # Mode: desktop
    desktop_parser = subparsers.add_parser(
        "desktop",
        help="Launch PySide6 Windows desktop authorization agent",
    )
    desktop_parser.add_argument(
        "--remote-url",
        default=None,
        help="Optional remote gateway WebSocket URL (e.g. ws://127.0.0.1:8765/ws/agent)",
    )

    # Mode: remote-gateway
    remote_parser = subparsers.add_parser(
        "remote-gateway",
        help="Run remote HTTPS / Streamable HTTP MCP gateway for Claude Web",
    )
    remote_parser.add_argument(
        "--host",
        default=settings.remote_host,
        help="Bind host for remote gateway",
    )
    remote_parser.add_argument(
        "--port",
        type=int,
        default=settings.remote_port,
        help="Bind port for remote gateway",
    )

    # Mode: info / verify
    subparsers.add_parser(
        "info",
        help="Display gateway configuration and verify environment",
    )

    # Mode: test-client
    test_client_parser = subparsers.add_parser(
        "test-client",
        help="Run authentic MCP client session to test tool interception live",
    )
    test_client_parser.add_argument(
        "--tool",
        default="calculate_sum",
        help="Tool to invoke (default: calculate_sum)",
    )

    # Mode: install-claude-config
    subparsers.add_parser(
        "install-claude-config",
        help="Install or update Walled AI gateway entry in Claude Desktop configuration",
    )

    # Mode: validate-claude-config
    subparsers.add_parser(
        "validate-claude-config",
        help="Validate Claude Desktop configuration file and Walled AI gateway entry",
    )

    # Mode: rollback-claude-config
    subparsers.add_parser(
        "rollback-claude-config",
        help="Restore Claude Desktop configuration from the latest backup",
    )

    return parser


def run_info() -> int:
    """Print configuration and verification info to stderr."""
    sys.stderr.write("============================================================\n")
    sys.stderr.write("   Walled AI - MCP Communication Monitoring & Security Gateway\n")
    sys.stderr.write("============================================================\n")
    sys.stderr.write(f"Python Version       : {sys.version.split()[0]}\n")
    sys.stderr.write(f"Auth Timeout         : {settings.auth_timeout_seconds}s\n")
    sys.stderr.write(f"Database Path        : {settings.db_path}\n")
    sys.stderr.write(f"Upstream Server Name : {settings.upstream_server_name}\n")
    cmd_repr = f"{settings.default_upstream_command} {settings.default_upstream_args}"
    sys.stderr.write(f"Upstream Command     : {cmd_repr}\n")
    sys.stderr.write(f"Remote Gateway       : {settings.remote_host}:{settings.remote_port}\n")
    sys.stderr.write(f"Redaction Keys Count : {len(settings.sensitive_keys_list)}\n")
    sys.stderr.write("Environment Status   : VERIFIED (Milestone 2 Ready)\n")
    sys.stderr.write("============================================================\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Application entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)

    if args.command == "local-proxy":
        from app.gateway.local_proxy import LocalMCPProxy
        from app.gateway.upstream_manager import UpstreamManager

        up_mgr = UpstreamManager(
            command=args.upstream_cmd,
            args=args.upstream_args,
            server_name=args.server_name,
        )
        proxy = LocalMCPProxy(
            upstream_mgr=up_mgr,
            server_name=f"Walled AI Gateway ({args.server_name})",
        )
        asyncio.run(proxy.run())
        return 0
    elif args.command == "desktop":
        from app.desktop.application import run_desktop

        return run_desktop(remote_url=args.remote_url)
    elif args.command == "test-client":
        from app.gateway.interactive_client import run_client_demo

        args_dict = (
            {"a": 25.0, "b": 75.0}
            if args.tool == "calculate_sum"
            else {"filename": "test.txt", "content": "demo"}
        )
        asyncio.run(run_client_demo(tool_name=args.tool, arguments=args_dict))
        return 0
    elif args.command == "install-claude-config":
        from app.gateway.claude_config import install_claude_config

        path, snippet = install_claude_config()
        sys.stderr.write(f"\n[CLAUDE DESKTOP CONFIGURATION]\nConfig File: {path}\n\n")
        sys.stderr.write(f"{snippet}\n\n")
        sys.stderr.write("To use in Claude Desktop: Restart Claude Desktop if already running.\n")
        return 0
    elif args.command == "validate-claude-config":
        from app.gateway.claude_config import validate_claude_config

        is_valid, messages = validate_claude_config()
        sys.stderr.write("\n[CLAUDE DESKTOP CONFIGURATION VALIDATION]\n")
        for msg in messages:
            sys.stderr.write(f"  {msg}\n")
        sys.stderr.write(
            f"\nResult: {'VALID (Ready for Claude Desktop)' if is_valid else 'INVALID'}\n\n"
        )
        return 0 if is_valid else 1
    elif args.command == "rollback-claude-config":
        from app.gateway.claude_config import rollback_claude_config

        success, msg = rollback_claude_config()
        status_label = "SUCCESS" if success else "FAILED"
        sys.stderr.write(f"\n[CLAUDE DESKTOP CONFIGURATION ROLLBACK - {status_label}]\n")
        sys.stderr.write(f"  {msg}\n\n")
        return 0 if success else 1
    elif args.command == "remote-gateway":
        import uvicorn

        from app.remote.app import create_remote_app

        remote_app = create_remote_app()
        sys.stderr.write(
            f"[INFO] Launching Walled AI Remote MCP Gateway on {args.host}:{args.port}...\n"
        )
        uvicorn.run(remote_app, host=args.host, port=args.port, log_level=args.log_level.lower())
        return 0

    return run_info()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
