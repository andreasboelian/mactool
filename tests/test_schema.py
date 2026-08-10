"""Regression: requests must name the schema, otherwise PostgREST picks its own."""
import json, logging, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)
import config as cfg_mod, customer_stats as cs
from supabase_rest import SupabaseRest, describe_target

failures=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f" :: {d}"))
    if not c: failures.append(n)

# Mimics the real project: 'api' is the default (narrow, read-only view),
# 'public' is the real table.
API_COLS   = ["id","username","session_id","total_interactions","start_time"]
PUBLIC_COLS= API_COLS + ["device","serverzuordnung","total_scraped","total_watched",
                         "successful_interactions","total_followed","total_likes",
                         "total_unfollowed","total_pm","posts","followers","following","imported_at"]
SEEN={"writes":[]}

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self,code,body="[]"):
        b=body.encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        prof=self.headers.get("Accept-Profile","api")     # kein Header -> api
        cols=PUBLIC_COLS if prof=="public" else API_COLS
        if urlparse(self.path).path.rstrip("/")=="/rest/v1": return self._s(401,'{"m":"no"}')
        if self.headers.get("Prefer","").startswith("count="): return self._s(200,"[]")
        if "session_id=in." in self.path or "like.missing" in self.path: return self._s(200,"[]")
        return self._s(200, json.dumps([{c:None for c in cols}]))
    def do_POST(self):
        prof=self.headers.get("Content-Profile","api")
        rows=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))) or "[]")
        SEEN["writes"].append(prof)
        if prof!="public":
            return self._s(401,'{"code":"42501","message":"permission denied for view users"}')
        return self._s(201,"")

srv=HTTPServer(("127.0.0.1",0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
BASE=f"http://127.0.0.1:{srv.server_address[1]}"

print("\n[1] Header werden gesendet")
api=SupabaseRest(BASE,"k")
check("Default-Schema ist public", api.schema=="public", api.schema)
cols,_=api.columns_with_source("users")
check("Lesen trifft public (21 statt 5 Spalten)", "device" in cols, str(sorted(cols)[:6]))
check("api-Schema liefert die schmale View",
      "device" not in SupabaseRest(BASE,"k",schema="api").columns("users"))

print("\n[2] Upload schreibt ins richtige Schema")
cfg=cfg_mod.get_config()
cfg.customer_stats_source="sessions"
cfg.customer_stats_url=""; cfg.customer_stats_key=""
cfg.customer_users_url=BASE; cfg.customer_users_key="k"
cfg.customer_users_table="users"; cfg.customer_users_schema="public"
cfg.customer_stats_enabled=True; cfg.auto_disable_legacy_upload=False
SEEN["writes"].clear()
res=cs.run_customer_stats_upload(trigger="manual")
check("Ziel B erfolgreich", res["users"]["status"]=="success", str(res["users"])[:160])
check("Zeilen geschrieben", res["users"]["written"]>0, str(res["users"].get("written")))
check("alle Schreibzugriffe mit Content-Profile: public",
      set(SEEN["writes"])=={"public"}, str(set(SEEN["writes"])))
check("nichts weggefiltert", not res["users"].get("removed_columns"), str(res["users"].get("removed_columns")))

print("\n[3] Falsches Schema wird als Rechte-Fehler erkannt")
cfg.customer_users_schema="api"
SEEN["writes"].clear()
res=cs.run_customer_stats_upload(trigger="manual")
check("Ziel B meldet Fehler", res["users"]["status"]=="error", str(res["users"])[:120])
check("als fehlende Schreibrechte", res["users"].get("aborted")=="no_write_permission", str(res["users"])[:160])
check("Abbruch nach 1 Versuch", len(SEEN["writes"])==1, str(len(SEEN["writes"])))

print("\n[4] Verbindungstest nennt das Schema")
info=describe_target(BASE,"k","users",cs.USERS_SCHEMA,schema="public")
check("Schema im Ergebnis", info.get("schema")=="public", str(info.get("schema")))

srv.shutdown()
print("\n"+("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
