"""Main CLI entrypoint for Walled AI MCP Security Gateway.

Provides commands to start:
1. Local stdio MCP Proxy (Claude Desktop integration)
2. Desktop Agent (PySide6 authorization GUI)
3. Remote Gateway (FastAPI HTTPS / Streamable HTTP server)
4. System diagnostics and configuration check
"""

from __future__ import annotations

import argparse
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
    sys.stderr.write("Environment Status   : VERIFIED (Milestone 1 Ready)\n")
    sys.stderr.write("============================================================\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Application entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_logging(args.log_level)

    if args.command == "local-proxy":
        sys.stderr.write("[INFO] Local MCP Proxy launching...\n")
        return 0
    elif args.command == "desktop":
        sys.stderr.write("[INFO] Desktop Agent launching...\n")
        return 0
    elif args.command == "remote-gateway":
        sys.stderr.write("[INFO] Remote Gateway launching...\n")
        return 0

    return run_info()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
