"""End-to-end upload against a fake PostgREST server."""
import json, logging, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as cfg_mod
import customer_stats as cs

failures = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f" :: {detail}"))
    if not condition:
        failures.append(name)


STATE = {
    "statistik": [],
    "users": [],
    "posts": [],            # one entry per accepted POST
    "flaky_left": 0,        # answer this many POSTs with 503 first
}

# statistik intentionally lacks "args" so column filtering gets exercised
SCHEMAS = {
    "statistik": [c for c in cs.STATS_SCHEMA if c != "args"],
    "users": list(cs.USERS_SCHEMA),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body="[]", headers=None):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path.rstrip("/") == "/rest/v1":
            definitions = {
                table: {"properties": {c: {} for c in cols}}
                for table, cols in SCHEMAS.items()
            }
            return self._send(200, json.dumps({"definitions": definitions}))

        table = parsed.path.rsplit("/", 1)[-1]
        rows = STATE.get(table, [])

        if self.headers.get("Prefer", "").startswith("count="):
            return self._send(200, "[]", {"Content-Range": f"0-0/{len(rows)}"})

        if "session_id" in query and query["session_id"][0].startswith("in."):
            wanted = set(re.findall(r'"([^"]*)"', query["session_id"][0]))
            hits = [{"session_id": r["session_id"]} for r in rows if r.get("session_id") in wanted]
            return self._send(200, json.dumps(hits))

        if query.get("session_id", [""])[0] == "like.missing-*":
            since = query.get("start_time", ["gte."])[0][4:]
            hits = [
                {"username": r["username"]}
                for r in rows
                if str(r.get("session_id", "")).startswith("missing-")
                and str(r.get("start_time", "")) >= since
            ]
            return self._send(200, json.dumps(hits))

        return self._send(200, json.dumps(rows[:1]))

    def do_POST(self):
        parsed = urlparse(self.path)
        table = parsed.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length", 0))
        rows = json.loads(self.rfile.read(length) or "[]")

        if STATE["flaky_left"] > 0:
            STATE["flaky_left"] -= 1
            return self._send(503, '{"message":"overloaded"}')

        STATE["posts"].append({
            "table": table,
            "params": parse_qs(parsed.query),
            "count": len(rows),
            "prefer": self.headers.get("Prefer", ""),
            "profile": self.headers.get("Content-Profile", ""),
            "keys": sorted(rows[0].keys()) if rows else [],
        })

        store = STATE.setdefault(table, [])
        by_id = {r.get("session_id"): i for i, r in enumerate(store)}
        for row in rows:
            # A poisoned row simulates a constraint violation on a single record
            if row.get("username") == "__POISON__":
                return self._send(400, '{"message":"invalid row"}')
            session_id = row.get("session_id")
            if session_id in by_id:
                store[by_id[session_id]] = row
            else:
                by_id[session_id] = len(store)
                store.append(row)

        return self._send(201, "")


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

cfg = cfg_mod.get_config()
cfg.customer_stats_source = "sessions"
cfg.customer_stats_url = BASE
cfg.customer_stats_key = "test-key"
cfg.customer_stats_table = "statistik"
cfg.customer_users_url = BASE
cfg.customer_users_key = "test-key"
cfg.customer_users_table = "users"
cfg.auto_disable_legacy_upload = False

# ── 1. First run ─────────────────────────────────────────────────────
print("\n[1] Erster Lauf")
result = cs.run_customer_stats_upload(trigger="manual")

check("Status success", result["status"] == "success", json.dumps(result)[:300])
check("71 Sessions gemeldet", result["sessions"] == 71, str(result["sessions"]))
check("statistik: 71 Zeilen geschrieben", result["statistik"]["written"] == 71, str(result["statistik"]))
check("statistik im Speicher", len(STATE["statistik"]) == 71, str(len(STATE["statistik"])))
check("users: 71 Sessions + 2 Missing = 73", result["users"]["written"] == 73, str(result["users"]))
check("users: nichts uebersprungen", result["users"]["skipped_existing"] == 0)

upserts = [p for p in STATE["posts"] if p["table"] == "statistik"]
check("statistik nutzt on_conflict=session_id",
      all(p["params"].get("on_conflict") == ["session_id"] for p in upserts), str(upserts[0]["params"]))
check("statistik nutzt merge-duplicates",
      all("merge-duplicates" in p["prefer"] for p in upserts), upserts[0]["prefer"])
check("gebatcht statt einzeln", len(upserts) == 1, f"{len(upserts)} Requests fuer 71 Zeilen")
check("unbekannte Spalte 'args' herausgefiltert",
      "args" not in upserts[0]["keys"] and "profile" in upserts[0]["keys"], str(upserts[0]["keys"]))
check("Schreibzugriff nennt das Schema",
      all(p["profile"] == "public" for p in STATE["posts"]),
      str({p["profile"] for p in STATE["posts"]}))

inserts = [p for p in STATE["posts"] if p["table"] == "users"]
check("users ohne on_conflict", all(not p["params"].get("on_conflict") for p in inserts))
check("users gebatcht (73 Zeilen, 100er Batch)", len(inserts) == 1, str(len(inserts)))

# ── 2. Second run: dedupe ────────────────────────────────────────────
print("\n[2] Zweiter Lauf (Dublettenschutz)")
STATE["posts"].clear()
result2 = cs.run_customer_stats_upload(trigger="manual")

check("users: alle 71 als vorhanden erkannt", result2["users"]["skipped_existing"] == 71,
      str(result2["users"]))
check("users: keine neuen Zeilen", result2["users"]["written"] == 0, str(result2["users"]["written"]))
check("Missing-Eintraege heute schon vorhanden → 0",
      result2["users"]["missing_written"] == 0, str(result2["users"]["missing_written"]))
check("users-Tabelle unveraendert (73)", len(STATE["users"]) == 73, str(len(STATE["users"])))
check("statistik erneut upserted (idempotent)",
      result2["statistik"]["written"] == 71 and len(STATE["statistik"]) == 71,
      str(len(STATE["statistik"])))

# ── 3. Missing entries from a previous day ───────────────────────────
print("\n[3] Missing-Eintrag von gestern")
for row in STATE["users"]:
    if str(row.get("session_id", "")).startswith("missing-"):
        row["start_time"] = "2020-01-01 03:00:00"
result3 = cs.run_customer_stats_upload(trigger="manual")
check("neuer Tag → Missing-Eintraege wieder geschrieben",
      result3["users"]["missing_written"] == 2, str(result3["users"]["missing_written"]))

# ── 4. Retry on 503 ──────────────────────────────────────────────────
print("\n[4] Transiente Fehler")
STATE["flaky_left"] = 2
STATE["posts"].clear()
start = time.time()
result4 = cs.run_customer_stats_upload(trigger="manual")
check("503 wird ueberstanden", result4["status"] == "success", json.dumps(result4)[:200])
check("Backoff hat gewartet", time.time() - start >= 2, f"{time.time() - start:.1f}s")

# ── 5. Bad row inside a batch ────────────────────────────────────────
print("\n[5] Fehlerhafte Einzelzeile")
STATE["posts"].clear()
api = cs.SupabaseRest(BASE, "test-key")
rows = [{"session_id": f"x{i}", "username": "ok"} for i in range(5)]
rows[2]["username"] = "__POISON__"
outcome = api.insert_many("statistik", rows)
check("gute Zeilen kommen trotzdem an", outcome["written"] == 4, str(outcome))
check("schlechte Zeile wird gemeldet", outcome["failed"] == 1, str(outcome))
check("Fallback lief zeilenweise",
      sum(1 for p in STATE["posts"] if p["table"] == "statistik") >= 4, str(len(STATE["posts"])))

# ── 6. Connection test ───────────────────────────────────────────────
print("\n[6] Verbindungstest")
info = cs.check_customer_target("customer_stats")
check("Status incomplete (args fehlt)", info["status"] == "incomplete", str(info))
check("fehlende Spalte benannt", info["missing"] == ["args"], str(info.get("missing")))
check("ALTER-SQL erzeugt", 'ADD COLUMN IF NOT EXISTS "args" jsonb' in info.get("sql", ""), info.get("sql"))
check("UNIQUE-Hinweis vorhanden", "UNIQUE INDEX" in info.get("hint", ""))

info_users = cs.check_customer_target("customer_users")
check("users-Ziel vollstaendig", info_users["status"] == "ok", str(info_users))

cfg.customer_stats_url = ""
check("ohne URL: not_configured", cs.check_customer_target("customer_stats")["status"] == "not_configured")

server.shutdown()
print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
