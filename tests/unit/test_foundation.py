"""Comprehensive unit test suite for Milestone 1: Environment, Configuration & Foundation.

Strictly verifies:
- Python 3.12 compatibility
- Settings schema, bounds, and environment defaults
- Strict request state machine transitions (PENDING -> APPROVED, REJECTED, TIMED_OUT, CANCELLED)
- Immutability of terminal states (no transition allowed from any terminal state)
- Recursive sensitive parameter and token redaction
- Domain model integrity (AuthorizationRequest, AuthorizationDecision, AuditRecord)
- Application entrypoint, CLI arguments parsing, and startup behavior
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.config import Settings, configure_logging, get_logger
from app.main import build_parser, main
from app.models.requests import (
    TERMINAL_STATUSES,
    AuditRecord,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    InvalidStateTransitionError,
    redact_sensitive_data,
)


def test_python_version_is_312() -> None:
    """Verify runtime environment is strictly Python 3.12."""
    assert sys.version_info.major == 3, f"Expected Python 3, got {sys.version_info.major}"
    assert sys.version_info.minor == 12, (
        f"Expected Python 3.12, got {sys.version_info.major}.{sys.version_info.minor}"
    )


def test_settings_validation_and_bounds() -> None:
    """Verify configuration schema, validation bounds, and normalization."""
    # Valid defaults
    settings = Settings()
    assert settings.auth_timeout_seconds == 30.0
    assert settings.remote_port == 8765
    assert isinstance(settings.sensitive_keys_list, list)
    assert "password" in settings.sensitive_keys_list
    assert "token" in settings.sensitive_keys_list
    assert "secret" in settings.sensitive_keys_list

    # Ensure duplicate or mixed-case keys in redact_keys are normalized
    custom_settings = Settings(redact_keys=" Password , token, PASSWORD, Secret_Key ")
    assert custom_settings.sensitive_keys_list == ["password", "secret_key", "token"]

    # Bounds validation: timeout must be > 0
    with pytest.raises(ValidationError):
        Settings(auth_timeout_seconds=0.0)

    with pytest.raises(ValidationError):
        Settings(auth_timeout_seconds=-10.0)

    # Bounds validation: remote_port must be 1..65535
    with pytest.raises(ValidationError):
        Settings(remote_port=0)

    with pytest.raises(ValidationError):
        Settings(remote_port=70000)


def test_settings_db_path_creates_directory(tmp_path: Path) -> None:
    """Verify db_path property automatically creates parent directory if needed."""
    nested_db = tmp_path / "nested" / "subfolder" / "audit.db"
    assert not nested_db.parent.exists()

    settings = Settings(database_path=str(nested_db))
    resolved_path = settings.db_path
    assert resolved_path.name == "audit.db"
    assert resolved_path.parent.exists()


def test_logging_configuration_strictly_stderr() -> None:
    """Verify logging config writes exclusively to sys.stderr, never sys.stdout."""
    configure_logging("DEBUG")
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) == 1
    handler = root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr

    logger = get_logger("test.audit")
    assert logger.name == "test.audit"


def test_redact_sensitive_data_nested_structures() -> None:
    """Verify recursive redaction masks sensitive keys and preserves others across all types."""
    original_payload: Dict[str, Any] = {
        "id": 101,
        "username": "admin_user",
        "api_key": "sk-proj-super-secret-12345",
        "auth_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz",
        "credentials": {
            "password": "ClearTextPassword123!",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...",
            "safe_config": {"retries": 3, "timeout": 5.0},
        },
        "items": [
            {"secret_code": 9999, "label": "classified"},
            {"public_id": "item_1"},
        ],
        "tuple_data": ({"token": "secret_in_tuple"}, "regular_tuple_string"),
        "set_data": {"safe_set_entry"},
        "bearer_in_value": "Bearer dGVzdF90b2tlbjEyMzQ1",
        "raw_secret_list": ["secret1", "secret2"],
    }

    redacted = redact_sensitive_data(original_payload)

    # Assert masked fields
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["auth_token"] == "[REDACTED]"
    assert redacted["credentials"]["password"] == "[REDACTED]"
    assert redacted["credentials"]["private_key"] == "[REDACTED]"
    assert redacted["items"][0]["secret_code"] == "[REDACTED]"
    assert redacted["tuple_data"][0]["token"] == "[REDACTED]"
    assert redacted["bearer_in_value"] == "[REDACTED]"

    # Assert preserved non-sensitive fields
    assert redacted["id"] == 101
    assert redacted["username"] == "admin_user"
    assert redacted["credentials"]["safe_config"]["retries"] == 3
    assert redacted["items"][1]["public_id"] == "item_1"
    assert redacted["tuple_data"][1] == "regular_tuple_string"
    assert "safe_set_entry" in redacted["set_data"]

    # Verify original payload remains untouched
    assert original_payload["api_key"] == "sk-proj-super-secret-12345"
    assert original_payload["credentials"]["password"] == "ClearTextPassword123!"


def test_redact_standalone_sensitive_strings() -> None:
    """Verify raw standalone strings matching bearer tokens are masked."""
    token_str = "Bearer secret-token-xyz"
    assert redact_sensitive_data(token_str) == "[REDACTED]"

    safe_str = "Normal documentation string"
    assert redact_sensitive_data(safe_str) == "Normal documentation string"

    # List of standalone bearer strings
    token_list = ["Bearer a1b2c3d4", "Safe Item"]
    assert redact_sensitive_data(token_list) == ["[REDACTED]", "Safe Item"]


def test_authorization_request_validation() -> None:
    """Verify AuthorizationRequest schema validation and default attributes."""
    req = AuthorizationRequest(
        server_name="filesystem-server",
        tool_name="read_file",
        arguments={"path": "/safe/path.txt"},
    )
    assert len(req.request_id) > 0
    assert req.server_name == "filesystem-server"
    assert req.tool_name == "read_file"
    assert req.status == AuthorizationStatus.PENDING
    assert not req.is_terminal()
    assert req.decision_at is None
    assert req.reason is None

    # Blank server_name or tool_name must be rejected
    with pytest.raises(ValidationError):
        AuthorizationRequest(server_name="", tool_name="read_file")

    with pytest.raises(ValidationError):
        AuthorizationRequest(server_name="valid", tool_name="")


@pytest.mark.parametrize(
    "target_status",
    [
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    ],
)
def test_all_valid_state_transitions(target_status: AuthorizationStatus) -> None:
    """Verify PENDING can transition to all terminal states."""
    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 1, "b": 2},
    )
    assert req.can_transition_to(target_status)
    req.transition_to(target_status, reason=f"Transitioned to {target_status.value}")
    assert req.status == target_status
    assert req.is_terminal()
    assert req.decision_at is not None
    assert req.reason == f"Transitioned to {target_status.value}"


@pytest.mark.parametrize("terminal_state", list(TERMINAL_STATUSES))
@pytest.mark.parametrize(
    "attempted_next_state",
    [
        AuthorizationStatus.PENDING,
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    ],
)
def test_terminal_states_are_immutable(
    terminal_state: AuthorizationStatus, attempted_next_state: AuthorizationStatus
) -> None:
    """Verify a request in any terminal state can never transition to any other state."""
    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={},
    )
    req.transition_to(terminal_state, reason="Initial terminal transition")
    assert req.status == terminal_state
    assert req.is_terminal()
    assert not req.can_transition_to(attempted_next_state)

    with pytest.raises(InvalidStateTransitionError):
        req.transition_to(attempted_next_state)


def test_authorization_decision_validation() -> None:
    """Verify AuthorizationDecision enforces valid terminal user choices."""
    d_allow = AuthorizationDecision(
        request_id="req-123",
        decision=AuthorizationStatus.APPROVED,
        reason="User clicked Allow",
    )
    assert d_allow.decision == AuthorizationStatus.APPROVED

    d_block = AuthorizationDecision(
        request_id="req-124",
        decision=AuthorizationStatus.REJECTED,
        reason="Suspicious arguments",
    )
    assert d_block.decision == AuthorizationStatus.REJECTED

    # Decisions other than APPROVED/REJECTED must fail validation
    with pytest.raises(ValidationError):
        AuthorizationDecision(request_id="req-125", decision=AuthorizationStatus.PENDING)

    with pytest.raises(ValidationError):
        AuthorizationDecision(request_id="req-126", decision=AuthorizationStatus.TIMED_OUT)

    with pytest.raises(ValidationError):
        AuthorizationDecision(request_id="req-127", decision=AuthorizationStatus.CANCELLED)

    # Empty request_id must fail validation
    with pytest.raises(ValidationError):
        AuthorizationDecision(request_id="", decision=AuthorizationStatus.APPROVED)


def test_audit_record_from_request_factory() -> None:
    """Verify AuditRecord.from_request correctly captures and redacts data."""
    req = AuthorizationRequest(
        server_name="database-server",
        tool_name="execute_query",
        arguments={
            "query": "SELECT * FROM users",
            "password": "db_secret_password",
        },
    )

    # Cannot create audit record from a non-terminal request
    with pytest.raises(ValueError, match="is still in state PENDING"):
        AuditRecord.from_request(req)

    # Transition to APPROVED
    req.transition_to(AuthorizationStatus.APPROVED, reason="Approved by admin")

    audit = AuditRecord.from_request(
        req,
        execution_outcome="SUCCESS",
        failure_reason=None,
    )

    assert audit.request_id == req.request_id
    assert audit.server_name == "database-server"
    assert audit.tool_name == "execute_query"
    assert audit.decision == AuthorizationStatus.APPROVED
    assert audit.execution_outcome == "SUCCESS"
    assert audit.failure_reason == "Approved by admin"
    # Arguments in audit record must be redacted
    assert audit.arguments_redacted["query"] == "SELECT * FROM users"
    assert audit.arguments_redacted["password"] == "[REDACTED]"
    # Original arguments on request object must remain untouched
    assert req.arguments["password"] == "db_secret_password"


def test_audit_record_rejects_pending_decision() -> None:
    """Verify AuditRecord constructor rejects non-terminal statuses."""
    with pytest.raises(ValidationError):
        AuditRecord(
            request_id="req-1",
            server_name="server",
            tool_name="tool",
            timestamp=datetime.now(timezone.utc),
            decision=AuthorizationStatus.PENDING,
        )


def test_main_cli_modes() -> None:
    """Verify all subcommands and CLI options execute cleanly."""
    parser = build_parser()

    # Help
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0

    # Local proxy args
    args_proxy = parser.parse_args(
        [
            "local-proxy",
            "--upstream-cmd",
            "python",
            "--upstream-args",
            "mock.py",
            "--server-name",
            "ProxyTest",
        ]
    )
    assert args_proxy.command == "local-proxy"
    assert args_proxy.server_name == "ProxyTest"

    # Desktop args
    args_desktop = parser.parse_args(["desktop", "--remote-url", "ws://localhost:8765/ws"])
    assert args_desktop.command == "desktop"
    assert args_desktop.remote_url == "ws://localhost:8765/ws"

    # Remote gateway args
    args_remote = parser.parse_args(["remote-gateway", "--host", "0.0.0.0", "--port", "9000"])
    assert args_remote.command == "remote-gateway"
    assert args_remote.port == 9000

    # Test main function dispatch with different arguments
    assert main([]) == 0
    assert main(["info"]) == 0
    assert main(["local-proxy"]) == 0
    with patch("app.desktop.application.run_desktop", return_value=0):
        assert main(["desktop"]) == 0
    with patch("uvicorn.run", return_value=None):
        assert main(["remote-gateway"]) == 0
