# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Öffentliche Schlüssel, mit denen Lizenzen geprüft werden
# ==============================================================================
"""
Wem RIMPERIO eine Lizenz glaubt.

Jede Lizenz ist mit Ed25519 signiert. Hier stehen die **öffentlichen** Schlüssel,
mit denen die Signatur geprüft wird - der private Schlüssel liegt nur beim
Herausgeber (als Secret im Lizenz-Repository auf GitHub, siehe
`licensing/README.md`). Ohne Eintrag hier gibt es keine gültige Lizenz, und
RIMPERIO läuft als Community-Ausgabe - vollständig, nur ohne die
Enterprise-Funktionen.

Einen Schlüssel erzeugen und eintragen:

    python licensing/rimperio_license.py keygen --key-id rimperio-2026

Die Ausgabe nennt die Zeile für diese Datei. Mehrere Schlüssel sind erlaubt
(Schlüsselwechsel: neuen dazu, alten erst entfernen, wenn keine Lizenz mehr
mit ihm signiert ist).

Bewusst eine Datei im Code und keine Einstellung: wer den Schlüssel in der
Oberfläche tauschen könnte, könnte sich seine Lizenz selbst ausstellen.
"""

# key_id -> öffentlicher Ed25519-Schlüssel (32 Byte, Base64)
TRUSTED_KEYS: dict[str, str] = {
    # Herausgeber-Schlüssel ab 0.2.1 (Lizenz-Repository Phydran6/RIMPERIO-Licenses)
    "rimperio-2026": "+Oasqj+kKqk3ScjjAiKa7zOc7oj9QwCqCl8k00YDc6E=",
}
