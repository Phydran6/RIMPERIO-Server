# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.11.13.03.42
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Routes Package
# ==============================================================================

from .auth import router as auth_router
from .mfa import router as mfa_router
from .health import router as health_router
from .users import router as users_router
from .agents import router as agents_router, token_router as agent_tokens_router
from .logs import router as logs_router
from .webhooks import router as webhooks_router
from .settings import router as settings_router
from .database import router as database_router
from .ldap import router as ldap_router
from .archiving import router as archiving_router
from .passkey import router as passkey_router
from .diagnostics import router as diagnostics_router
from .updates import router as updates_router
from .backup import router as backup_router
from .mobile import router as mobile_router
from .ai import router as ai_router
from .stacks import router as stacks_router
from .mail import router as mail_router
from .shell import router as shell_router
from .journal import router as journal_router
from .containers import router as containers_router
from .sso import router as sso_router
from .about import router as about_router
from .discovery import router as discovery_router
from .alerts import router as alerts_router
from . import caddy
from . import network

__all__ = ["auth_router", "mfa_router", "health_router", "users_router", "agents_router",
           "agent_tokens_router", "logs_router", "webhooks_router", "settings_router",
           "database_router", "ldap_router", "archiving_router", "passkey_router",
           "diagnostics_router", "updates_router", "backup_router", "mobile_router",
           "ai_router", "stacks_router", "mail_router", "shell_router",
           "journal_router", "containers_router", "sso_router", "about_router",
           "discovery_router", "alerts_router", "caddy", "network"]
