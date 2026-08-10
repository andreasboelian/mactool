"""Regression: a missing write permission must abort at once, not retry every row."""
import json, logging, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)
from supabase_rest import SupabaseRest, _is_permission_error

failures=[]
def check(n,c,d=""):
    print(("  PASS  " if c else "  FAIL  ")+n+("" if c else f" :: {d}")); 
    if not c: failures.append(n)

POSTS={"n":0}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self,code,body="[]"):
        b=body.encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if urlparse(self.path).path.rstrip("/")=="/rest/v1": return self._s(401,'{"message":"unauthorized"}')
        return self._s(200,"[]")
    def do_POST(self):
        POSTS["n"]+=1
        self.rfile.read(int(self.headers.get("Content-Length",0)))
        return self._s(401,'{"code":"42501","details":null,"hint":null,"message":"permission denied for view users"}')

srv=HTTPServer(("127.0.0.1",0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
api=SupabaseRest(f"http://127.0.0.1:{srv.server_address[1]}","anon")

print("\n[1] Erkennung")
for txt,exp in [('HTTP 401: {"code":"42501","message":"permission denied for view users"}',True),
                ("HTTP 403: forbidden",True),
                ('HTTP 400: {"code":"PGRST204","message":"Could not find the \'x\' column"}',False),
                ("HTTP 500: internal",False)]:
    check(f"{txt[:42]}... -> {exp}", _is_permission_error(txt)==exp)

print("\n[2] Abbruch statt 946 Einzelversuchen")
rows=[{"session_id":f"s{i}","username":"u"} for i in range(946)]
POSTS["n"]=0
out=api.insert_many("users", rows)
check("als Rechte-Problem gemeldet", out.get("aborted")=="no_write_permission", str(out)[:200])
check("nichts geschrieben", out["written"]==0, str(out["written"]))
check("alle Zeilen als fehlgeschlagen gezaehlt", out["failed"]==946, str(out["failed"]))
check(f"nur 1 Request statt 946 (war: {POSTS['n']})", POSTS["n"]==1, str(POSTS["n"]))
check("Fehlermeldung erhalten", "42501" in out["errors"][0], str(out["errors"])[:120])

srv.shutdown()
print("\n"+("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
