# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: Mandantenfaehigkeit (ohne Datenbank)
# ==============================================================================
"""Mandanten ohne Datenbank: python -m unittest discover -s tests (in server/backend).

Die Trennung selbst (Filter in der Datenbankschicht) wird hier am erzeugten
SQL geprueft; der Durchlauf gegen PostgreSQL steht in docs/tenants/README.md.
"""

import unittest
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql

from app import patchmgmt as pm
from app import tenancy
from app.models import Agent, Log, PatchPolicy, Script


def sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class PathRules(unittest.TestCase):
    def test_allowed_for_tenants(self):
        for path in ("/api/auth/me", "/api/logs", "/api/rmm/agents", "/api/rmm/agents/abc/commands",
                     "/api/patching/overview", "/api/users", "/api/agent-tokens", "/api/app/bootstrap",
                     "/api/tenants/context", "/api/snmp/devices", "/api/pve/updates",
                     "/api/log-handling/explain/12"):
            self.assertTrue(tenancy.tenant_path_allowed(path), path)

    def test_blocked_for_tenants(self):
        for path in ("/api/shell/ws", "/api/settings", "/api/backup", "/api/updates/status",
                     "/api/containers", "/api/journal", "/api/database/status", "/api/caddy/config",
                     "/api/network/dns", "/api/discovery/devices", "/api/rmm/settings/thresholds",
                     "/api/app/server/update", "/api/snmp/probe", "/api/snmp/discovered",
                     "/api/agent-tokens/harden", "/api/log-handling/rules", "/api/stacks"):
            self.assertFalse(tenancy.tenant_path_allowed(path, "POST"), path)

    def test_read_only(self):
        self.assertTrue(tenancy.tenant_path_allowed("/api/patching/sources", "GET"))
        self.assertFalse(tenancy.tenant_path_allowed("/api/patching/sources", "POST"))
        self.assertFalse(tenancy.tenant_path_allowed("/api/patching/sources/4", "PATCH"))
        self.assertTrue(tenancy.tenant_path_allowed("/api/patching/pve/hosts", "GET"))
        self.assertFalse(tenancy.tenant_path_allowed("/api/patching/pve/hosts", "POST"))
        self.assertFalse(tenancy.tenant_path_allowed("/api/patching/pve/hosts/3", "PUT"))
        self.assertTrue(tenancy.tenant_path_allowed("/api/patching/pve/hosts/3/test", "POST"))


class Scope(unittest.TestCase):
    def tearDown(self):
        tenancy._set_enabled(False)

    def test_tenant_user_is_always_scoped(self):
        tenancy._set_enabled(False)
        self.assertEqual(tenancy.scope_for_user(SimpleNamespace(tenant_id=7), "all"), 7)

    def test_provider_header(self):
        user = SimpleNamespace(tenant_id=None)
        tenancy._set_enabled(True)
        self.assertIsNone(tenancy.scope_for_user(user, ""))
        self.assertIsNone(tenancy.scope_for_user(user, "all"))
        self.assertEqual(tenancy.scope_for_user(user, "0"), tenancy.UNASSIGNED)
        self.assertEqual(tenancy.scope_for_user(user, "12"), 12)
        self.assertIsNone(tenancy.scope_for_user(user, "abc"))
        tenancy._set_enabled(False)
        self.assertIsNone(tenancy.scope_for_user(user, "12"))

    def test_scoped_context(self):
        self.assertIsNone(tenancy.current())
        with tenancy.scoped(4, force=True):
            self.assertEqual(tenancy.current(), 4)
            self.assertTrue(tenancy.forced())
        self.assertIsNone(tenancy.current())
        self.assertFalse(tenancy.forced())

    def test_slugify(self):
        self.assertEqual(tenancy.slugify("Müller & Söhne GmbH"), "mueller-soehne-gmbh")
        self.assertEqual(tenancy.slugify("  "), "mandant")


class Criteria(unittest.TestCase):
    def test_strict_table(self):
        text = sql(select(Agent).options(*tenancy._criteria(5, False)))
        self.assertIn("agents.tenant_id = 5", text)

    def test_via_agent(self):
        text = sql(select(Log).options(*tenancy._criteria(5, False)))
        self.assertIn("logs.agent_id IN (SELECT agents.id", text)
        self.assertIn("agents.tenant_id = 5", text)

    def test_shared_read_and_write(self):
        read = sql(select(Script).options(*tenancy._criteria(5, False)))
        self.assertIn("scripts.tenant_id IS NULL", read)
        write = sql(delete(Script).options(*tenancy._criteria(5, True)))
        self.assertIn("scripts.tenant_id = 5", write)
        self.assertNotIn("IS NULL", write)

    def test_unassigned(self):
        text = sql(select(Agent).options(*tenancy._criteria(tenancy.UNASSIGNED, False)))
        self.assertIn("agents.tenant_id IS NULL", text)


class Policies(unittest.TestCase):
    def pol(self, pid, tenant_id, scope="global", value="", priority=100):
        return PatchPolicy(id=pid, name=f"p{pid}", enabled=True, tenant_id=tenant_id, scope_type=scope,
                           scope_value=value, priority=priority, settings={})

    def test_foreign_tenant_policy_never_applies(self):
        agent = Agent(id=1, hostname="a", tenant_id=2, site="Standard", agent_uid="u1")
        chosen = pm.pick_policy(agent, [self.pol(1, 3, priority=1), self.pol(2, None, priority=1000)])
        self.assertEqual(chosen.id, 2)

    def test_own_policy_beats_shared_at_same_scope(self):
        agent = Agent(id=1, hostname="a", tenant_id=2, site="Standard", agent_uid="u1")
        chosen = pm.pick_policy(agent, [self.pol(1, None, priority=1), self.pol(2, 2, priority=500)])
        self.assertEqual(chosen.id, 2)

    def test_narrower_scope_still_wins(self):
        agent = Agent(id=1, hostname="a", tenant_id=2, site="Lager", agent_uid="u1")
        chosen = pm.pick_policy(agent, [self.pol(1, 2), self.pol(2, None, scope="site", value="Lager")])
        self.assertEqual(chosen.id, 2)


if __name__ == "__main__":
    unittest.main()
