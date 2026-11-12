import json
import logging
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from .services import get_live_queue_snapshot

logger = logging.getLogger(__name__)


class LiveQueueConsumer(AsyncJsonWebsocketConsumer):
    """
    Real-time WebSocket consumer for the ClinicHub TV Display and Staff Queue Monitor.
    Subscribes connected clients to the 'queue_live' channel group.
    Dispatches immediate full snapshots upon connection, on client demand,
    and whenever appointment status transitions occur in the clinic.
    """
    GROUP_NAME = "queue_live"

    async def connect(self):
        # Join broadcast group
        await self.channel_layer.group_add(
            self.GROUP_NAME,
            self.channel_name
        )
        await self.accept()

        # Send immediate live queue snapshot on connection
        try:
            snapshot = await self.get_snapshot()
            await self.send_json({
                "type": "queue_snapshot",
                "data": snapshot,
            })
        except Exception as exc:
            logger.warning("Error sending initial queue snapshot over WebSocket: %s", exc)

    async def disconnect(self, close_code):
        # Leave broadcast group
        await self.channel_layer.group_discard(
            self.GROUP_NAME,
            self.channel_name
        )

    async def receive_json(self, content, **kwargs):
        """
        Handle incoming messages from client (e.g. heartbeat or explicit refresh).
        """
        action = content.get("action")
        if action in ("get_queue", "refresh", "ping"):
            snapshot = await self.get_snapshot()
            await self.send_json({
                "type": "queue_snapshot",
                "data": snapshot,
            })

    async def queue_update(self, event):
        """
        Invoked by channel layer when broadcast_queue_update() triggers a 'queue.update' event.
        Fetches the freshest database state and pushes to this client.
        """
        try:
            snapshot = await self.get_snapshot()
            await self.send_json({
                "type": "queue_update",
                "data": snapshot,
            })
        except Exception as exc:
            logger.warning("Error pushing queue update to WebSocket client: %s", exc)

    @database_sync_to_async
    def get_snapshot(self):
        return get_live_queue_snapshot()
