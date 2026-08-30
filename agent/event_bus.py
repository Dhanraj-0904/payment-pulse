import json
import asyncio
from typing import Callable, Any
from agent.events import PaymentEvent

class EventBus:
    def __init__(self):
        self._subscribers: list[Callable[[PaymentEvent], Any]] = []
        self._ws_connections: set[Any] = set()
        # Keep a short in-memory history of recent events for dashboards on connect
        self.event_history: list[PaymentEvent] = []

    def subscribe(self, callback: Callable[[PaymentEvent], Any]):
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[PaymentEvent], Any]):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def register_ws(self, ws: Any):
        self._ws_connections.add(ws)

    def unregister_ws(self, ws: Any):
        self._ws_connections.discard(ws)

    def publish(self, event: PaymentEvent):
        # Keep history bounded
        self.event_history.append(event)
        if len(self.event_history) > 100:
            self.event_history.pop(0)

        # 1. Notify Python subscribers
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception:
                pass

        # 2. Broadcast to WebSockets asynchronously
        if self._ws_connections:
            event_json = event.model_dump_json()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._broadcast_ws(event_json))
                else:
                    # Fallback for sync execution contexts (like threads)
                    new_loop = asyncio.new_event_loop()
                    new_loop.run_until_complete(self._broadcast_ws(event_json))
                    new_loop.close()
            except Exception:
                pass

    async def _broadcast_ws(self, data: str):
        dead_connections = set()
        for ws in list(self._ws_connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead_connections.add(ws)
        for ws in dead_connections:
            self._ws_connections.discard(ws)

event_bus = EventBus()
