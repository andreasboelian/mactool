# Statistik-Uploads im Mactool — Plan (v1.0.109)

Ziel: Zwei getrennt schaltbare Statistik-Uploads im Mactool, beide manuell triggerbar,
beide mit frei konfigurierbarer Supabase (URL sichtbar, Key maskiert, Tabelle wählbar).
Das bisherige plist-Skript `upload-macXX.py` wird dadurch überflüssig.

## Entscheidungen (mit Andreas abgestimmt)
- **Statistik (Dashboard)** = Toggle betrifft **nur die `stats`-Tabelle**. `device`, `profile`,
  `bin` und der Bot-Log-Upload laufen unabhängig weiter.
- **Statistik (Kunde)** = beide Ziele des Altskripts, jedes einzeln konfigurierbar:
  - Ziel A `statistik` (Rohdaten, UPSERT auf `session_id`)
  - Ziel B `users` (verschönerte Zahlen + Missing-Session-Einträge, INSERT mit Dublettenprüfung)
- **Zeitplan**: beide hängen an den bestehenden `sync_times`, nacheinander (kein zweiter Zeitplan).
- **Manuelle Buttons**: laufen immer — egal ob der jeweilige Toggle an oder aus ist.
- **Datenquelle Kunde**: Umschalter `sessions` (Default, wie bisher) ⇄ `super.db`.
  Beim Umschalten auf super.db erscheint ein Hinweis, welche Felder dort leer bleiben.
- **Spalten**: kein Auto-Anlegen (per PostgREST kein DDL möglich) → „Verbindung testen"
  zeigt fehlende Spalten + generiert `ALTER TABLE`-SQL. Upload überspringt fehlende Spalten.
- **Keys**: URLs + Tabellennamen als Default im Repo, Keys leer (service_role gehört nicht in Git).
- **Altes plist-Skript**: wird vom Mactool erkannt und **nach dem ersten erfolgreichen**
  Kunden-Upload automatisch deaktiviert (reversibel), plus Buttons im Dashboard.

## Recherche-Ergebnisse (verifiziert)
- Alle 13 `upload-macXX.py` sind identisch bis auf `serverzuordnung` → `config.server_name` ersetzt das.
- Zielschema `statistik` (eesha…, 20 Spalten) exakt bestätigt: id, username, session_id, start_time,
  total_interactions, successful_interactions, total_followed, total_likes, total_unfollowed,
  total_pm, total_watched, device, posts, followers, following, total_scraped(jsonb), args(jsonb),
  profile(jsonb), imported_at, serverzuordnung.
- Zielschema `users` (apltf…) bleibt unverändert — Payload wird 1:1 aus dem Altskript übernommen.
- `super.db.stats` Spalten (über die gesyncte EBM-Supabase verifiziert): id, profileID, date,
  dateTime ("HH:MM:SS YYYY-MM-DD"), follow ("13/50"), unfollow, like, comment, dm, watch,
  interaction, followers ("nill" möglich), followings, blocked, source_username,
  followBlocked, likeBlocked, logname.
- sessions.json-Schema bestätigt: id, total_*, total_scraped(dict), start_time, args(dict mit
  `device`/`username`), profile(dict mit posts/followers/following).

## Schritte
- [x] `config.py`: neue Felder + `load()` gegen unbekannte Keys härten (Downgrade-Sicherheit)
      - `dashboard_stats_enabled` (True), `dashboard_table_{device,profile,stats,bin}`
      - `customer_stats_enabled` (False), `customer_stats_source` ("sessions"), `customer_stats_session_limit` (90)
      - `customer_stats_{url,key,table}` (Ziel A), `customer_users_{url,key,table}` (Ziel B)
      - `auto_disable_legacy_upload` (True)
- [x] `config.json.example`: neue Felder mit URLs/Tabellen vorbelegt, Keys leer
- [x] `supabase_rest.py` (neu): schlanker REST-Helper (kein zweiter `create_client`)
      — `columns()`, `select_in()`, `insert_many()`, `upsert_many()`, Retry/Backoff bei 429/5xx/Timeout
- [x] `customer_stats.py` (neu):
      - Quelle A: `accounts/*/sessions.json` + `config.yml` (Device-Fallback)
      - Quelle B: `super.db` → `stats` JOIN `profile` (auf temporärer Kopie, wie sync.py)
      - Verschönerungs-Logik + Missing-Session-Logik **1:1** aus dem Altskript
      - Batch-Upload statt Einzelrequests (~30.000 → ~150 Requests) mit Einzel-Retry bei Batchfehler
      - `dry_run` für die Vorschau, `check_target()` für Verbindungstest + ALTER-SQL
- [x] `run_state.py` (neu): letzter Lauf je Upload persistiert (Zeit, Status, Zahlen)
- [x] `legacy_upload.py` (neu): plist mit `upload-*.py` finden, deaktivieren (reversibel), reaktivieren
- [x] `sync.py`: Tabellennamen aus Config, `stats` bei ausgeschaltetem Toggle überspringen, Lauf protokollieren
- [x] `scheduler.py`: ein Job je `sync_time` → Dashboard-Sync, danach Kunden-Upload
- [x] `api.py`: Config-Endpoints erweitern (Key maskiert, nur bei Eingabe überschreiben),
      neue Endpoints (`/api/stats/customer/run|preview`, `/api/stats/dashboard/run`,
      `/api/stats/test-connection`, `/api/legacy-upload/*`), Dashboard-Karte „Statistik-Uploads"
- [x] `main.py`: CLI `--customer-stats`, `--customer-stats-preview`
- [x] `.gitignore`: `run_state.json`
- [x] `CHANGELOG.md` + Tag v1.0.109
- [x] Verifikation: py_compile, API-Routen, Sessions-Parser gegen echte sessions.json,
      super.db-Mapping gegen echte stats-Daten, Dry-Run ohne Netzwerkzugriff

## Review

Alles umgesetzt und verifiziert. Neue Dateien: `customer_stats.py`, `supabase_rest.py`,
`legacy_upload.py`, `run_state.py`. Geändert: `config.py`, `config.json.example`, `sync.py`,
`scheduler.py`, `api.py`, `main.py`, `requirements.txt`, `.gitignore`, `CHANGELOG.md`.

### Verifikation
- **Zeilen-Parität**: Alle 71 echten Sessions aus einer produktiven sessions.json erzeugen
  byte-identische `users`- und `statistik`-Zeilen wie das Altskript (gleicher Random-Seed,
  Legacy-Logik im Test wortwörtlich gegenübergestellt). Auch die Missing-Session-Zeile
  stimmt inkl. Zufallswerten und session_id-Format überein.
- **super.db-Quelle**: gegen eine super.db mit dem echten Schema getestet — "13/50"→13,
  "nill"→None, dateTime "23:59:57 2026-07-15"→"2026-07-15 23:59:57", Zeilen ohne Username
  werden übersprungen, session_id bekommt den Mac-Präfix (und zwar nur einmal).
- **Upload gegen PostgREST-Mock**: Batching (1 Request statt 71), `on_conflict=session_id`
  + `merge-duplicates`, Spaltenfilterung (unbekanntes `args` wird entfernt statt Fehler),
  Dublettenschutz im zweiten Lauf (71 übersprungen, 0 neu), Missing-Einträge nur 1×/Tag,
  503-Retry mit Backoff, zeilenweiser Fallback rettet 4 von 5 Zeilen bei einer kaputten.
- **LaunchAgent-Erkennung**: findet `upload-mac17.py` und `upload_stats.py`, ignoriert
  mactool selbst, fremde Python-Skripte und Nicht-Python-Agents; Deaktivieren/Reaktivieren
  funktioniert, geschützte Agents werden abgelehnt.
- **API**: alle Routen registriert, Key nur maskiert im JSON, leeres Feld/zurückgeschickte
  Maske lassen den Key stehen, "-" löscht ihn, ungültige Datenquelle wird mit 400 abgelehnt.
- **Toggle**: `stats` wird übersprungen, `device`/`profile`/`bin` laufen weiter.
- **Scheduler**: beide Sync-Zeiten zeigen auf `run_scheduled_uploads`; ein Fehler im
  Dashboard-Sync blockiert den Kunden-Upload nicht.
- **Dashboard**: HTML/JS syntaktisch geprüft (node --check), jede `getElementById` hat ein
  Element, jeder Handler eine Funktion.
- `py_compile` über alle Dateien, CLI `--customer-stats-preview` läuft.

### Zwei Bugs beim Testen gefunden und behoben
1. Ein Tabellenname aus nur Leerzeichen hätte den gespeicherten Namen gelöscht (`if value:`
   vor dem `.strip()`).
2. plist-Pfade wurden in ein inline `onclick` interpoliert — jetzt über den Index.

### Bewusste Abweichungen vom Altskript
- Missing-Session-Einträge nur 1× pro Tag und Account statt 1× pro Lauf. Das Altskript lief
  1× nachts; bei 2 Sync-Zeiten hätte jeder inaktive Account sonst doppelt so viele
  Fake-Sessions bekommen.
- `session_id` aus super.db wird mit dem Servernamen präfigiert, weil die IDs auf allen Macs
  identisch sind. Im sessions-Modus (UUIDs) bleibt sie unverändert.
- Batch- statt Einzelrequests. Ergebnis identisch, nur ~263 statt ~31.000 Requests pro Lauf.

### Nachtrag v1.0.110 — Bug in der Spaltenerkennung

Beim ersten Test auf mac17 meldete der Verbindungstest für `users` vier fehlende Spalten
(`total_watched`, `device`, `total_scraped`, `serverzuordnung`), obwohl sie existieren.

Ursache: bei `apltfvenhqwnidmuptdp` antwortet die OpenAPI-Introspektion für den anon-Key mit
401, also griff der Fallback „eine Zeile lesen und Schlüssel zählen". Der zeigt aber nur die
Spalten, die der Key **lesen** darf — 15 von 19. Belegt ist die Existenz der Spalten dadurch,
dass das alte Skript sie mitschickt und dabei allein in der Nacht 370 Zeilen erfolgreich
geschrieben hat; PostgREST hätte sonst jede abgelehnt.

Gefährlich war nicht die Meldung, sondern dass dieselbe Erkennung den Upload-Payload
filterte: `device` und `serverzuordnung` wären beim ersten Lauf lautlos verschwunden.
Ausgelöst hat das nichts — im Screenshot stand „noch nicht gelaufen", und die 171 Zeilen von
heute ab 09:00 stammen vom alten Skript (Zeitstempel über eine Stunde im 30-Sekunden-Takt,
das Einzelrequest-Muster; mein Code schreibt in einem Batch).

Behoben: Vorfiltern nur noch bei lesbarer OpenAPI-Spec, sonst alles senden und eine Spalte
erst entfernen, wenn die Datenbank sie wirklich ablehnt (PGRST204/42703). Verbindungstest
unterscheidet jetzt `incomplete` von `unverified`. Zeilenzahl via `count=planned`, weil
`count=exact` auf der grossen users-Tabelle einen 500er auslöste.

Getestet mit einem Mock, der genau diese Konstellation nachbaut (OpenAPI 401, Stichprobe mit
15 von 19 Spalten): alle vier Felder kommen jetzt mit echten Werten an; eine tatsächlich
fehlende Spalte wird entfernt, ohne die übrigen zu verlieren.

### Nachtrag v1.0.111 — Ziel B lief auf mac17 nie

Befund am 10.08.: Ziel A (statistik) lieferte von mac17 um 08:10 einen vollstaendigen Lauf
mit 17.447 Zeilen inkl. der Session von 08:04 desselben Morgens — vorher waren es ~100
Zeilen pro Tag (Altskript, das regelmaessig frueh abbrach). Ziel B (users) dagegen hatte
von den 40 neuesten mac17-Sessions **keine einzige**; auch der manuelle Lauf am 06.08. hat
dort nichts geschrieben.

Ursache: auf mac17 war nur `customer_stats_key` hinterlegt, `customer_users_key` nicht.
Ein Ziel ohne URL/Key bekam den Status `not_configured` und wurde uebersprungen — der
Gesamtlauf meldete aber `success`, weil nur der Fall „beide Ziele unkonfiguriert" gesondert
behandelt wurde. Damit war der tote Zweig nirgends sichtbar, und schlimmer: `success` haette
das Altskript deaktiviert, obwohl es das einzige war, das users noch befuellte.

Nicht die Ursache (geprueft): die Dublettenpruefung gegen die grosse users-Tabelle
antwortet in 0,18s fuer 100 IDs.

Behoben: neuer Status `incomplete`; Auto-Deaktivierung des Altskripts nur bei echtem
`success` **und** eingeschaltetem Toggle; Dashboard zeigt je Ziel dauerhaft „Kein Key
hinterlegt" bzw. Zeitpunkt und Zeilenzahl des letzten Laufs; Ergebnis je Ziel landet in
`run_state.json`. Zusaetzlich erkennt die LaunchAgent-Suche das Skript jetzt auch in einem
Shell-Aufruf — das erklaert, warum das alte plist auf mac17 weiterlief.

### Nachtrag v1.0.112 — die tatsaechliche Ursache

Das Log von mac17 hat es beantwortet, nachdem zwei Hypothesen von mir falsch waren
(erst „Key fehlt" — der Key war da; dann „uneinheitliche Zeilen" — device/start_time sind
zu 100% gefuellt):

    HTTP 401: {"code":"42501","message":"permission denied for view users"}

`users` ist eine **View**, und der anon-Key hat darauf kein INSERT-Recht. Dazu passend
meldete derselbe Lauf `removed_columns: device, serverzuordnung, total_scraped,
total_watched` — die View stellt wirklich nur 15 Spalten bereit, es war also keine
Rechtefrage beim Lesen, sondern die View ist tatsaechlich schmaler.

Ziel A funktioniert, weil dort ein service_role-Key auf eine echte Tabelle schreibt.

Das ist ein serverseitiges Rechte-/Schema-Thema in der Kunden-Supabase, kein Mactool-Bug.
Behoben wurde die Diagnostizierbarkeit: Rechte-Fehler brechen sofort ab (1 statt 946
Requests), das Dashboard benennt den Fall samt Auswegen, und der Verbindungstest sagt
explizit, dass er nur Lesezugriff prueft.

### Nachtrag v1.0.113 — die wirkliche Ursache: falsches Schema

Die Rechteabfrage in der Kunden-Supabase zeigte zwei Relationen namens `users`:

    api.users    -> anon: SELECT                       (schmale View, 15 Spalten)
    public.users -> anon: UPDATE, DELETE, TRUNCATE, .. (echte Tabelle, 21 Spalten)

PostgREST loest eine Anfrage ohne Schema-Angabe gegen das zuerst freigegebene Schema auf —
hier `api`. `supabase-py`, das im Altskript steckt, sendet bei jedem Request
`Accept-Profile`/`Content-Profile: public` und landet deshalb auf der echten Tabelle.
Unser schlanker REST-Helper sendete diese Header nicht.

Verifiziert per Direktabfrage: ohne Header 15 Spalten, mit `Accept-Profile: public` 21 —
und die Differenz ist exakt `device, email, serverzuordnung, total_scraped, total_watched,
userid`.

Damit loest sich auch der Nachtrag zu v1.0.110 auf: die vier Spalten waren nie eine Frage
von Leserechten, sie existieren in `api.users` schlicht nicht. Der damalige Fix (nicht auf
Basis unsicherer Erkennung filtern) war trotzdem richtig und hat verhindert, dass Felder
still verloren gehen.

Behoben: beide Profile-Header werden gesendet, Schema je Ziel konfigurierbar (Default
`public`), Verbindungstest zeigt `schema.tabelle`.

### Offen (nur auf Wunsch)
- Commit + Tag v1.0.109 + Push
- Pro Mac: Keys eintragen, Toggle „Statistik (Kunde)" einschalten
