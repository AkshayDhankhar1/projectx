"""
websocket_manager.py — WebSocket Connection Manager for Live Dashboard

WebSockets allow the server to PUSH data to connected clients in real-time,
without the client having to repeatedly ask (poll) for updates.

How it works:
1. Dashboard opens a WebSocket connection to ws://localhost:8000/ws
2. When new events are ingested, we broadcast them to ALL connected dashboards
3. The dashboard JavaScript receives the data and updates the UI instantly

This module manages the list of active WebSocket connections and provides
a broadcast function to send data to all connected clients at once.
"""

from fastapi import WebSocket
import json
import structlog

logger = structlog.get_logger(__name__)


class WebSocketManager:
    """Manages active WebSocket connections for the live dashboard.
    
    Pattern: when a client connects, we add their WebSocket to a list.
    When we want to send data, we iterate the list and send to each.
    When a client disconnects, we remove them from the list.
    """

    def __init__(self):
        # List of currently connected WebSocket clients
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and add it to the active list."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            "websocket_connected",
            total_connections=len(self.active_connections),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(
            "websocket_disconnected",
            total_connections=len(self.active_connections),
        )

    async def broadcast(self, data: dict) -> None:
        """Send a JSON message to ALL connected WebSocket clients.
        
        If sending to a specific client fails (e.g., they disconnected
        without telling us), we silently remove them from the list.
        This is called "fire and forget with cleanup".
        """
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                # Client disconnected without proper close handshake
                disconnected.append(connection)

        # Clean up any connections that failed
        for conn in disconnected:
            self.disconnect(conn)


# ============================================================
# Global singleton — one manager for the whole app
# ============================================================
ws_manager = WebSocketManager()
