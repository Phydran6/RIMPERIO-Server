# ---------------------------------------------------------------------------
# Datei:        frontend/Dockerfile
# Projekt:      RIMPERIO — Frontend
# Autor:        Phydran6
# Version:      2026.10.31.15.40.00
# Beschreibung: Baut die Vue-Oberfläche und liefert sie über nginx aus.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
