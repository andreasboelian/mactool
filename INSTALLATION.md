# 📦 EBM Mactool — Installation Guide

Vollständige Anleitung zur Installation auf allen Macs mit Autostart.

---

## 3-Schritt Installation (für alle Macs gleich!)

### Schritt 1: Supabase Key besorgen

1. Gehe zu: https://supabase.com/dashboard
2. Dein Projekt öffnen: **fxreaveeihaawkusmybi**
3. Gehe zu: **Settings** → **API**
4. Suche die Zeile: **`service_role`** (nicht `anon`!)
5. Klicke auf "Reveal" und kopiere den Key
6. Format: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` (lange Zeichenkette)

⚠️ **WICHTIG:**
- Dieser Key ist **geheim** — niemals öffentlich machen
- Nur für Backend verwenden
- Speicher ihn nirgendwo anders!

---

### Schritt 2: Installation starten

```bash
# Terminal öffnen und folgendes ausführen:
bash ~/wtf/ebm/mactool/install.sh
```

Das Script wird dich fragen nach:
1. **Server Name** (z.B. `mac04`, `mac05`, etc.)
2. **Supabase Service Role Key** (den du oben kopiert hast)

---

### Schritt 3: Fertig!

Das Script macht alles automatisch:
- ✓ Homebrew installiert (wenn nötig)
- ✓ Python 3.12 installiert (wenn nötig)
- ✓ Virtual Environment erstellt
- ✓ Alle Dependencies installiert
- ✓ config.json generiert
- ✓ `~/Desktop/GramBotStorage/` Verzeichnis erstellt
- ✓ LaunchAgent für Autostart installiert
- ✓ Tool startet automatisch beim Login
- ✓ Web UI öffnet sich: http://localhost:8000

---

## Nach der Installation

### Web UI
```
http://localhost:8000
```

Zugriff auf:
- Dashboard (Status, Konfiguration)
- Device Management
- Manueller Sync Trigger
- Bot Management
- Config Editor

### Logs anschauen
```bash
tail -f ~/Applications/mactool/logs/mactool.log
```

### Installation überprüfen
```bash
# Ist der Service aktiv?
launchctl list | grep com.ebm.mactool

# Output sollte sein:
# PID    Status  Label
# 12345  0       com.ebm.mactool
```

---

## Wichtige Pfade nach Installation

| Pfad | Beschreibung |
|------|------------|
| `~/Applications/mactool/` | Installation |
| `~/Desktop/GramBotStorage/super.db` | SQLite Datenbankdatei (muss hier liegen!) |
| `~/Library/LaunchAgents/com.ebm.mactool.plist` | Autostart-Konfiguration |
| `~/Applications/mactool/logs/mactool.log` | Hauptlog-Datei |
| `~/Applications/mactool/config.json` | Konfiguration (verwaltet via Web UI) |

---

## Troubleshooting

### "Installation failed"

Stelle sicher dass:
- ✓ Internet-Verbindung aktiv
- ✓ Terminal-Berechtigungen OK
- ✓ Genug Speicherplatz

Versuche manuell:
```bash
# Homebrew überprüfen
brew doctor

# Python überprüfen
python3 --version

# Neu installieren
bash ~/wtf/ebm/mactool/install.sh
```

---

### "Web UI nicht erreichbar (http://localhost:8000)"

```bash
# Service starten
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist

# Warten 3 Sekunden
sleep 3

# Überprüfen
launchctl list | grep com.ebm.mactool

# Logs checken
tail -20 ~/Applications/mactool/logs/mactool.log
```

---

### "GramBotStorage Ordner nicht gefunden"

Das Script erstellt `~/Desktop/GramBotStorage/` automatisch.
Falls nicht:

```bash
mkdir -p ~/Desktop/GramBotStorage
```

Dann manuell `super.db` dort hinein kopieren.

---

### "Supabase Key ungültig"

Error: `SUPABASE_KEY not set` oder Sync funktioniert nicht?

1. Überprüfe den Key nochmal:
   - https://supabase.com/dashboard/project/fxreaveeihaawkusmybi/settings/api
   - Stelle sicher dass es der **service_role** Key ist!

2. Config aktualisieren:
   - Web UI: http://localhost:8000 → Config
   - Oder manuell: `~/Applications/mactool/config.json` editieren

3. Service neustarten:
```bash
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist
```

---

### "BotApp wird nicht gefunden"

Wenn der Bot nicht startet:

```bash
# Überprüfe ob BotApp installiert ist
ls /Applications/botapp.app

# Falls nicht gefunden, manuell in config.json die Pfad updaten:
# "bot_app_path": "/Pfad/zum/botapp.app/Contents/MacOS/BotApp"
```

---

## Auf mehreren Macs installieren

Für jeden Mac das gleiche Vorgehen:

1. `bash ~/wtf/ebm/mactool/install.sh`
2. Server Name eingeben (z.B. `mac04`, `mac05`)
3. Supabase Key eingeben (gleich für alle!)
4. Fertig!

Jeder Mac bekommt einen eindeutigen Server Name → wird dann in Supabase als Prefix verwendet.

---

## Deinstallation

Falls nötig:

```bash
# LaunchAgent entfernen (stoppt Autostart)
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist

# Installation löschen
rm -rf ~/Applications/mactool

# LaunchAgent Datei löschen
rm ~/Library/LaunchAgents/com.ebm.mactool.plist

# Logs löschen
rm -rf ~/Applications/mactool/logs
```

---

## Nächste Schritte

1. ✓ Installation komplett
2. ✓ Web UI auf http://localhost:8000 aufrufen
3. ✓ Dashboard anschauen
4. ✓ Logs überprüfen: `tail -f ~/Applications/mactool/logs/mactool.log`
5. ✓ Manueller Sync Test: Web UI → "Sync Now" Button

---

## Support

Logs anschauen für Details:
```bash
tail -50 ~/Applications/mactool/logs/mactool.log
```

Error? → Logs zeigen genau was falsch ist!
