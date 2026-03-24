# ⚡ Quick Start — 2 Minuten

## Was brauchst du?

1. **Supabase Service Role Key** (von https://supabase.com/dashboard)
2. **Terminal**

---

## Installation (alles automatisch!)

```bash
bash ~/wtf/ebm/mactool/install.sh
```

Das Script fragt dich nach:
- Server Name: z.B. `mac04`
- Supabase Key: Paste den Key ein

Fertig! ✓

---

## Nach der Installation

### Web UI öffnen
```
http://localhost:8000
```

### Logs anschauen
```bash
tail -f ~/Applications/mactool/logs/mactool.log
```

### Manueller Sync
```bash
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/main.py --sync
```

---

## Service Management

```bash
# Überprüfe Status
launchctl list | grep com.ebm.mactool

# Stoppe Tool
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist

# Starte Tool
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist

# Logs (letzte 50 Zeilen)
tail -50 ~/Applications/mactool/logs/mactool.log

# Logs (Echtzeit)
tail -f ~/Applications/mactool/logs/mactool.log
```

---

## Wo liegt was?

```
~/Applications/mactool/          ← Installation
~/Desktop/GramBotStorage/        ← super.db liegt hier
~/Library/LaunchAgents/          ← Autostart-Konfiguration
~/Applications/mactool/config.json  ← Konfiguration
~/Applications/mactool/logs/     ← Logs
```

---

## Supabase Key besorgen

1. https://supabase.com/dashboard
2. Projekt: **fxreaveeihaawkusmybi**
3. **Settings** → **API**
4. Suche **`service_role`** (NICHT `anon`!)
5. Klick "Reveal" → Copy

---

## Problem?

Logs zeigen die Lösung:
```bash
tail -50 ~/Applications/mactool/logs/mactool.log
```

Für Details siehe: **INSTALLATION.md**
