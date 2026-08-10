"""Regression: a target without a key must not read as success, and must not
retire the legacy script that is still feeding it."""
import json, logging, shutil, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as cfg_mod, customer_stats as cs, legacy_upload, run_state

failures = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f" :: {detail}"))
    if not cond: failures.append(name)

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _s(self, code, body="[]", hdr=None):
        b=body.encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        for k,v in (hdr or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=urlparse(self.path)
        if p.path.rstrip("/")=="/rest/v1":
            return self._s(200, json.dumps({"definitions":{"statistik":{"properties":{c:{} for c in cs.STATS_SCHEMA}}}}))
        return self._s(200,"[]")
    def do_POST(self): return self._s(201,"")

srv=HTTPServer(("127.0.0.1",0),H); threading.Thread(target=srv.serve_forever,daemon=True).start()
BASE=f"http://127.0.0.1:{srv.server_address[1]}"

# Fake LaunchAgents: one plain, one wrapped in a shell command
fake=Path("fake2"); shutil.rmtree(fake, ignore_errors=True); fake.mkdir()
def plist(name,label,args):
    body="".join(f"<string>{a}</string>" for a in args)
    (fake/name).write_text('<?xml version="1.0" encoding="UTF-8"?>'
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
      f'<plist version="1.0"><dict><key>Label</key><string>{label}</string>'
      f'<key>ProgramArguments</key><array>{body}</array></dict></plist>')
plist("com.ebm.upload.plist","com.ebm.upload",["/bin/sh","-c","cd /Users/x/Downloads &amp;&amp; /usr/bin/python3 upload-mac17.py"])
plist("com.other.sync.plist","com.other.sync",["/bin/sh","-c","cd /Users/x &amp;&amp; python3 sync_things.py"])
legacy_upload.LAUNCH_AGENTS_DIR = fake

print("\n[1] plist im Shell-Aufruf")
found = legacy_upload.find_agents()
check("shell-verpacktes Upload-Skript wird erkannt",
      [a["label"] for a in found] == ["com.ebm.upload"], str([a["label"] for a in found]))
check("Skriptpfad extrahiert", found and found[0]["script"] == "upload-mac17.py", str(found))

print("\n[2] Ziel B ohne Key")
cfg = cfg_mod.get_config()
cfg.customer_stats_source="sessions"
cfg.customer_stats_url=BASE; cfg.customer_stats_key="k"; cfg.customer_stats_table="statistik"
cfg.customer_users_url=BASE; cfg.customer_users_key=""          # <- genau der mac17-Fall
cfg.customer_stats_enabled=True; cfg.auto_disable_legacy_upload=True

res = cs.run_customer_stats_upload(trigger="manual")
check("Gesamtstatus 'incomplete' statt 'success'", res["status"]=="incomplete", res["status"])
check("Ziel A hat geschrieben", res["statistik"]["written"]>0, str(res["statistik"]))
check("Ziel B als not_configured markiert", res["users"]["status"]=="not_configured", str(res["users"]))
check("altes Skript bleibt AKTIV", legacy_upload.find_agents()[0]["enabled"] is True)
detail = run_state.get_runs()["customer_stats"].get("detail", {})
check("Detail je Ziel protokolliert", detail.get("users",{}).get("status")=="not_configured", str(detail))

print("\n[3] Beide Ziele konfiguriert, aber Toggle aus")
cfg.customer_users_key="k"; cfg.customer_stats_enabled=False
res = cs.run_customer_stats_upload(trigger="manual")
check("Status success", res["status"]=="success", res["status"])
check("altes Skript bleibt aktiv, weil Toggle aus", legacy_upload.find_agents()[0]["enabled"] is True)

print("\n[4] Beide Ziele + Toggle an")
cfg.customer_stats_enabled=True
res = cs.run_customer_stats_upload(trigger="manual")
check("Status success", res["status"]=="success", res["status"])
check("jetzt wird das alte Skript deaktiviert", legacy_upload.find_agents()[0]["enabled"] is False)

shutil.rmtree(fake, ignore_errors=True); srv.shutdown()
print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
