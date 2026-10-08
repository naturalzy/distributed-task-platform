from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


@lru_cache
def get_sync_session_factory() -> sessionmaker:
    """Create the sync pool inside each dispatcher/worker process, after fork."""
    engine = create_engine(
        get_settings().sync_database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)

