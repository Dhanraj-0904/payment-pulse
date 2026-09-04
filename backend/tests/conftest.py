"""Test configuration using an isolated SQLite database."""

import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import engine
import app.models  # noqa: F401 - imports models into Base metadata
from app.main import app


@pytest.fixture(autouse=True)
def database_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def isolate_test_environment():
    """Ensure complete test isolation: no background threads, clean event bus, clean simulator."""
    try:
        from app.api.demo import traffic_runner
        traffic_runner.stop()
    except Exception:
        pass

    try:
        from agent.event_bus import event_bus
        event_bus._subscribers.clear()
        event_bus._ws_connections.clear()
        event_bus.event_history.clear()
    except Exception:
        pass

    try:
        from app.core.simulator_adapter import get_simulator_adapter
        adapter = get_simulator_adapter()
        adapter.simulator.incidents_config.clear()
        adapter.simulator.incident_rngs.clear()
        adapter.simulator.active_actions.clear()
        adapter.simulator.action_history.clear()
        adapter.simulator.last_step_transactions.clear()
        adapter.simulator.prior_step_transactions.clear()
    except Exception:
        pass

    yield

    try:
        from app.api.demo import traffic_runner
        traffic_runner.stop()
    except Exception:
        pass

    try:
        from agent.event_bus import event_bus
        event_bus._subscribers.clear()
        event_bus._ws_connections.clear()
        event_bus.event_history.clear()
    except Exception:
        pass

    try:
        from app.core.simulator_adapter import get_simulator_adapter
        adapter = get_simulator_adapter()
        adapter.simulator.incidents_config.clear()
        adapter.simulator.incident_rngs.clear()
        adapter.simulator.active_actions.clear()
        adapter.simulator.action_history.clear()
        adapter.simulator.last_step_transactions.clear()
        adapter.simulator.prior_step_transactions.clear()
    except Exception:
        pass


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
