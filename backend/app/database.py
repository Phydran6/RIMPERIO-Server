"""
Datei:        database.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Datenbank-Anbindung (async SQLAlchemy 2.0 auf PostgreSQL).
              Stellt Engine, Session-Factory, die Basisklasse der Modelle und
              eine FastAPI-Abhängigkeit zum Beziehen einer Session bereit.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """Gemeinsame Basis aller ORM-Modelle."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-Abhängigkeit: eine Session pro Anfrage, sauber geschlossen."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Legt fehlende Tabellen an. Für einfache Schemata ohne Migrations-Tool."""
    from . import models  # noqa: F401  (Modelle registrieren)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
