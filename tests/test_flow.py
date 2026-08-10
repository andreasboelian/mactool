"""Dry run, ALTER-SQL generator and the legacy LaunchAgent handling."""
import json, logging, shutil, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
logging.basicConfig(level=logging.CRITICAL)

import config as cfg_mod
import customer_stats as cs
import supabase_rest
import legacy_upload

failures = []


def check(name, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + name + ("" if condition else f" :: {detail}"))
    if not condition:
        failures.append(name)


# ── 1. Dry run — must not touch the network or the run state ─────────
print("\n[1] Dry-Run (kein Upload)")
result = cs.run_customer_stats_upload(trigger="preview", dry_run=True)
check("Status dry_run", result["status"] == "dry_run", result["status"])
check("Quelle sessions", result["source"] == "sessions", result["source"])
check("2 Accounts", result["accounts"] == 2, str(result["accounts"]))
check("71 Sessions", result["sessions"] == 71, str(result["sessions"]))
# Both accounts are older than 12h and have a device → both get a missing entry,
# exactly like the legacy script would produce them.
check("2 Missing-Eintraege", result["missing_sessions"] == 2, str(result["missing_sessions"]))
check("Vorschau enthaelt Beispielzeilen", len(result["preview"]["statistik"]) == 3)
check("Dry-Run schreibt keinen Lauf-Status", not (REPO / "run_state.json").exists())

cfg = cfg_mod.get_config()
cfg.customer_stats_source = "superdb"
result = cs.run_customer_stats_upload(trigger="preview", dry_run=True)
check("super.db Dry-Run laeuft", result["status"] == "dry_run" and result["source"] == "superdb")
check("super.db: 2 Accounts, 4 Sessions",
      result["accounts"] == 2 and result["sessions"] == 4,
      f'{result["accounts"]}/{result["sessions"]}')
check("super.db meldet fehlende Felder", "unavailable_fields" in result)
check("super.db: 1 Missing-Eintrag", result["missing_sessions"] == 1, str(result["missing_sessions"]))
cfg.customer_stats_source = "sessions"

# No target configured → must report that, not crash
result = cs.run_customer_stats_upload(trigger="manual")
check("ohne Keys: not_configured statt Absturz", result["status"] == "not_configured", result["status"])
check("Lauf wurde protokolliert", (REPO / "run_state.json").exists())

# ── 2. ALTER-SQL Generator ───────────────────────────────────────────
print("\n[2] ALTER-SQL")
sql = supabase_rest.build_alter_sql("statistik", {"args": "jsonb", "posts": "integer"})
check("SQL enthaelt beide Spalten", '"args" jsonb' in sql and '"posts" integer' in sql, sql)
check("SQL ist idempotent", "IF NOT EXISTS" in sql, sql)
check("leeres SQL bei nichts fehlendem", supabase_rest.build_alter_sql("t", {}) == "")
many = supabase_rest.build_alter_sql("t", {f"c{i}": "text" for i in range(60)})
check("SQL gekuerzt bei >40 Spalten", "gekürzt" in many)

# ── 3. Legacy LaunchAgent detection ──────────────────────────────────
print("\n[3] LaunchAgent-Erkennung")
fake_dir = Path("fake_agents")
shutil.rmtree(fake_dir, ignore_errors=True)
fake_dir.mkdir()


def write_plist(name, label, args):
    body = "".join(f"<string>{a}</string>" for a in args)
    (fake_dir / name).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        f'<plist version="1.0"><dict><key>Label</key><string>{label}</string>'
        f'<key>ProgramArguments</key><array>{body}</array></dict></plist>'
    )


write_plist("com.ebm.upload.plist", "com.ebm.upload",
            ["/usr/bin/python3", "/Users/x/Downloads/upload-mac17.py"])
write_plist("com.ebm.mactool.plist", "com.ebm.mactool",
            ["/Users/x/Applications/mactool/venv/bin/python3", "/Users/x/Applications/mactool/main.py"])
write_plist("com.other.backup.plist", "com.other.backup",
            ["/usr/bin/python3", "/Users/x/scripts/backup.py"])
write_plist("com.adobe.thing.plist", "com.adobe.thing",
            ["/Applications/Adobe/thing", "--uploader"])
write_plist("com.ebm.uploader2.plist", "com.ebm.uploader2",
            ["/usr/bin/python3", "/Users/x/bin/upload_stats.py"])

legacy_upload.LAUNCH_AGENTS_DIR = fake_dir
found = legacy_upload.find_agents()
labels = sorted(a["label"] for a in found)
check("nur echte Upload-Agents gefunden", labels == ["com.ebm.upload", "com.ebm.uploader2"], str(labels))
check("mactool selbst wird ignoriert", "com.ebm.mactool" not in labels)
check("fremde Skripte werden ignoriert", "com.other.backup" not in labels)
check("Nicht-Python-Agents werden ignoriert", "com.adobe.thing" not in labels)

target = next(a for a in found if a["label"] == "com.ebm.upload")
out = legacy_upload.disable_agent(target["path"])
check("deaktivieren benennt um", out["status"] == "disabled", str(out))
check("Original weg, .mactool-disabled da",
      not (fake_dir / "com.ebm.upload.plist").exists()
      and (fake_dir / "com.ebm.upload.plist.mactool-disabled").exists())
after = {a["label"]: a["enabled"] for a in legacy_upload.find_agents()}
check("Status jetzt deaktiviert", after.get("com.ebm.upload") is False, str(after))

back = legacy_upload.enable_agent(str(fake_dir / "com.ebm.upload.plist.mactool-disabled"))
check("reaktivieren stellt Datei her",
      back["status"] == "enabled" and (fake_dir / "com.ebm.upload.plist").exists(), str(back))

blocked = legacy_upload.disable_agent(str(fake_dir / "com.ebm.mactool.plist"))
check("mactool-Agent kann nicht deaktiviert werden", blocked["status"] == "error", str(blocked))
blocked = legacy_upload.disable_agent(str(fake_dir / "com.other.backup.plist"))
check("fremder Agent kann nicht deaktiviert werden", blocked["status"] == "error", str(blocked))

shutil.rmtree(fake_dir, ignore_errors=True)

print("\n" + ("ALLE TESTS BESTANDEN" if not failures else f"{len(failures)} FEHLGESCHLAGEN: {failures}"))
sys.exit(1 if failures else 0)
