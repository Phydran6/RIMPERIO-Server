# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.03.31.17.26.46
# Beschreibung: RIMPERIO v2026.03.31.17.26.46 - Backend API Dockerfile
# ==============================================================================

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc busybox-static util-linux && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
