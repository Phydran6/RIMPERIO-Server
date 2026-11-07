# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.08.14.12.00.00
# Beschreibung: RIMPERIO - Backend Konfiguration
# ==============================================================================

import logging
import sys
from typing import List
from urllib.parse import quote_plus
from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "rimperio"
    db_password: str = ""
    db_name: str = "rimperio"
    # Externe Datenbank: komplette Verbindung in einem Rutsch. Hat Vorrang vor den
    # Einzelwerten oben und akzeptiert auch die uebliche Schreibweise ohne Treiber
    # ("postgresql://..." wird auf "postgresql+asyncpg://..." gehoben).
    database_url_override: str = Field(default="", validation_alias="DATABASE_URL")
    # TLS zur Datenbank. Bei einer DB ausserhalb des Docker-Netzes gehoert hier
    # "require" (oder strenger) hin - sonst laufen Zugangsdaten und Logs im Klartext.
    # Werte: "" (aus), "require", "verify-ca", "verify-full".
    db_sslmode: str = ""
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    app_version: str = "2026.11.06.23.28.43"
    caddy_admin_url: str = "http://caddy:2019"
    caddy_certs_dir: str = "/caddy-certs"
    # Notausstieg ohne Web-UI: CADDY_FORCE_HTTP=true startet mit reinem HTTP auf
    # Port 80 und verwirft die gespeicherte Reverse-Proxy-Konfiguration.
    # Gedacht fuer den Fall "TLS kaputt, komme nicht mehr ins UI".
    caddy_force_http: bool = False
    # CORS: Komma-getrennte Liste erlaubter Origins, z.B. "https://rimperio.example.com"
    # Leer lassen = nur same-origin (empfohlen für Caddy-Deployments)
    cors_origins: str = ""
    # Öffentliche URL des Servers (für QR-Code-Generierung), z.B. "https://rimperio.example.com"
    site_url: str = ""
    # HTTPS gleich bei der Installation (install.sh --domain/--tls/--email).
    # Greift nur, solange noch keine Reverse-Proxy-Konfiguration gespeichert ist;
    # danach gilt, was unter Einstellungen -> Netzwerk eingestellt wird.
    rimperio_domain: str = ""
    rimperio_tls: str = ""          # letsencrypt | internal
    rimperio_acme_email: str = ""
    # install.sh --https-only: Port 80 nur als Weiterleitung auf HTTPS.
    rimperio_https_only: bool = False

    @property
    def database_url(self) -> str:
        """Verbindungs-URL: DATABASE_URL falls gesetzt, sonst aus den Einzelwerten."""
        raw = (self.database_url_override or "").strip()
        if raw:
            # Gaengige Schreibweisen auf den async-Treiber heben.
            for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
                if raw.startswith(prefix):
                    return self._with_ssl(raw)
            for prefix in ("postgresql://", "postgres://"):
                if raw.startswith(prefix):
                    return self._with_ssl("postgresql+asyncpg://" + raw[len(prefix):])
            return self._with_ssl(raw)

        return self._with_ssl(
            f"postgresql+asyncpg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def _with_ssl(self, url: str) -> str:
        """Haengt den TLS-Modus an, sofern gesetzt und noch nicht enthalten."""
        mode = (self.db_sslmode or "").strip()
        if not mode or "ssl=" in url or "sslmode=" in url:
            return url
        # asyncpg kennt den Parameter als "ssl", nicht als "sslmode".
        return f"{url}{'&' if '?' in url else '?'}ssl={mode}"

    @property
    def database_is_external(self) -> bool:
        """True, wenn die Datenbank nicht der mitgelieferte Container ist."""
        if (self.database_url_override or "").strip():
            return "@postgres:" not in self.database_url_override
        return self.db_host not in ("postgres", "localhost", "127.0.0.1")

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    class Config:
        extra = "ignore"


settings = Settings()


_WEAK_SECRETS = {"change-me", "changeme", "secret", "jwt-secret", "rimperio", ""}


def validate_security_settings() -> None:
    """Prueft beim Start, ob die Schluessel taugen.

    Mit dem JWT-Secret unterschreibt der Server jede Sitzung. Wer es kennt,
    stellt sich selbst einen Admin-Token aus - und hat damit die Root-Shell
    auf dem Host. Ein bekannter Standardwert ist darum kein Warnfall, sondern
    ein Startabbruch. Notausstieg fuer Testaufbauten:
    RIMPERIO_ALLOW_WEAK_SECRET=true.
    """
    import os
    logger = logging.getLogger("rimperio.security")
    secret = settings.jwt_secret or ""
    weak = secret.strip().lower() in _WEAK_SECRETS
    short = len(secret) < 32
    if not (weak or short):
        return
    if weak:
        logger.critical(
            "SICHERHEITSWARNUNG: JWT_SECRET ist ein bekannter Standardwert! "
            "Setze JWT_SECRET in der .env auf einen langen Zufallswert, z. B. "
            "`openssl rand -hex 32`.")
        if os.getenv("RIMPERIO_ALLOW_WEAK_SECRET", "").lower() not in ("1", "true", "yes"):
            logger.critical("Start abgebrochen. (Nur für Tests: RIMPERIO_ALLOW_WEAK_SECRET=true)")
            sys.exit(1)
    else:
        logger.warning(
            "JWT_SECRET ist kürzer als 32 Zeichen. Empfohlen: `openssl rand -hex 32`.")
