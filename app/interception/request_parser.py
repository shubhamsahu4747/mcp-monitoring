"""Parser and validator for intercepted MCP tool invocation requests."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class RequestParser:
    """Validates and extracts normalized tool call arguments."""

    @staticmethod
    def parse_tool_call(
        name: str,
        arguments: Dict[str, Any] | None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Validate and normalize tool call parameters.

        Args:
            name: Raw tool name from client request.
            arguments: Tool arguments mapping or None.

        Returns:
            Tuple of (clean_tool_name, clean_arguments_dict).

        Raises:
            ValueError: If tool name is missing or empty.
        """
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Tool name must be a non-empty string")

        clean_args = dict(arguments) if arguments is not None else {}
        return clean_name, clean_args
