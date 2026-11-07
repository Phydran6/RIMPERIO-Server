# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.03.31.17.26.46
# Beschreibung: RIMPERIO v2026.03.31.17.26.46 - Backend API Dockerfile
# ==============================================================================

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
# gcc wird nur zum Bauen der Python-Pakete gebraucht und fliegt danach wieder
# raus: ein Compiler im laufenden (privilegierten) Container ist ein Werkzeug
# fuer Angreifer, nicht fuer uns.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc busybox-static util-linux && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc && \
    rm -rf /var/lib/apt/lists/*
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
