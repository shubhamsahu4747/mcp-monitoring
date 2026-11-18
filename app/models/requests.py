"""Domain models and state machine for authorization requests and audit records.

Implements strict state transition enforcement, Pydantic v2 validation,
at-most-once dispatch guarantees, and sensitive data redaction.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, Optional, Sequence, cast

from pydantic import BaseModel, Field, field_validator


class AuthorizationStatus(StrEnum):
    """Lifecycle states for an MCP authorization request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


# Terminal states from which no further transition is permitted
TERMINAL_STATUSES: frozenset[AuthorizationStatus] = frozenset(
    {
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    }
)

# Valid transitions map: from_state -> set of allowed to_states
VALID_TRANSITIONS: dict[AuthorizationStatus, set[AuthorizationStatus]] = {
    AuthorizationStatus.PENDING: {
        AuthorizationStatus.APPROVED,
        AuthorizationStatus.REJECTED,
        AuthorizationStatus.TIMED_OUT,
        AuthorizationStatus.CANCELLED,
    },
    AuthorizationStatus.APPROVED: set(),
    AuthorizationStatus.REJECTED: set(),
    AuthorizationStatus.TIMED_OUT: set(),
    AuthorizationStatus.CANCELLED: set(),
}

# Regex pattern matching bearer tokens or private key blocks in string values
_SENSITIVE_VALUE_REGEX = re.compile(
    r"(?i)(bearer\s+[a-z0-9\-._~+/]+=*|-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----)",
)


class AuthorizationError(Exception):
    """Base exception for authorization-related errors."""


class InvalidStateTransitionError(AuthorizationError):
    """Raised when attempting an invalid state transition (e.g. from terminal states)."""


class DuplicateDecisionError(AuthorizationError):
    """Raised when attempting to submit a second decision for a resolved request."""


class AuthorizationTimeoutError(AuthorizationError):
    """Raised when an authorization request exceeds its time limit."""


class AuthorizationCancelledError(AuthorizationError):
    """Raised when an authorization request is cancelled before completion."""


class AlreadyDispatchedError(AuthorizationError):
    """Raised when attempting to dispatch an authorization request more than once."""


def redact_sensitive_data(
    data: Any,
    sensitive_keys: Sequence[str] = (
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "private_key",
        "access_token",
        "credential",
        "secret_key",
    ),
) -> Any:
    """Recursively redacts sensitive parameter keys and values.

    Args:
        data: Arbitrary Python structure (dict, list, tuple, set, primitive).
        sensitive_keys: Substrings treated as sensitive when found in dictionary keys.

    Returns:
        Deep copy of data with sensitive values masked with "[REDACTED]".
    """
    normalized_keys = [k.strip().lower() for k in sensitive_keys if k.strip()]

    if isinstance(data, dict):
        redacted_dict: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            key_is_sensitive = any(s in k_lower for s in normalized_keys)

            if isinstance(v, dict):
                redacted_dict[k] = redact_sensitive_data(v, normalized_keys)
            elif isinstance(v, (list, tuple, set)):
                if key_is_sensitive and not any(
                    isinstance(item, (dict, list, tuple, set)) for item in v
                ):
                    masked = ["[REDACTED]" for _ in v]
                    redacted_dict[k] = type(v)(masked) if not isinstance(v, set) else set(masked)
                else:
                    redacted_dict[k] = redact_sensitive_data(v, normalized_keys)
            elif key_is_sensitive:
                redacted_dict[k] = "[REDACTED]"
            elif isinstance(v, str) and _SENSITIVE_VALUE_REGEX.search(v):
                redacted_dict[k] = "[REDACTED]"
            else:
                redacted_dict[k] = v
        return redacted_dict

    elif isinstance(data, list):
        return [redact_sensitive_data(item, normalized_keys) for item in data]
    elif isinstance(data, tuple):
        return tuple(redact_sensitive_data(item, normalized_keys) for item in data)
    elif isinstance(data, set):
        return {redact_sensitive_data(item, normalized_keys) for item in data}
    elif isinstance(data, str) and _SENSITIVE_VALUE_REGEX.search(data):
        return "[REDACTED]"
    return data


class AuthorizationDecision(BaseModel):
    """A user or policy decision for a pending authorization request."""

    request_id: str = Field(
        min_length=1,
        description="UUID of the authorization request.",
    )
    decision: AuthorizationStatus = Field(
        description="Outcome decision: APPROVED or REJECTED.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the decision.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional justification or failure explanation.",
    )

    @field_validator("decision")
    @classmethod
    def validate_terminal_decision(cls, v: AuthorizationStatus) -> AuthorizationStatus:
        """Ensure decision submitted is explicitly APPROVED or REJECTED."""
        if v not in (AuthorizationStatus.APPROVED, AuthorizationStatus.REJECTED):
            raise ValueError(
                f"User decision must be APPROVED or REJECTED, received '{v}'"
            )
        return v


class AuthorizationRequest(BaseModel):
    """Represents an intercepted MCP tool invocation awaiting user authorization."""

    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        min_length=1,
        description="Globally unique identifier for the request.",
    )
    server_name: str = Field(
        min_length=1,
        description="Name of the MCP server hosting the intercepted tool.",
    )
    tool_name: str = Field(
        min_length=1,
        description="Name of the tool requested by the LLM client.",
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Exact tool arguments passed in the MCP tools/call request.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the request was intercepted.",
    )
    status: AuthorizationStatus = Field(
        default=AuthorizationStatus.PENDING,
        description="Current lifecycle state of the request.",
    )
    decision_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the decision was finalized.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Explanation or error details associated with the final status.",
    )
    dispatched: bool = Field(
        default=False,
        description="True if tool execution was dispatched upstream. Enforces at-most-once.",
    )
    dispatched_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when execution was dispatched.",
    )

    @property
    def timestamp(self) -> datetime:
        """Alias for created_at to provide consistent timestamp property access."""
        return self.created_at

    def is_terminal(self) -> bool:
        """Check if request has reached an immutable terminal state."""
        return self.status in TERMINAL_STATUSES

    def can_transition_to(self, new_status: AuthorizationStatus) -> bool:
        """Check if transitioning to new_status is permitted by state machine."""
        allowed = VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def transition_to(
        self,
        new_status: AuthorizationStatus,
        reason: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Safely transition request state.

        Raises:
            InvalidStateTransitionError: If the transition is invalid or already terminal.
        """
        if not self.can_transition_to(new_status):
            raise InvalidStateTransitionError(
                f"Invalid state transition for request {self.request_id}: "
                f"cannot transition from {self.status.value} to {new_status.value}."
            )
        self.status = new_status
        self.decision_at = timestamp or datetime.now(timezone.utc)
        if reason:
            self.reason = reason

    def mark_dispatched(self) -> None:
        """Mark request as dispatched upstream. Guarantees at-most-once dispatch.

        Raises:
            AlreadyDispatchedError: If request was already dispatched.
            InvalidStateTransitionError: If request is not in APPROVED status.
        """
        if self.dispatched:
            raise AlreadyDispatchedError(
                f"At-most-once violation: Request {self.request_id} has already been dispatched."
            )
        if self.status != AuthorizationStatus.APPROVED:
            raise InvalidStateTransitionError(
                f"Cannot dispatch request {self.request_id}: status is {self.status.value}, "
                f"expected APPROVED."
            )
        self.dispatched = True
        self.dispatched_at = datetime.now(timezone.utc)

    def get_redacted_arguments(
        self, sensitive_keys: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """Return a copy of the arguments with sensitive information masked."""
        from app.config import settings

        keys = sensitive_keys if sensitive_keys is not None else settings.sensitive_keys_list
        result = redact_sensitive_data(self.arguments, keys)
        return cast(Dict[str, Any], result if isinstance(result, dict) else {})


class AuditRecord(BaseModel):
    """Immutable audit entry persisted to the SQLite database."""

    request_id: str = Field(min_length=1)
    server_name: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    timestamp: datetime
    decision: AuthorizationStatus
    decision_timestamp: Optional[datetime] = None
    execution_outcome: Optional[str] = None
    failure_reason: Optional[str] = None
    arguments_redacted: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("decision")
    @classmethod
    def validate_decision_terminal(cls, v: AuthorizationStatus) -> AuthorizationStatus:
        """Audit records must record a terminal authorization outcome."""
        if v not in TERMINAL_STATUSES:
            raise ValueError(
                f"Audit record decision must be a terminal status, received '{v}'"
            )
        return v

    @classmethod
    def from_request(
        cls,
        request: AuthorizationRequest,
        execution_outcome: Optional[str] = None,
        failure_reason: Optional[str] = None,
        sensitive_keys: Optional[Sequence[str]] = None,
    ) -> AuditRecord:
        """Construct a validated AuditRecord from a completed AuthorizationRequest.

        Args:
            request: The completed AuthorizationRequest.
            execution_outcome: Optional execution summary (e.g. 'SUCCESS', 'TOOL_ERROR', 'BLOCKED').
            failure_reason: Optional failure description.
            sensitive_keys: Optional custom list of sensitive parameter keys.

        Raises:
            ValueError: If the request is not in a terminal status.
        """
        if not request.is_terminal():
            raise ValueError(
                f"Cannot create AuditRecord: request {request.request_id} "
                f"is still in state {request.status.value}"
            )

        return cls(
            request_id=request.request_id,
            server_name=request.server_name,
            tool_name=request.tool_name,
            timestamp=request.created_at,
            decision=request.status,
            decision_timestamp=request.decision_at or datetime.now(timezone.utc),
            execution_outcome=execution_outcome,
            failure_reason=failure_reason or request.reason,
            arguments_redacted=request.get_redacted_arguments(sensitive_keys),
        )
