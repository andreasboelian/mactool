# Tests für die Statistik-Uploads

```bash
./tests/run_all.sh
```

Läuft ohne Netzwerk und ohne echte Zugangsdaten: jedes Supabase-Ziel ist ein lokaler
PostgREST-Mock, die Fixtures baut `make_fixtures.py` frisch in ein temporäres Verzeichnis.
Der Lauf dauert ca. 30 Sekunden (ein Test wartet bewusst auf einen Retry-Backoff).

**Nie auf einen echten Account-Ordner zeigen lassen.** `sessions.json` enthält auf den Macs
Instagram-Passwörter im `args`-Block — die Fixtures sind deshalb komplett synthetisch.

## Was die einzelnen Suites absichern

| Datei | Absicherung |
|---|---|
| `test_parity.py` | Die erzeugten Zeilen sind identisch mit denen von `upload-macXX.py`. Die Legacy-Logik steht wortwörtlich im Test, inklusive Verschönerung und Missing-Session-Regel. Dazu der super.db-Parser (`"13/50"` → 13, `"nill"` → None, gedrehtes `dateTime`). |
| `test_flow.py` | Dry-Run schreibt nichts, ALTER-SQL-Generator, Erkennung des alten Upload-LaunchAgents (und dass mactool selbst sowie fremde Skripte unangetastet bleiben). |
| `test_upload.py` | Batching statt Einzelrequests, `on_conflict=session_id`, Dublettenschutz, Missing-Einträge max. 1×/Tag, Retry bei 503, zeilenweiser Fallback bei einer kaputten Zeile. |
| `test_api.py` | Alle Routen, Key-Maskierung (leer = unverändert, `-` = löschen), Config-Validierung, `stats`-Toggle im Sync, Downgrade-Sicherheit bei unbekannten Config-Feldern. |
| `test_columns.py` | Ein Key ohne Introspektionsrecht darf **nicht** dazu führen, dass Felder still verworfen werden. Regression zu v1.0.110. |
| `test_incomplete.py` | Ein Ziel ohne Key gilt nicht als „success" und deaktiviert das Altskript nicht. Regression zu v1.0.111. |
| `test_permission.py` | Fehlende Schreibrechte brechen nach dem ersten Request ab statt 946-mal. Regression zu v1.0.112. |
| `test_schema.py` | `Accept-Profile`/`Content-Profile` werden gesendet, sonst landet der Upload im falschen Schema. Regression zu v1.0.113. |

## Wenn ein Test rot wird

Die Ausgabe nennt die fehlgeschlagene Prüfung samt Ist-Wert. Einzelne Suite direkt starten:

```bash
cd "$(mktemp -d)" && python3 ~/wtf/ebm/mactool/tests/make_fixtures.py \
  && python3 ~/wtf/ebm/mactool/tests/test_upload.py
```

Die Fixtures müssen im aktuellen Arbeitsverzeichnis liegen — `config.json` wird von
`config.save()` überschrieben, deshalb baut `run_all.sh` sie vor jeder Suite neu.
