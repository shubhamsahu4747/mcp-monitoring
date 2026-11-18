"""Presenter mapping authorization domain models to safe, formatted UI view models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from app.models.requests import AuthorizationRequest, redact_sensitive_data


class RequestPresenter:
    """Transforms AuthorizationRequest models into safe UI presentation representations."""

    @staticmethod
    def get_redacted_arguments(request: AuthorizationRequest) -> Dict[str, Any]:
        """Return a deep-copied, redacted representation of tool arguments."""
        redacted = redact_sensitive_data(request.arguments)
        if isinstance(redacted, dict):
            return redacted
        return {"value": redacted}

    @classmethod
    def get_formatted_json(cls, request: AuthorizationRequest) -> str:
        """Return pretty-printed JSON representation of redacted arguments.

        Guarantees that no raw unredacted credentials or sensitive tokens are returned.
        """
        redacted_args = cls.get_redacted_arguments(request)
        try:
            return json.dumps(redacted_args, indent=2, ensure_ascii=False)
        except Exception:
            return str(redacted_args)

    @staticmethod
    def get_formatted_timestamp(dt: datetime) -> str:
        """Format datetime into a readable local or ISO string."""
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def calculate_remaining_seconds(
        request: AuthorizationRequest,
        timeout_seconds: float,
    ) -> float:
        """Compute remaining seconds before the request times out."""
        now = datetime.now(timezone.utc)
        req_ts = request.created_at
        if req_ts.tzinfo is None:
            # Handle naive datetime as UTC
            req_ts = req_ts.replace(tzinfo=timezone.utc)

        elapsed = (now - req_ts).total_seconds()
        remaining = timeout_seconds - elapsed
        return float(max(0.0, remaining))

    @staticmethod
    def get_safe_notification_summary(request: AuthorizationRequest) -> str:
        """Return a safe notification string containing no argument values."""
        return (
            f"Server: {request.server_name}\n"
            f"Tool: {request.tool_name}\n"
            f"Request ID: {request.request_id[:8]}..."
        )
