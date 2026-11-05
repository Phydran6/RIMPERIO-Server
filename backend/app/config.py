"""
Datei:        config.py
Projekt:      RIMPERIO
Paket:        backend.app
Autor:        Phydran6
Version:      2026.10.31.15.40.00

Beschreibung: Zentrale Konfiguration. Liest die Umgebung (.env) ein und stellt
              sie als getyptes Settings-Objekt bereit. Nichts im Code steht fest
              verdrahtet, was in die Umgebung gehört.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Kennung -----------------------------------------------------------
    app_name: str = "RIMPERIO"
    app_version: str = "2026.11.05.12.00.00"
    rimperio_domain: str = "localhost"
    tz: str = "Europe/Berlin"

    # --- Datenbank ---------------------------------------------------------
    postgres_user: str = "rimperio"
    postgres_password: str = "rimperio"
    postgres_db: str = "rimperio"
    db_host: str = "db"
    db_port: int = 5432

    # --- Sicherheit / Sitzungen -------------------------------------------
    rimperio_secret_key: str = "bitte-aendern-unbedingt"
    rimperio_admin_user: str = "admin"
    rimperio_admin_password: str = "admin"
    rimperio_session_minutes: int = 720
    rimperio_enroll_secret: str = "bitte-aendern-enroll"

    # --- Agent-Standardwerte ----------------------------------------------
    rimperio_checkin_interval: int = 60
    rimperio_offline_after: int = 300

    @property
    def database_url(self) -> str:
        """Async-URL für SQLAlchemy/asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.db_host}:{self.db_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
