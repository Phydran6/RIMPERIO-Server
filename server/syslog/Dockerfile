# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.03.31.17.26.46
# Beschreibung: RIMPERIO v2026.03.31.17.26.46 - Syslog Server Dockerfile
# ==============================================================================

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 514/udp
EXPOSE 514/tcp
CMD ["python", "-u", "syslog_server.py"]
