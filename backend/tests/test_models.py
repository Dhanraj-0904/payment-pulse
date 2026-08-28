from app.db.base import Base


def test_initial_schema_contains_required_tables():
    assert {"transactions", "incidents", "agent_actions", "incident_outcomes"}.issubset(
        Base.metadata.tables
    )
