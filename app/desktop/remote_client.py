"""Outbound WebSocket client connecting Desktop Agent to Remote MCP Gateway.

Enables the Walled AI Desktop Agent on Windows to register with a remote gateway,
receive real-time authorization requests, and return user decisions over
an authenticated, persistent WebSocket connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app.config import settings

if TYPE_CHECKING:
    from app.desktop.application import WalledAIDesktopApp

logger = logging.getLogger(__name__)


class RemoteAgentWebSocketClient:
    """Manages persistent outbound WebSocket connection to the Remote MCP Gateway."""

    def __init__(
        self,
        remote_url: str,
        desktop_app: WalledAIDesktopApp,
        token: Optional[str] = None,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.remote_url = remote_url
        self.desktop_app = desktop_app
        self.token = token or settings.agent_auth_token
        self.heartbeat_interval = heartbeat_interval

        self._running = False
        self._connected = False
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to remote gateway."""
        return self._connected

    def _build_ws_url(self) -> str:
        """Construct WebSocket connection URL with authentication token query param."""
        sep = "&" if "?" in self.remote_url else "?"
        return f"{self.remote_url}{sep}token={self.token}"

    async def start(self) -> None:
        """Start the background connection and message handling loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._connection_loop())
        logger.info("RemoteAgentWebSocketClient started for URL: %s", self.remote_url)

    async def stop(self) -> None:
        """Stop client and terminate WebSocket connection."""
        self._running = False
        self._connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug("Error closing remote agent websocket: %s", e)
            self._ws = None

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

        logger.info("RemoteAgentWebSocketClient stopped.")

    async def _connection_loop(self) -> None:
        """Maintain persistent connection with automatic reconnect and exponential backoff."""
        reconnect_delay = 1.0
        max_reconnect_delay = 16.0

        target_url = self._build_ws_url()

        while self._running:
            try:
                logger.info("Connecting to Remote MCP Gateway at %s...", self.remote_url)
                async with websockets.connect(target_url) as ws:
                    self._ws = ws
                    self._connected = True
                    reconnect_delay = 1.0
                    logger.info("Connected to Remote MCP Gateway.")

                    # Update Desktop status
                    self.desktop_app.main_window.set_gateway_status(
                        True, f"Remote Gateway: Connected ({self.remote_url})"
                    )

                    # Start periodic heartbeat
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

                    # Process incoming messages until connection terminates
                    try:
                        async for raw_message in ws:
                            if not self._running:
                                break
                            await self._handle_message(raw_message, ws)
                    finally:
                        if self._heartbeat_task:
                            self._heartbeat_task.cancel()
                            self._heartbeat_task = None

            except asyncio.CancelledError:
                break
            except Exception as err:
                self._connected = False
                self.desktop_app.main_window.set_gateway_status(
                    False, f"Remote Gateway: Disconnected ({err})"
                )
                if not self._running:
                    break
                logger.warning(
                    "Remote gateway connection lost or failed: %s. Reconnecting in %.1fs...",
                    err,
                    reconnect_delay,
                )
                try:
                    await asyncio.sleep(reconnect_delay)
                except asyncio.CancelledError:
                    break
                reconnect_delay = min(reconnect_delay * 2.0, max_reconnect_delay)

    async def _heartbeat_loop(self, ws: Any) -> None:
        """Periodically send heartbeat ping messages to keep connection active."""
        try:
            while self._running and self._connected:
                await asyncio.sleep(self.heartbeat_interval)
                ping_payload = json.dumps({"type": "ping"})
                async with self._send_lock:
                    await ws.send(ping_payload)
        except (asyncio.CancelledError, ConnectionClosed):
            pass
        except Exception as e:
            logger.debug("Heartbeat exception: %s", e)

    async def _handle_message(self, raw_message: str | bytes, ws: Any) -> None:
        """Parse and process an incoming message from the remote gateway."""
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            data = json.loads(raw_message)
        except Exception as e:
            logger.warning("Failed to decode message from remote gateway: %s", e)
            return

        msg_type = data.get("type")
        if msg_type == "ping":
            async with self._send_lock:
                await ws.send(json.dumps({"type": "pong"}))
        elif msg_type == "pong":
            # Heartbeat acknowledged
            pass
        elif msg_type == "authorize":
            # Spawn asynchronous authorization handling to avoid blocking message loop
            task = asyncio.create_task(self._process_authorization_request(data, ws))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            logger.warning("Unknown message type from remote gateway: %s", msg_type)

    async def _process_authorization_request(self, data: Dict[str, Any], ws: Any) -> None:
        """Present incoming authorization request in the GUI and send decision back."""
        request_id = data.get("request_id")
        if not request_id:
            logger.warning("Authorization request missing request_id")
            return

        server_name = data.get("server_name", "Remote MCP Server")
        tool_name = data.get("tool_name", "unknown_tool")
        arguments = data.get("arguments", {})
        timeout_seconds = float(data.get("timeout_seconds", 30.0))
        client_id = data.get("client_id")
        session_id = data.get("session_id")

        logger.info(
            "Received remote authorization request: %s (tool: %s, client: %s)",
            request_id,
            tool_name,
            client_id,
        )

        try:
            decision = await self.desktop_app.request_authorization_via_gui(
                server_name=server_name,
                tool_name=tool_name,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                client_id=client_id,
                session_id=session_id,
            )
            response_payload = {
                "type": "decision",
                "request_id": decision.request_id,
                "status": decision.decision.value,
                "reason": decision.reason,
            }
            logger.info(
                "Sending decision for remote request %s: %s",
                decision.request_id,
                decision.decision.value,
            )
            async with self._send_lock:
                await ws.send(json.dumps(response_payload))
        except Exception as e:
            logger.error("Error processing remote authorization request %s: %s", request_id, e)
