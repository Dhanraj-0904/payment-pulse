"""Database engine and session factory."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if "sqlite" in settings.resolved_database_url else {"connect_timeout": 3}
engine = create_engine(settings.resolved_database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    """Yield a database session for future request handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_is_available() -> bool:
    """Return whether a short connection check succeeds."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
