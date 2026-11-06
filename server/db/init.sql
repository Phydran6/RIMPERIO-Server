-- ==============================================================================
-- Name:        Phydran6
-- Kontakt:     Phydran6
-- Version:     2026.04.11.13.38.42
-- Beschreibung: RIMPERIO v2026.04.11.13.38.42 - PostgreSQL Datenbankschema
-- ==============================================================================

-- Performance-Extensions
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Benutzer-Tabelle
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100),
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'user',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agents-Tabelle (erkannte Geräte)
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL,
    ip_address VARCHAR(45),
    mac_address VARCHAR(17),
    device_type VARCHAR(50) DEFAULT 'unknown',
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    extra_data JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_hostname_ip ON agents(hostname, ip_address);
CREATE INDEX IF NOT EXISTS idx_agents_mac ON agents(mac_address);

-- Logs-Tabelle
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    agent_id INTEGER REFERENCES agents(id) ON DELETE SET NULL,
    hostname VARCHAR(255),
    ip_address VARCHAR(45),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    facility INTEGER,
    level VARCHAR(20),
    source VARCHAR(100),
    message TEXT,
    raw_message TEXT,
    extra_data JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Duplikat-Erkennung beim HTTPS-Ingest, NULL = keine Pruefung (z.B. Syslog)
    dedup_key VARCHAR(64)
);

CREATE INDEX IF NOT EXISTS idx_logs_agent_id ON logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_hostname ON logs(hostname);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source);
CREATE INDEX IF NOT EXISTS idx_logs_hostname_trgm ON logs USING gin (hostname gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_logs_source_trgm ON logs USING gin (source gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_logs_message_trgm ON logs USING gin (message gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_logs_level_lower ON logs ((lower(level)));
-- Fuer den Logtyp-Filter nach Syslog-Facility
CREATE INDEX IF NOT EXISTS idx_logs_facility ON logs(facility);
-- Duplikat-Erkennung beim HTTPS-Ingest (nur Zeilen mit Schluessel)
ALTER TABLE logs ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_dedup_key ON logs(dedup_key) WHERE dedup_key IS NOT NULL;

-- Passkeys / WebAuthn (Anmeldung ohne Passwort)
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id VARCHAR(512) UNIQUE NOT NULL,
    public_key TEXT NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    name VARCHAR(100),
    transports VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_webauthn_user ON webauthn_credentials(user_id);

-- Herkunft eines Kontos: 'local', 'ldap' oder 'sso' (Microsoft 365 / OIDC)
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_source VARCHAR(20) NOT NULL DEFAULT 'local';

-- Webhooks-Tabelle
CREATE TABLE IF NOT EXISTS webhooks (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    token VARCHAR(64) UNIQUE NOT NULL,
    description TEXT,
    filters JSONB DEFAULT '{}',
    max_results INTEGER DEFAULT 100,
    include_raw BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    call_count INTEGER DEFAULT 0,
    last_called_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Settings-Tabelle
CREATE TABLE IF NOT EXISTS settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(100) UNIQUE NOT NULL,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Standard Admin: admin / admin
INSERT INTO users (username, email, password_hash, role) 
VALUES ('admin', 'admin@localhost', '$2b$12$XOE63DtzGEyiaLLBY05W0ulT6EVFIC243bkg7UivW1kfx0.bmmSj2', 'admin')
ON CONFLICT (username) DO NOTHING;

-- Retention-Policy Spalten (werden per Startup-Migration auch auf bestehende DBs angewendet)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS retention_max_logs INTEGER;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS retention_days INTEGER;

-- Zugangsschlüssel für die Maschinen-Schnittstelle (HTTPS-Agents, Sammler).
--
-- Drei Arten, und die Art entscheidet, was der Schlüssel darf:
--   global  Generalschlüssel des Administrators — darf alles.
--   agent   gehört genau einem Gerät, darf nur für dieses liefern.
--   enroll  Einladung: kurzlebig, zählbar, darf nur einen Geräteschlüssel holen.
--
-- Gespeichert wird NIE der Schlüssel selbst, nur sein SHA-256-Abdruck
-- (token_hash). Die Spalte `token` gibt es nur noch für Bestände aus früheren
-- Fassungen; neue Schlüssel lassen sie leer. Deshalb ist sie nullable.
CREATE TABLE IF NOT EXISTS agent_tokens (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    token VARCHAR(64) UNIQUE,
    token_hash VARCHAR(64),
    prefix VARCHAR(24),
    kind VARCHAR(20) NOT NULL DEFAULT 'agent',
    device_type VARCHAR(50),
    agent_id INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    max_uses INTEGER,
    use_count INTEGER NOT NULL DEFAULT 0,
    allowed_cidrs TEXT,
    expires_at TIMESTAMP,
    revoked_at TIMESTAMP,
    last_used_at TIMESTAMP,
    last_used_ip VARCHAR(45),
    created_by VARCHAR(50),
    note TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_hash
    ON agent_tokens(token_hash) WHERE token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_tokens_agent ON agent_tokens(agent_id);

-- Systemtagebuch: was RIMPERIO SELBST getan hat.
--
-- Bewusst eine eigene Tabelle und nicht `logs`: der Aufräumlauf kürzt `logs`,
-- und ausgerechnet der Eintrag "Aufräumlauf hat 4,2 Mio. Zeilen gelöscht" darf
-- dabei nicht mit verschwinden. Die Zeilen hier sind klein und selten — sie
-- überleben problemlos Jahre.
CREATE TABLE IF NOT EXISTS system_events (
    id SERIAL PRIMARY KEY,
    at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    category VARCHAR(40) NOT NULL,
    level VARCHAR(20) NOT NULL DEFAULT 'info',
    event VARCHAR(80) NOT NULL,
    message TEXT NOT NULL,
    actor VARCHAR(100),
    source_ip VARCHAR(45),
    target VARCHAR(200),
    ok BOOLEAN NOT NULL DEFAULT TRUE,
    duration_ms INTEGER,
    detail JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_system_events_at ON system_events(at DESC);
CREATE INDEX IF NOT EXISTS idx_system_events_category ON system_events(category);
CREATE INDEX IF NOT EXISTS idx_system_events_level ON system_events(level);

-- App-Login-Tokens (für QR-Code-Authentifizierung der Android-App)
CREATE TABLE IF NOT EXISTS app_login_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(64) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_app_login_tokens_token ON app_login_tokens(token);
CREATE INDEX IF NOT EXISTS idx_app_login_tokens_user_id ON app_login_tokens(user_id);

-- Netzwerksuche: was im Netz haengt (backend/app/discovery.py).
--
-- Getrennt von `agents`: dort steht, wer Logs *liefert*; hier, wer im Netz
-- *gefunden* wurde. Der Abgleich passiert beim Lesen, weil sich "liefert"
-- mit jeder Logzeile aendert und nicht mit jedem Suchlauf.
CREATE TABLE IF NOT EXISTS discovered_devices (
    id SERIAL PRIMARY KEY,
    ip_address VARCHAR(45) NOT NULL UNIQUE,
    mac_address VARCHAR(17),
    hostname VARCHAR(255),
    kind VARCHAR(30) NOT NULL DEFAULT 'unknown',
    os_guess VARCHAR(120),
    open_ports JSONB DEFAULT '[]',
    network VARCHAR(100),
    same_subnet BOOLEAN,
    ignored BOOLEAN NOT NULL DEFAULT FALSE,
    note TEXT,
    details JSONB DEFAULT '{}',
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_mac ON discovered_devices(mac_address);

-- Standard-Einstellungen
INSERT INTO settings (key, value, description) VALUES
    ('log_retention_days', '90', 'Logs älter als X Tage löschen'),
    ('agent_offline_timeout', '300', 'Sekunden bis Agent offline')
ON CONFLICT (key) DO NOTHING;
