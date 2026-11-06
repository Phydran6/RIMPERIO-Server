"""
================================================================================
Name:           Phydran6
Kontakt:        Phydran6
Version:        2026.08.02.14.00.00
Changelog:      docs/changelog/backend.md
================================================================================

RIMPERIO Branding API - Backend für Whitelabel-System
===================================================

Schreibende Endpunkte (Konfiguration, Uploads, Reset) sind Admins vorbehalten:
über sie lassen sich Custom-CSS und Dateien in die Oberfläche einbringen, die
jeder Besucher ausgeliefert bekommt.
================================================================================
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional
import json
import os
import re
import shutil
from datetime import datetime

from .auth import get_current_admin
from .models import User

# =============================================================================
# Router-Instanz
# =============================================================================
branding_router = APIRouter(prefix="/api/branding", tags=["Branding"])

# =============================================================================
# Pfade
# =============================================================================
BRANDING_CONFIG_PATH = "/app/data/branding_config.json"
ASSETS_DIR = "/app/data/assets"

# Logos/Favicons sind kleine Dateien - alles darüber ist ein Versehen oder Absicht.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Erlaubte Asset-Namen: genau das Muster, das die Upload-Routine selbst erzeugt.
ASSET_NAME_PATTERN = re.compile(r"^(logo|favicon)_\d{8}_\d{6}\.[a-z0-9]{1,5}$")


def _asset_path(filename: str) -> Optional[str]:
    """Pfad zu einem Asset - oder None, wenn der Name nicht zulässig ist.

    Ohne diese Prüfung könnte über den Namen aus dem Asset-Ordner ausgebrochen
    werden (z.B. "../../etc/passwd").
    """
    if not filename or not ASSET_NAME_PATTERN.match(filename):
        return None

    filepath = os.path.normpath(os.path.join(ASSETS_DIR, filename))
    # Doppelt gesichert: der aufgelöste Pfad muss im Asset-Ordner liegen.
    if os.path.commonpath([os.path.abspath(filepath), os.path.abspath(ASSETS_DIR)]) != os.path.abspath(ASSETS_DIR):
        return None
    return filepath


# =============================================================================
# Pydantic-Modelle
# =============================================================================

class ColorScheme(BaseModel):
    """Farbschema für Dark oder Light Mode"""
    background: str = Field(description="Haupt-Hintergrund")
    surface: str = Field(description="Karten, Modals")
    surface_elevated: str = Field(description="Dropdowns, Tooltips")
    border: str = Field(description="Rahmenfarbe")
    text_primary: str = Field(description="Primärer Text")
    text_secondary: str = Field(description="Sekundärer Text")
    text_muted: str = Field(description="Gedämpfter Text")


class BrandingConfig(BaseModel):
    """Vollständige Branding-Konfiguration"""
    
    # Allgemein
    company_name: str = "RIMPERIO"
    tagline: str = "Logs, Überwachung und Fernwartung"
    # Frueher stand hier "© 2026 RIMPERIO. All rights reserved." Das war schlicht
    # falsch: RIMPERIO steht unter der MIT-Lizenz, die Rechte sind also gerade
    # NICHT vorbehalten, sondern eingeraeumt. Und es beantwortete keine einzige
    # Frage, die ein Betreiber tatsaechlich hat - dafuer gibt es jetzt den
    # Bereich "Über RIMPERIO" (app/routes/about.py).
    footer_text: str = "RIMPERIO · Freie Software unter MIT-Lizenz"
    support_email: str = "support@example.com"
    
    # Assets
    logo_path: Optional[str] = None
    favicon_path: Optional[str] = None
    
    # Theme
    default_theme: str = "dark"
    allow_theme_toggle: bool = True
    
    # Markenfarben
    primary_color: str = "#b8860b"
    secondary_color: str = "#1e3a6e"
    accent_color: str = "#d4af37"
    success_color: str = "#22c55e"
    warning_color: str = "#f59e0b"
    danger_color: str = "#ef4444"
    
    # Dark Mode Farben
    dark_scheme: ColorScheme = ColorScheme(
        background="#0b1322",
        surface="#111c32",
        surface_elevated="#17243f",
        border="#24324f",
        text_primary="#f8fafc",
        text_secondary="#cbd5e1",
        text_muted="#94a3b8"
    )

    # Light Mode Farben
    light_scheme: ColorScheme = ColorScheme(
        background="#f4f6fa",
        surface="#ffffff",
        surface_elevated="#f8fafc",
        border="#e2e8f0",
        text_primary="#0f172a",
        text_secondary="#334155",
        text_muted="#64748b"
    )

    # Custom CSS
    custom_css: str = ""


# =============================================================================
# Farbschema-Umstellung 2026.08.02
# =============================================================================
# Das Design wurde auf ein kontrastreicheres Schema umgestellt. Bestehende
# Installationen haben die alten Standardfarben in ihrer branding_config.json
# liegen - die wuerden das neue Design ueberschreiben. Deshalb: nur wer die
# Farben NIE angefasst hat (Werte exakt wie frueher), bekommt die neuen.
# Eigene Farben bleiben unangetastet.
LEGACY_DARK_SCHEME = {
    "background": "#444464",
    "surface": "#313146",
    "surface_elevated": "#3a3a54",
    "border": "#45455f",
    "text_primary": "#f8fafc",
    "text_secondary": "#e2e8f0",
    "text_muted": "#cbd5e1",
}

LEGACY_LIGHT_SCHEME = {
    "background": "#f1f5f9",
    "surface": "#ffffff",
    "surface_elevated": "#f8fafc",
    "border": "#e2e8f0",
    "text_primary": "#0f172a",
    "text_secondary": "#334155",
    "text_muted": "#64748b",
}


def _migrate_legacy_schemes(data: dict) -> tuple[dict, bool]:
    """Hebt unveraenderte Alt-Farbschemata auf die neuen Standardwerte an."""
    defaults = BrandingConfig()
    changed = False

    def scheme_matches(stored, legacy: dict) -> bool:
        if not isinstance(stored, dict):
            return False
        return all(
            str(stored.get(key, "")).strip().lower() == value
            for key, value in legacy.items()
        )

    if scheme_matches(data.get("dark_scheme"), LEGACY_DARK_SCHEME):
        data["dark_scheme"] = defaults.dark_scheme.model_dump()
        changed = True
    if scheme_matches(data.get("light_scheme"), LEGACY_LIGHT_SCHEME):
        data["light_scheme"] = defaults.light_scheme.model_dump()
        changed = True

    return data, changed


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def ensure_directories():
    """Erstellt benötigte Verzeichnisse"""
    config_dir = os.path.dirname(BRANDING_CONFIG_PATH)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)


def load_config() -> BrandingConfig:
    """Lädt Konfiguration aus JSON oder gibt Default zurück"""
    ensure_directories()

    if os.path.exists(BRANDING_CONFIG_PATH):
        try:
            with open(BRANDING_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data, migrated = _migrate_legacy_schemes(data)
            config = BrandingConfig(**data)
            if migrated:
                # Einmalig festschreiben, damit die Umstellung nicht bei jedem
                # Aufruf neu berechnet wird.
                save_config(config)
            return config
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[Branding] Config-Fehler: {e}")
            return BrandingConfig()

    return BrandingConfig()


def save_config(config: BrandingConfig) -> None:
    """Speichert Konfiguration als JSON"""
    ensure_directories()
    with open(BRANDING_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)


# =============================================================================
# API-Endpunkte
# =============================================================================

@branding_router.get("/config", response_model=BrandingConfig)
async def get_branding_config():
    """GET /api/branding/config - Lädt aktuelle Konfiguration"""
    return load_config()


@branding_router.put("/config", response_model=BrandingConfig)
async def update_branding_config(config: BrandingConfig, _admin: User = Depends(get_current_admin)):
    """PUT /api/branding/config - Speichert Konfiguration (nur Admin)"""
    save_config(config)
    return config


async def _store_upload(file: UploadFile, allowed: list, prefix: str) -> str:
    """Prüft und speichert einen Upload; gibt den erzeugten Dateinamen zurück.

    Der Dateiname wird immer selbst erzeugt (Zeitstempel + geprüfte Endung) -
    der vom Client gelieferte Name landet nie im Dateisystem.
    """
    ensure_directories()

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed:
        raise HTTPException(400, f"Format nicht erlaubt. Erlaubt: {', '.join(allowed)}")

    filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    filepath = os.path.join(ASSETS_DIR, filename)

    # Stückweise schreiben und dabei mitzählen: ohne Limit könnte ein einziger
    # Upload die Platte des Servers füllen.
    written = 0
    try:
        with open(filepath, "wb") as buffer:
            while chunk := await file.read(64 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413, f"Datei zu groß (max. {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
                    )
                buffer.write(chunk)
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)
        raise

    return filename


def _remove_asset(name: Optional[str]) -> None:
    """Löscht ein früheres Asset, sofern der Name auf ein Asset zeigt."""
    if not name:
        return
    filepath = _asset_path(name)
    if filepath and os.path.isfile(filepath):
        os.remove(filepath)


@branding_router.post("/upload/logo")
async def upload_logo(file: UploadFile = File(...), _admin: User = Depends(get_current_admin)):
    """POST /api/branding/upload/logo - Lädt Logo hoch (nur Admin)"""
    filename = await _store_upload(file, ['.png', '.jpg', '.jpeg', '.svg', '.webp'], "logo")

    config = load_config()
    _remove_asset(config.logo_path)
    config.logo_path = filename
    save_config(config)

    return {"path": filename, "message": "Logo hochgeladen"}


@branding_router.post("/upload/favicon")
async def upload_favicon(file: UploadFile = File(...), _admin: User = Depends(get_current_admin)):
    """POST /api/branding/upload/favicon - Lädt Favicon hoch (nur Admin)"""
    filename = await _store_upload(file, ['.ico', '.png', '.svg'], "favicon")

    config = load_config()
    _remove_asset(config.favicon_path)
    config.favicon_path = filename
    save_config(config)

    return {"path": filename, "message": "Favicon hochgeladen"}


@branding_router.get("/assets/{filename}")
async def get_asset(filename: str):
    """GET /api/branding/assets/{filename} - Liefert Asset aus.

    Bewusst ohne Anmeldung: Logo und Favicon werden schon auf der Login-Seite
    gebraucht. Der Name wird streng geprüft, damit darüber nichts anderes als
    ein Asset ausgeliefert werden kann.
    """
    filepath = _asset_path(filename)
    if not filepath or not os.path.isfile(filepath):
        raise HTTPException(404, "Asset nicht gefunden")
    return FileResponse(filepath)


@branding_router.post("/reset")
async def reset_branding(_admin: User = Depends(get_current_admin)):
    """POST /api/branding/reset - Setzt auf Standardwerte zurück (nur Admin)"""
    if os.path.exists(ASSETS_DIR):
        for filename in os.listdir(ASSETS_DIR):
            filepath = os.path.join(ASSETS_DIR, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)

    if os.path.exists(BRANDING_CONFIG_PATH):
        os.remove(BRANDING_CONFIG_PATH)

    return BrandingConfig()