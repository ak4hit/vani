"""Async database engine and session factory."""

from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from src.config import settings
from src.database.base import Base
from src.utils.logger import get_logger

logger = get_logger("vani.database")

_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine(db_url: Optional[str] = None) -> AsyncEngine:
    """Retrieve or initialize the global async SQLAlchemy engine."""
    global _engine, _session_maker
    if _engine is None or db_url is not None:
        url = db_url or settings.DATABASE_URL
        # Handle SQLite check_same_thread if using sqlite+aiosqlite
        connect_args = {}
        if "sqlite" in url:
            connect_args["check_same_thread"] = False

        _engine = create_async_engine(
            url,
            echo=(settings.ENVIRONMENT == "development" and False),
            future=True,
            connect_args=connect_args
        )
        _session_maker = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
        )
        logger.info(f"Database engine initialized with dialect: {_engine.dialect.name}")
    return _engine


def get_session_maker(db_url: Optional[str] = None) -> async_sessionmaker[AsyncSession]:
    """Retrieve or initialize async session maker."""
    global _session_maker
    if _session_maker is None or db_url is not None:
        get_engine(db_url)
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an async database session."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db(engine: Optional[AsyncEngine] = None) -> None:
    """Create all registered database tables (if they do not exist)."""
    eng = engine or get_engine()
    # If running with PostgreSQL and pgvector is present, create extension if possible
    if eng.dialect.name == "postgresql":
        try:
            from sqlalchemy import text
            async with eng.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                logger.info("PostgreSQL pgvector extension verified/created.")
        except Exception as e:
            logger.warning(f"Could not auto-create vector extension: {e}")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized successfully.")
