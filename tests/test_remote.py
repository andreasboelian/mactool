"""Fernzugriff: Reservieren, Verfall, gesperrte Aktionen, Maskierung, Diagnose.

Läuft gegen einen echten kleinen PostgREST-Nachbau im selben Prozess — nur so wird
das bedingte PATCH wirklich geprüft, auf dem das Reservieren beruht. Kein Netz,
keine echte Supabase.
"""
import json, logging, re, sqlite3, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as cfg_mod
import remote_agent
import remote_commands

failures = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f" :: {detail}"))
    if not condition:
        failures.append(name)


# ── Fake-PostgREST ────────────────────────────────────────────────────

STATE = {"mac_commands": [], "mac_agents": [], "next_id": 1}


def _matches(row, params):
    """PostgREST-Filter nachbilden: eq.x und in.(a,b)."""
    for key, values in params.items():
        if key in ("select", "order", "limit", "on_conflict"):
            continue
        condition = values[0]
        actual = row.get(key)
        if condition.startswith("eq."):
            if str(actual) != condition[3:]:
                return False
        elif condition.startswith("in."):
            allowed = condition[4:-1].split(",")
            if str(actual) not in allowed:
                return False
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body="[]"):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _table(self):
        path = urlparse(self.path).path
        match = re.match(r"^/rest/v1/([\w-]+)/?$", path)
        return match.group(1) if match else None

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length)) if length else None

    def do_GET(self):
        table = self._table()
        if table not in STATE:
            return self._send(404, '{"message":"not found"}')
        params = parse_qs(urlparse(self.path).query)
        rows = [r for r in STATE[table] if _matches(r, params)]
        limit = params.get("limit", [None])[0]
        if limit:
            rows = rows[: int(limit)]
        self._send(200, json.dumps(rows))

    def do_POST(self):
        table = self._table()
        if table not in STATE:
            return self._send(404, '{"message":"not found"}')
        payload = self._body()
        rows = payload if isinstance(payload, list) else [payload]
        params = parse_qs(urlparse(self.path).query)
        conflict = params.get("on_conflict", [None])[0]

        for row in rows:
            if conflict:
                existing = next(
                    (r for r in STATE[table] if r.get(conflict) == row.get(conflict)), None
                )
                if existing:
                    existing.update(row)
                    continue
            STATE[table].append(dict(row))
        self._send(201, json.dumps(rows))

    def do_PATCH(self):
        table = self._table()
        if table not in STATE:
            return self._send(404, '{"message":"not found"}')
        params = parse_qs(urlparse(self.path).query)
        values = self._body() or {}
        changed = []
        for row in STATE[table]:
            if _matches(row, params):
                row.update(values)
                changed.append(dict(row))
        # return=representation: nur die tatsächlich geänderten Zeilen
        self._send(200, json.dumps(changed))


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE_URL = f"http://127.0.0.1:{server.server_address[1]}"


# ── Testdaten ─────────────────────────────────────────────────────────


def queue(command, args=None, ttl_minutes=15, server_name="mac17"):
    """Einen Befehl in den Briefkasten legen, wie macctl.py es täte."""
    row = {
        "id": STATE["next_id"],
        "server_name": server_name,
        "command": command,
        "args": args or {},
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        ).isoformat(),
        "result": None,
        "error": None,
    }
    STATE["next_id"] += 1
    STATE["mac_commands"].append(row)
    return row["id"]


def row_by_id(command_id):
    return next(r for r in STATE["mac_commands"] if r["id"] == command_id)


cfg = cfg_mod.get_config()
cfg.server_name = "mac17"
cfg.supabase_url = BASE_URL
cfg.supabase_key = "test-service-role-key-ABCD"
cfg.remote_control_enabled = True
cfg.remote_allow_actions = True


# ── 1. Ein Befehl geht durch ──────────────────────────────────────────
print("\n[1] Befehl abarbeiten")
status_id = queue("status")
result = remote_agent.poll_once()
check("poll_once meldet ok", result["status"] == "ok", result)
check("ein Befehl ausgeführt", result["executed"] == 1, result)

done = row_by_id(status_id)
check("Status ist 'done'", done["status"] == "done", done["status"])
check("Ergebnis enthält den Servernamen",
      isinstance(done["result"], dict) and done["result"].get("server_name") == "mac17",
      done.get("result"))
check("finished_at gesetzt", bool(done.get("finished_at")))


# ── 2. Lebenszeichen ──────────────────────────────────────────────────
print("\n[2] Heartbeat")
agents = STATE["mac_agents"]
check("Mac hat sich eingetragen", len(agents) == 1, agents)
check("Servername im Heartbeat", agents and agents[0]["server_name"] == "mac17")
check("Version im Heartbeat", bool(agents and agents[0].get("version")))


# ── 3. Reservieren ist exklusiv ───────────────────────────────────────
print("\n[3] Reservieren")
race_id = queue("status")
client = remote_agent._client()
first = remote_agent._claim(client, "mac_commands", {"id": race_id})
second = remote_agent._claim(client, "mac_commands", {"id": race_id})
check("erster Zugriff gewinnt", first is True)
check("zweiter Zugriff verliert", second is False,
      "zwei Prozesse würden denselben Befehl doppelt ausführen")
row_by_id(race_id)["status"] = "done"  # aufräumen


# ── 4. Abgelaufenes wird nicht ausgeführt ─────────────────────────────
print("\n[4] Verfall")
executed = []
original = remote_commands.HANDLERS["status"]
remote_commands.HANDLERS["status"] = (
    lambda args: executed.append("lief") or {"ok": True},
    False,
)
expired_id = queue("status", ttl_minutes=-5)
result = remote_agent.poll_once()
remote_commands.HANDLERS["status"] = original

expired = row_by_id(expired_id)
check("Status ist 'expired'", expired["status"] == "expired", expired["status"])
check("Handler wurde NICHT aufgerufen", executed == [], executed)
check("poll_once zählt den Verfall", result["expired"] == 1, result)
check("Grund steht in der Zeile", "abgelaufen" in (expired.get("error") or ""))


# ── 5. Unbekannter Befehl stürzt nicht ab ─────────────────────────────
print("\n[5] Unbekannter Befehl")
bogus_id = queue("rm-rf-alles")
result = remote_agent.poll_once()
bogus = row_by_id(bogus_id)
check("poll_once läuft weiter", result["status"] == "ok", result)
check("Status ist 'error'", bogus["status"] == "error", bogus["status"])
check("Fehlertext nennt den Namen", "rm-rf-alles" in (bogus.get("error") or ""),
      bogus.get("error"))
check("kein Ergebnis geschrieben", bogus.get("result") is None)


# ── 6. Gesperrte Aktionen ─────────────────────────────────────────────
print("\n[6] remote_allow_actions = false")
cfg.remote_allow_actions = False
sync_id = queue("sync")
read_id = queue("status")
remote_agent.poll_once()
cfg.remote_allow_actions = True

blocked = row_by_id(sync_id)
allowed = row_by_id(read_id)
check("'sync' wird abgelehnt", blocked["status"] == "error", blocked["status"])
check("Ablehnung nennt den Schalter",
      "remote_allow_actions" in (blocked.get("error") or ""), blocked.get("error"))
check("'status' läuft trotzdem", allowed["status"] == "done", allowed["status"])


# ── 7. Keine Klartext-Geheimnisse ─────────────────────────────────────
print("\n[7] Maskierung")
cfg.customer_stats_key = "kunden-key-GEHEIM-1234"
cfg.alert_smtp_password = "smtp-passwort-GEHEIM"
cfg.webhook_url = "https://n8n.example/webhook/GEHEIM-ID"
config_id = queue("config")
remote_agent.poll_once()
answer = json.dumps(row_by_id(config_id)["result"], ensure_ascii=False)

for secret in ("test-service-role-key-ABCD", "kunden-key-GEHEIM-1234",
               "smtp-passwort-GEHEIM", "GEHEIM-ID"):
    check(f"'{secret[:20]}…' nicht im Klartext", secret not in answer)
check("maskierte Form vorhanden", "•" in answer)
check("harmlose Felder sichtbar", '"server_name": "mac17"' in answer)


# ── 8. Kein Ausbruch aus dem Log-Verzeichnis ──────────────────────────
print("\n[8] Pfad-Ausbruch")
for attempt in ("../../../etc/passwd", "/etc/passwd", "../config.json"):
    escape_id = queue("logs", {"file": attempt})
    remote_agent.poll_once()
    row = row_by_id(escape_id)
    resolved = (row.get("result") or {}).get("file", "")
    check(
        f"'{attempt}' bleibt im logs-Verzeichnis",
        row["status"] == "error" or resolved.startswith(str(REPO / "logs")),
        f"status={row['status']} file={resolved}",
    )


# ── 9. Abgeschalteter Fernzugriff holt nichts ─────────────────────────
print("\n[9] Fernzugriff aus")
cfg.remote_control_enabled = False
idle_id = queue("status")
result = remote_agent.poll_once()
cfg.remote_control_enabled = True
check("meldet 'disabled'", result["status"] == "disabled", result)
check("Befehl bleibt unangetastet", row_by_id(idle_id)["status"] == "queued")


# ── 10. Diagnose benennt die Ursache ──────────────────────────────────
print("\n[10] Log-Upload-Diagnose")
# Die Fixture hat eine leere device-Tabelle — also kein Gerät mit 'Phone' im
# Namen, also kein freigegebener Account. Genau das muss die Diagnose sagen.
import diagnostics

report = diagnostics.diagnose_log_upload()
check("Urteil ist ein Satz", bool(report.get("verdict")), report.get("verdict"))
check("'allowed_usernames' blockiert", "allowed_usernames" in report["blocking"],
      report["blocking"])
counts = report["steps"]["allowed_usernames"]["counts"]
check("Gegenprobe: kein Phone-Gerät", counts["devices_named_phone"] == 0, counts)
check("Gegenprobe: Profile werden trotzdem gezählt", counts["profiles"] > 0, counts)
check("Diagnose lädt nichts hoch", report["steps"]["bucket"].get("reachable") is not True)

# Mit Phone-Gerät und passender Logdatei muss der Vorwurf verschwinden
db_path = Path(cfg.sqlite_db_path).expanduser()
conn = sqlite3.connect(db_path)
conn.execute("INSERT INTO device VALUES ('ba56df78','Phone 3')")
conn.commit()
conn.close()
logs_dir = db_path.parent / "logs"
logs_dir.mkdir(exist_ok=True)
(logs_dir / "creatif_dr.log").write_text("[09/08 04:12:33] start\n")

report = diagnostics.diagnose_log_upload()
check("jetzt ist ein Account freigegeben",
      report["steps"]["allowed_usernames"]["count"] == 1,
      report["steps"]["allowed_usernames"])
check("Datei wird zugeordnet", report["steps"]["file_matching"]["matched"] == 1,
      report["steps"]["file_matching"])
check("Zeitstempel wird gelesen", report["steps"]["timestamps"]["parsed"] == 1,
      report["steps"]["timestamps"])
# Das Jahr leitet der Uploader aus der aktuellen Zeit ab, nur Monat und Tag
# stehen in der Logdatei — sonst wäre diese Zusicherung ab Silvester falsch.
expected_path = f"mac17/{datetime.now().year}-09-08_0412_creatif_dr.log"
check("Zielpfad wird gezeigt",
      report["steps"]["timestamps"]["would_upload_as"] == [expected_path],
      report["steps"]["timestamps"]["would_upload_as"])
# Die Fixture speichert "00.00-23.59" mit Punkten, verglichen wird gegen
# Doppelpunkte — der Textvergleich kann nie zutreffen, und genau das muss
# die Diagnose benennen statt es zu verschweigen.
check("Punkt/Doppelpunkt-Falle wird benannt",
      "Punkte" in (report["steps"]["timeslot"].get("hint") or ""),
      report["steps"]["timeslot"])


# ── 11. Befehlsliste ──────────────────────────────────────────────────
print("\n[11] Befehlsliste")
described = {d["command"]: d["requires_actions"] for d in remote_commands.describe()}
for name in ("status", "logs", "config", "diag-upload", "diag-db", "files", "versions"):
    check(f"'{name}' ist lesend", described.get(name) is False, described.get(name))
for name in ("sync", "bot", "rustdesk", "update", "customer-stats"):
    check(f"'{name}' braucht Aktionsrecht", described.get(name) is True, described.get(name))
check("kein Shell-Befehl in der Liste",
      not any("shell" in n or "exec" in n or "eval" in n for n in described),
      list(described))


server.shutdown()

print("\n" + ("FAIL: " + ", ".join(failures) if failures else "Alle Prüfungen bestanden."))
sys.exit(1 if failures else 0)
