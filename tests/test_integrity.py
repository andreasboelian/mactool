"""Eine beschaedigte super.db darf nicht hochgeladen werden — und muss auffallen.

Geprueft wird an einer echten, gezielt zerstoerten SQLite-Datei; kein Mock der
Pruefung selbst. Verschickt wird nichts: smtplib wird durch eine Attrappe ersetzt,
die den fertigen Mailverkehr mitschreibt.
"""
import json, logging, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as config_module
import db_integrity, mailer, run_state

failures = []
def check(n, c, d=""):
    print(("  PASS  " if c else "  FAIL  ") + n + ("" if c else f" :: {d}"))
    if not c: failures.append(n)

# run_state gehoert im Test in das Arbeitsverzeichnis, nicht ins Repo
run_state.STATE_FILE = Path("run_state_integrity_test.json")
if run_state.STATE_FILE.exists(): run_state.STATE_FILE.unlink()


def build_db(path: Path) -> Path:
    if path.exists(): path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE stats (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE INDEX idx_v ON stats(v)")
    conn.executemany("INSERT INTO stats (v) VALUES (?)", [(f"value-{i:06d}",) for i in range(4000)])
    conn.commit(); conn.close()
    return path


def corrupt(path: Path) -> Path:
    """Muell mitten in eine Datenseite schreiben — der Header bleibt heil,
    die Datei sieht also weiterhin wie eine Datenbank aus."""
    with open(path, "r+b") as f:
        f.seek(4096 * 3 + 100)
        f.write(b"\xde\xad\xbe\xef" * 200)
    return path


def set_config(**overrides):
    data = json.loads(Path("config.json").read_text())
    data.update(overrides)
    Path("config.json").write_text(json.dumps(data, indent=2))
    return config_module.reload_config()


print("\n[1] Pruefung erkennt heile und kaputte Datenbank")
healthy = build_db(Path("healthy.db"))
result = db_integrity.check_database(healthy)
check("heile DB -> ok", result["ok"], str(result)[:200])
check("Detail ist 'ok'", result["detail"] == "ok", result["detail"])

broken = corrupt(build_db(Path("broken.db")))
result = db_integrity.check_database(broken)
check("kaputte DB -> nicht ok", not result["ok"], str(result)[:200])
check("Detail benennt den Defekt", len(result["detail"]) > 10, result["detail"][:120])
check(
    f"Detail bleibt kurz (war {len(result['detail'])} Zeichen)",
    len(result["detail"]) < 800,
    result["detail"][:200],
)
check("Detail einzeilig", "\n" not in result["detail"], repr(result["detail"][:120]))

Path("kein.db").write_text("das ist keine datenbank")
result = db_integrity.check_database(Path("kein.db"))
check("Nicht-Datenbank -> nicht ok", not result["ok"], str(result)[:200])

result = db_integrity.check_database(Path("gibtsnicht.db"))
check("fehlende Datei -> nicht ok", not result["ok"], str(result)[:200])


print("\n[2] Mailtext")
sent = []
real_send = mailer.send_alert
mailer.send_alert = lambda subject, body: (sent.append((subject, body)), {"status": "sent", "to": "development@ebm-group.de"})[1]

set_config(alert_smtp_host="smtp.example.de", alert_mail_cooldown_hours=6)
result = db_integrity.verify_before_upload(broken, "mac17")
check("Upload wird verweigert", not result["ok"])
check("genau eine Mail", len(sent) == 1, str(len(sent)))
subject, body = sent[0]
check("Betreff nennt Mac und Anlass",
      "mac17" in subject and "Integrity Check" in subject, subject)
check("Text ist der geforderte Satz",
      "Auf mac17 war der Integrity Check der Datenbank fehlerhaft." in body, body[:200])
check("Text sagt, dass nicht hochgeladen wurde", "nicht ausgeführt" in body, body[:300])
check("Text nennt den Pfad", str(broken) in body, body[:300])
check("run_state kennt den Fehler",
      run_state.get_runs().get("db_integrity", {}).get("status") == "error",
      str(run_state.get_runs().get("db_integrity"))[:200])


print("\n[3] Keine 12 Mails am Tag")
sent.clear()
db_integrity.verify_before_upload(broken, "mac17")
check("zweiter Lauf mailt nicht", len(sent) == 0, str(len(sent)))

state = json.loads(run_state.STATE_FILE.read_text())
state["db_integrity"]["detail"]["last_alert_at"] = (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds")
run_state.STATE_FILE.write_text(json.dumps(state))
db_integrity.verify_before_upload(broken, "mac17")
check("nach Ablauf der Sperre wieder", len(sent) == 1, str(len(sent)))

sent.clear()
db_integrity.verify_before_upload(healthy, "mac17")
check("heile DB meldet sich nicht", len(sent) == 0, str(len(sent)))
check("run_state wieder auf success",
      run_state.get_runs().get("db_integrity", {}).get("status") == "success")
db_integrity.verify_before_upload(broken, "mac17")
check("neue Stoerung mailt sofort wieder", len(sent) == 1, str(len(sent)))

sent.clear()
mailer.send_alert = lambda subject, body: (sent.append((subject, body)), {"status": "error", "error": "SMTP tot"})[1]
db_integrity.verify_before_upload(healthy, "mac17")   # Zaehler zuruecksetzen
db_integrity.verify_before_upload(broken, "mac17")
sent.clear()
db_integrity.verify_before_upload(broken, "mac17")
check("nicht zugestellte Mail wird erneut versucht", len(sent) == 1, str(len(sent)))
mailer.send_alert = real_send


print("\n[4] Der Sync bricht ab, statt Muell hochzuladen")
import sync

manager = sync.SyncManager.__new__(sync.SyncManager)   # ohne Supabase-Client
manager.db_path = broken
manager.server_prefix = "mac17"

sent.clear()
mailer.send_alert = lambda subject, body: (sent.append((subject, body)), {"status": "sent", "to": "x"})[1]
set_config(alert_smtp_host="smtp.example.de")          # Zustand zuruecksetzen
run_state.STATE_FILE.unlink(missing_ok=True)

abort = manager._integrity_gate(upload_all_logs=False)
check("Gate stoppt den Sync", abort is not None and abort["status"] == "integrity_failed", str(abort)[:200])
check("keine Tabelle angefasst", abort.get("tables") == {}, str(abort)[:200])
check("Mail ging raus", len(sent) == 1, str(len(sent)))
check("Dashboard sieht den Abbruch",
      run_state.get_runs().get("dashboard_stats", {}).get("status") == "integrity_failed",
      str(run_state.get_runs().get("dashboard_stats"))[:200])

manager.db_path = healthy
check("heile DB laesst den Sync laufen", manager._integrity_gate(False) is None)

manager.db_path = Path("gibtsnicht.db")
sent.clear()
check("fehlende DB ist kein Defekt", manager._integrity_gate(False) is None)
check("und weckt niemanden per Mail", len(sent) == 0, str(len(sent)))

manager.db_path = broken
set_config(db_integrity_check_enabled=False)
check("abschaltbar", manager._integrity_gate(False) is None)
set_config(db_integrity_check_enabled=True)
mailer.send_alert = real_send


print("\n[5] SMTP-Versand: Ablauf und fertige Mail")
import smtplib

class FakeSMTP:
    calls = []
    def __init__(self, host, port, timeout=None):
        FakeSMTP.calls.append(("connect", host, port, timeout))
    def __enter__(self): return self
    def __exit__(self, *a): FakeSMTP.calls.append(("quit",))
    def ehlo(self): FakeSMTP.calls.append(("ehlo",))
    def starttls(self): FakeSMTP.calls.append(("starttls",))
    def login(self, user, password): FakeSMTP.calls.append(("login", user, password))
    def send_message(self, msg): FakeSMTP.calls.append(("send", msg))

real_smtp, real_ssl = smtplib.SMTP, smtplib.SMTP_SSL
smtplib.SMTP = FakeSMTP
class FakeSSL(FakeSMTP):
    def __init__(self, host, port, timeout=None):
        FakeSMTP.calls.append(("connect_ssl", host, port, timeout))
smtplib.SMTP_SSL = FakeSSL

set_config(alert_smtp_host="smtp.example.de", alert_smtp_port=587, alert_smtp_user="bot@ebm-group.de",
           alert_smtp_password="geheim", alert_smtp_security="starttls",
           alert_mail_to="development@ebm-group.de", alert_mail_from="")
out = mailer.send_alert("Betreff", "Inhalt")
check("Versand gemeldet", out["status"] == "sent", str(out))
names = [c[0] for c in FakeSMTP.calls]
check("STARTTLS vor Login", names.index("starttls") < names.index("login"), str(names))
check("Port 587 benutzt", FakeSMTP.calls[0][2] == 587, str(FakeSMTP.calls[0]))
msg = [c[1] for c in FakeSMTP.calls if c[0] == "send"][0]
check("Empfaenger stimmt", msg["To"] == "development@ebm-group.de", msg["To"])
check("Absender faellt auf SMTP-Benutzer zurueck", msg["From"] == "bot@ebm-group.de", msg["From"])
check("Betreff steht drin", msg["Subject"] == "Betreff", msg["Subject"])
check("Inhalt steht drin", "Inhalt" in msg.get_content(), msg.get_content()[:60])

FakeSMTP.calls.clear()
set_config(alert_smtp_security="ssl", alert_smtp_port=465)
mailer.send_alert("B", "I")
check("SSL-Modus nutzt SMTP_SSL ohne STARTTLS",
      FakeSMTP.calls[0][0] == "connect_ssl" and "starttls" not in [c[0] for c in FakeSMTP.calls],
      str([c[0] for c in FakeSMTP.calls]))

FakeSMTP.calls.clear()
set_config(alert_smtp_user="", alert_smtp_security="none")
mailer.send_alert("B", "I")
check("ohne Benutzer kein Login", "login" not in [c[0] for c in FakeSMTP.calls],
      str([c[0] for c in FakeSMTP.calls]))

set_config(alert_smtp_host="")
out = mailer.send_alert("B", "I")
check("ohne SMTP-Server: sauberer Fehler statt Absturz", out["status"] == "not_configured", str(out))

class DeadSMTP(FakeSMTP):
    def send_message(self, msg): raise OSError("Verbindung abgelehnt")
smtplib.SMTP = DeadSMTP
set_config(alert_smtp_host="smtp.example.de", alert_smtp_security="none")
out = mailer.send_alert("B", "I")
check("toter Server wirft nicht, sondern meldet", out["status"] == "error", str(out))

smtplib.SMTP, smtplib.SMTP_SSL = real_smtp, real_ssl


print("\n[6] Dashboard-Schnittstelle")
from fastapi.testclient import TestClient
import api

client = TestClient(api.app)
routes = {r.path for r in api.app.routes}
check("/api/alert/test registriert", "/api/alert/test" in routes)
check("/api/db/integrity-check registriert", "/api/db/integrity-check" in routes)

set_config(alert_smtp_host="smtp.example.de", alert_smtp_user="bot@ebm-group.de",
           alert_smtp_password="geheim", alert_mail_to="development@ebm-group.de",
           sqlite_db_path=str(broken.resolve()))
api.reload_config()

alerts = client.get("/api/stats/settings").json()["alerts"]
check("Einstellungen kommen im Dashboard an", alerts["smtp_host"] == "smtp.example.de", str(alerts))
check("Passwort nur maskiert", "geheim" not in json.dumps(alerts), str(alerts))
check("Maskierung zeigt die letzten 4 Zeichen", alerts["password_masked"].endswith("heim"), alerts["password_masked"])

resp = client.put("/api/config", json={"alert_smtp_host": "smtp.neu.de", "alert_smtp_port": 465,
                                       "alert_smtp_security": "ssl"})
check("Speichern geht", resp.status_code == 200, resp.text[:200])
check("Passwort bleibt ohne Eingabe stehen", api.get_config().alert_smtp_password == "geheim")
client.put("/api/config", json={"alert_smtp_password": alerts["password_masked"]})
check("zurueckgesandte Maskierung ueberschreibt nichts", api.get_config().alert_smtp_password == "geheim")
check("Unsinn bei der Verschluesselung wird abgelehnt",
      client.put("/api/config", json={"alert_smtp_security": "quatsch"}).status_code == 400)

sent.clear()
mailer.send_alert = lambda subject, body: (sent.append((subject, body)), {"status": "sent", "to": "x"})[1]
data = client.post("/api/db/integrity-check").json()
check("Handpruefung meldet den Defekt", data["ok"] is False, str(data)[:200])
check("Handpruefung mailt nicht", len(sent) == 0, str(len(sent)))
mailer.send_alert = real_send

run_state.STATE_FILE.unlink(missing_ok=True)

print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
