# ---------------------------------------------------------------------------
# Datei:        backend/Dockerfile
# Projekt:      RIMPERIO — Backend
# Autor:        Phydran6
# Version:      2026.10.31.15.40.00
# Beschreibung: Baut das FastAPI-Backend. Schlankes Python-Image, nur Wheels,
#               läuft als nicht-privilegierter Benutzer.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Nicht als root laufen.
RUN useradd --create-home --uid 10001 rimperio && chown -R rimperio:rimperio /app
USER rimperio

EXPOSE 8000

# Ein Worker: der Hintergrunddienst (Offline-Erkennung) soll nur einmal laufen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
