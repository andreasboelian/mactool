"""Regression: a key that cannot introspect must not cause silent field loss.

Mirrors the real apltfvenhqwnidmuptdp setup: OpenAPI returns 401 and a sampled
row only exposes part of the columns, while the table really has more.
"""
import json, logging, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as cfg_mod
import customer_stats as cs
from supabase_rest import SupabaseRest, describe_target, _unknown_column

failures = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f" :: {detail}"))
    if not condition:
        failures.append(name)


# Columns the key may READ (what select=* returns)
READABLE = ["id", "username", "session_id", "total_interactions", "successful_interactions",
            "total_followed", "total_likes", "total_unfollowed", "total_pm",
            "posts", "followers", "following", "imported_at", "start_time"]
# Columns that really EXIST (insert accepts these)
EXISTING = READABLE + ["total_watched", "device", "total_scraped", "serverzuordnung"]

STORED = []
REJECTED_COLUMN = {"name": None}   # simulate a genuinely missing column


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body="[]", headers=None):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        # Introspection blocked, exactly like the real anon key
        if parsed.path.rstrip("/") == "/rest/v1":
            return self._send(401, '{"message":"unauthorized"}')

        query = parse_qs(parsed.query)
        if self.headers.get("Prefer", "").startswith("count="):
            return self._send(200, "[]", {"Content-Range": "0-0/123456"})
        if "session_id" in query and query["session_id"][0].startswith("in."):
            return self._send(200, "[]")
        if query.get("session_id", [""])[0] == "like.missing-*":
            return self._send(200, "[]")
        return self._send(200, json.dumps([{c: None for c in READABLE}]))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        rows = json.loads(self.rfile.read(length) or "[]")
        bad = REJECTED_COLUMN["name"]
        if rows and bad and bad in rows[0]:
            return self._send(400, json.dumps(
                {"code": "PGRST204",
                 "message": f"Could not find the '{bad}' column of 'users' in the schema cache"}))
        for row in rows:
            unknown = set(row) - set(EXISTING)
            if unknown:
                return self._send(400, json.dumps(
                    {"code": "42703", "message": f"column users.{sorted(unknown)[0]} does not exist"}))
        STORED.extend(rows)
        return self._send(201, "")


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

cfg = cfg_mod.get_config()
cfg.customer_stats_source = "sessions"
cfg.customer_stats_url = ""          # target A off, we only test users here
cfg.customer_stats_key = ""
cfg.customer_users_url = BASE
cfg.customer_users_key = "anon-key"
cfg.customer_users_table = "users"
cfg.auto_disable_legacy_upload = False

# ── 1. Discovery reports its own unreliability ───────────────────────
print("\n[1] Spaltenerkennung")
api = SupabaseRest(BASE, "anon-key")
columns, source = api.columns_with_source("users")
check("Quelle ist die Zeilen-Stichprobe", source == "row", source)
check("nur die lesbaren Spalten sichtbar", columns == set(READABLE), str(sorted(columns)))

# ── 2. Upload keeps every field ──────────────────────────────────────
print("\n[2] Upload verliert keine Felder")
STORED.clear()
result = cs.run_customer_stats_upload(trigger="manual")
check("Upload erfolgreich", result["users"]["status"] == "success", str(result["users"]))
check("Zeilen geschrieben", len(STORED) > 0, str(len(STORED)))

sent = set(STORED[0])
for column in ("device", "serverzuordnung", "total_watched", "total_scraped"):
    check(f"'{column}' wurde mitgeschickt", column in sent, str(sorted(sent)))
check("device hat einen echten Wert", any(r.get("device") for r in STORED))
check("serverzuordnung ist gesetzt", all(r.get("serverzuordnung") == "mac17" for r in STORED))

# ── 3. A genuinely missing column is dropped, not fatal ──────────────
print("\n[3] Wirklich fehlende Spalte")
STORED.clear()
REJECTED_COLUMN["name"] = "total_scraped"
outcome = api.insert_many("users", [
    {"session_id": f"s{i}", "username": "u", "device": "d", "total_scraped": ""} for i in range(5)
])
check("alle Zeilen kamen an", outcome["written"] == 5, str(outcome))
check("keine Fehler", outcome["failed"] == 0, str(outcome))
check("abgelehnte Spalte wird gemeldet", outcome.get("removed_columns") == ["total_scraped"], str(outcome))
check("'device' blieb erhalten", all("device" in r for r in STORED))
check("'total_scraped' wurde entfernt", all("total_scraped" not in r for r in STORED))
REJECTED_COLUMN["name"] = None

# ── 4. Connection test wording ───────────────────────────────────────
print("\n[4] Verbindungstest")
info = describe_target(BASE, "anon-key", "users", cs.USERS_SCHEMA)
check("Status 'unverified' statt 'incomplete'", info["status"] == "unverified", info["status"])
check("nicht als verifiziert markiert", info["verified"] is False)
check("die 4 Spalten werden benannt",
      set(info["missing"]) == {"total_watched", "device", "total_scraped", "serverzuordnung"},
      str(info["missing"]))
check("Zeilenschaetzung funktioniert", info["rows"] == 123456, str(info["rows"]))

# ── 5. Error parser ──────────────────────────────────────────────────
print("\n[5] Fehler-Parser")
cases = [
    ("HTTP 400: {\"code\":\"PGRST204\",\"message\":\"Could not find the 'device' column of 'users' in the schema cache\"}",
     {"device", "username"}, "device"),
    ("HTTP 400: {\"code\":\"42703\",\"message\":\"column users.serverzuordnung does not exist\"}",
     {"serverzuordnung"}, "serverzuordnung"),
    ("HTTP 409: duplicate key value violates unique constraint \"users_pkey\"", {"id"}, None),
    ("HTTP 500: {\"message\":\"internal error\"}", {"id"}, None),
]
for text, keys, expected in cases:
    got = _unknown_column(text, keys, "users")
    check(f"{text[:45]}... → {expected}", got == expected, repr(got))

server.shutdown()
print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
