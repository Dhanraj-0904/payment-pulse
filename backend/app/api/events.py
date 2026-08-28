import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from agent.event_bus import event_bus

router = APIRouter(prefix="/api/events", tags=["events"])
ws_router = APIRouter(tags=["websockets"])

@router.get("/history")
def get_event_history():
    """Retrieve in-memory payment event history."""
    return [json.loads(evt.model_dump_json()) for evt in event_bus.event_history]

@ws_router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """WebSocket connection for real-time SRE metrics streaming."""
    await websocket.accept()
    event_bus.register_ws(websocket)
    
    # Push recent event history immediately upon connection for chart loading
    for evt in event_bus.event_history:
        try:
            await websocket.send_text(evt.model_dump_json())
        except Exception:
            break
            
    try:
        while True:
            # Keep client connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unregister_ws(websocket)
