# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: Versionen lesen und vergleichen (SemVer + altes Schema)
# ==============================================================================
"""Versionsvergleich ohne Datenbank: python -m unittest discover -s tests (in server/backend)."""

import unittest

from app import versioning as v
from app.backup import compare_versions as backup_compare


class SemVer(unittest.TestCase):
    def test_lesen(self):
        self.assertTrue(v.is_semver("0.0.1"))
        self.assertTrue(v.is_semver("v1.2.3"))
        self.assertTrue(v.is_semver("1.0.0-rc.1+build.7"))
        self.assertFalse(v.is_semver("01.2.3"))          # keine fuehrenden Nullen
        self.assertFalse(v.is_semver("1.2"))
        self.assertFalse(v.is_semver("2026.11.25.22.00.00"))
        self.assertIsNone(v.version_key(""))
        self.assertIsNone(v.version_key("main"))

    def test_reihenfolge(self):
        # Beispiel aus SemVer 2.0.0, Abschnitt 11.
        chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
                 "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        for older, newer in zip(chain, chain[1:]):
            self.assertEqual(v.compare_versions(older, newer), -1, (older, newer))
        self.assertEqual(v.compare_versions("0.0.10", "0.0.9"), 1)
        self.assertEqual(v.compare_versions("0.10.0", "0.9.9"), 1)
        self.assertEqual(v.compare_versions("1.0.0", "0.99.99"), 1)
        self.assertEqual(v.compare_versions("v1.2.3", "1.2.3"), 0)
        self.assertEqual(v.compare_versions("1.2.3+a", "1.2.3+b"), 0)   # Build zaehlt nicht

    def test_vorab(self):
        self.assertTrue(v.is_prerelease("1.0.0-rc.1"))
        self.assertFalse(v.is_prerelease("1.0.0"))
        self.assertFalse(v.is_prerelease("v2026.11.21.3"))


class Uebergang(unittest.TestCase):
    """Der Schritt vom Zeitstempel zu SemVer darf kein "Rueckschritt" sein."""

    def test_semver_schlaegt_zeitstempel(self):
        self.assertEqual(v.compare_versions("0.0.1", "2026.11.25.22.00.00"), 1)
        self.assertEqual(v.compare_versions("0.0.1-rc.1", "2026.11.27.14.00.00"), 1)
        self.assertTrue(v.is_newer("v0.0.1", "v2026.11.25"))
        self.assertFalse(v.is_newer("2026.12.31.23.59.59", "0.0.1"))

    def test_zeitstempel_untereinander(self):
        self.assertEqual(v.compare_versions("2026.11.25.22.00.00", "2026.11.23.21.00.00"), 1)
        self.assertEqual(v.compare_versions("v2026.11.21.3", "v2026.11.21.2"), 1)
        self.assertEqual(v.compare_versions("v2026.11.21", "2026.11.21.00.00.00"), 0)
        self.assertEqual(v.compare_versions("2026.09.09.22.00.00-rc1", "2026.09.09.22.00.00"), 0)
        self.assertTrue(v.is_legacy("2026.11.25.22.00.00"))
        self.assertFalse(v.is_legacy("0.0.1"))

    def test_sicherung(self):
        self.assertEqual(backup_compare("2026.11.25.22.00.00", "0.0.1")["level"], "info")
        self.assertEqual(backup_compare("0.0.2", "0.0.1")["level"], "blocking")
        self.assertEqual(backup_compare("0.0.1", "0.0.1")["level"], "ok")
        self.assertEqual(backup_compare("", "0.0.1")["level"], "warn")


class Updater(unittest.TestCase):
    def setUp(self):
        from app import updater
        self.u = updater

    def test_vergleich_ueber_den_wechsel(self):
        local = {"version": "2026.11.27.14.00.00", "commit": "a" * 40}
        remote = {"reachable": True, "version": "0.0.1", "commit": "b" * 40}
        available, _, relation = self.u.compare(local, remote)
        self.assertTrue(available)
        self.assertEqual(relation, "behind")

        local, remote = remote | {"commit": "b" * 40}, local | {"reachable": True}
        available, _, relation = self.u.compare(local, remote)
        self.assertFalse(available)
        self.assertEqual(relation, "ahead")

    def test_gleiche_version_anderer_commit(self):
        local = {"version": "0.1.0", "commit": "a" * 40}
        remote = {"reachable": True, "version": "0.1.0", "commit": "b" * 40}
        self.assertEqual(self.u.compare(local, remote)[2], "behind")

    def test_neuestes_release_nach_version(self):
        releases = [
            {"tag": "v0.1.1", "prerelease": False},       # Wartungs-Release, zuletzt angelegt
            {"tag": "v0.2.0-rc.1", "prerelease": False},  # SemVer-Vorab, ohne GitHub-Haken
            {"tag": "v0.3.0", "prerelease": True},
            {"tag": "v0.2.0", "prerelease": False},
            {"tag": "v2026.11.25", "prerelease": False},
        ]
        self.assertEqual(self.u.pick_latest(releases)["tag"], "v0.2.0")
        self.assertEqual(self.u.pick_latest(releases[-1:])["tag"], "v2026.11.25")
        self.assertEqual(self.u.pick_latest([{"tag": "nightly"}])["tag"], "nightly")
        self.assertIsNone(self.u.pick_latest([]))


if __name__ == "__main__":
    unittest.main()
