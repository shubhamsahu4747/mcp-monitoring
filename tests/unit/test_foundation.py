"""Unit tests for Milestone 1: Environment, Configuration, and Foundation Models."""

import sys

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import build_parser, run_info
from app.models.requests import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationStatus,
    InvalidStateTransitionError,
    redact_sensitive_data,
)


def test_python_version_is_312() -> None:
    """Requirement: Python 3.12 explicitly."""
    assert sys.version_info.major == 3
    assert sys.version_info.minor == 12, f"Expected Python 3.12, got {sys.version}"


def test_settings_defaults() -> None:
    """Verify settings defaults and helper properties."""
    settings = Settings()
    assert settings.auth_timeout_seconds == 30.0
    assert "password" in settings.sensitive_keys_list
    assert "token" in settings.sensitive_keys_list
    assert settings.remote_port == 8765
    assert settings.db_path.name == "audit.db"


def test_redact_sensitive_data_nested() -> None:
    """Verify recursive redaction masks sensitive keys and preserves others."""
    payload = {
        "user_id": 12345,
        "token": "secret-jwt-token-123",
        "nested": {
            "api_key": "sk-secret-live-xyz",
            "safe_arg": "normal_value",
            "inner_list": [
                {"password": "super-secret-password"},
                {"regular_data": True},
            ],
        },
    }
    redacted = redact_sensitive_data(payload)
    assert redacted["user_id"] == 12345
    assert redacted["token"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["safe_arg"] == "normal_value"
    assert redacted["nested"]["inner_list"][0]["password"] == "[REDACTED]"
    assert redacted["nested"]["inner_list"][1]["regular_data"] is True
    # Ensure original payload was NOT modified
    assert payload["token"] == "secret-jwt-token-123"


def test_authorization_request_initialization() -> None:
    """Verify AuthorizationRequest creates unique ID and PENDING status."""
    req1 = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 5, "b": 10},
    )
    req2 = AuthorizationRequest(
        server_name="test-server",
        tool_name="calculate_sum",
        arguments={"a": 5, "b": 10},
    )
    assert req1.request_id != req2.request_id
    assert req1.status == AuthorizationStatus.PENDING
    assert not req1.is_terminal()
    assert req1.can_transition_to(AuthorizationStatus.APPROVED)
    assert req1.can_transition_to(AuthorizationStatus.REJECTED)
    assert req1.can_transition_to(AuthorizationStatus.TIMED_OUT)
    assert req1.can_transition_to(AuthorizationStatus.CANCELLED)


def test_authorization_state_transitions() -> None:
    """Verify valid transitions succeed and update timestamps."""
    req = AuthorizationRequest(
        server_name="test-server",
        tool_name="get_system_info",
        arguments={},
    )
    req.transition_to(AuthorizationStatus.APPROVED, reason="User clicked Allow")
    assert req.status == AuthorizationStatus.APPROVED
    assert req.is_terminal()
    assert req.decision_at is not None
    assert req.reason == "User clicked Allow"

    # Terminal state transition must fail
    with pytest.raises(InvalidStateTransitionError):
        req.transition_to(AuthorizationStatus.REJECTED)

    with pytest.raises(InvalidStateTransitionError):
        req.transition_to(AuthorizationStatus.APPROVED)


def test_invalid_decision_validation() -> None:
    """Ensure user decisions only accept APPROVED or REJECTED."""
    # Valid
    d1 = AuthorizationDecision(
        request_id="abc",
        decision=AuthorizationStatus.APPROVED,
    )
    assert d1.decision == AuthorizationStatus.APPROVED

    # Invalid: PENDING or TIMED_OUT cannot be passed as a user decision
    with pytest.raises(ValidationError):
        AuthorizationDecision(
            request_id="abc",
            decision=AuthorizationStatus.PENDING,
        )


def test_redacted_arguments_method() -> None:
    """Verify get_redacted_arguments helper on request."""
    req = AuthorizationRequest(
        server_name="demo-server",
        tool_name="auth_service",
        arguments={"username": "alice", "password": "mypassword123"},
    )
    redacted = req.get_redacted_arguments()
    assert redacted["username"] == "alice"
    assert redacted["password"] == "[REDACTED]"
    # Ensure original arguments retain the plaintext value for upstream execution
    assert req.arguments["password"] == "mypassword123"


def test_main_cli_parser_and_info() -> None:
    """Verify CLI parser and startup info output."""
    parser = build_parser()
    args = parser.parse_args(["info"])
    assert args.command == "info"

    args_proxy = parser.parse_args(["local-proxy", "--server-name", "CustomServer"])
    assert args_proxy.command == "local-proxy"
    assert args_proxy.server_name == "CustomServer"

    code = run_info()
    assert code == 0
