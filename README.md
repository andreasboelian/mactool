# EBM Mactool — Production macOS Automation Backend

Vollständiges, produktionsreifes macOS-Backend-Tool für Bot-Automatisierung mit SQLite→Supabase Sync, ADB Device Monitoring, Bot.app Management und optional Web UI.

## Features

✅ **SQLite → Supabase Sync**
- Automatische Synchronisierung aller Datensätze (device, profile, stats)
- 90-Tage Filter + automatisches Cleanup
- Prefix-Injection für Multi-Tenant Support
- Batch UPSERT (500er Blöcke)
- Retry-Logik mit exponential backoff

✅ **Device Monitoring via ADB**
- Periodische ADB-Checks (konfigurierbar, default: 1h)
- Online/Offline Status Tracking mit State Cache
- n8n Webhook Notifications bei Offline
- Blacklist-Filter für ignorierte Geräte

✅ **Bot.app Management**
- Auto-Start wenn nicht läuft
- Periodische Health Checks (default: 5min)
- Restart-Funktion mit Python-Prozess Cleanup
- pgrep + ps Fallback Kompatibilität

✅ **Zeitgesteuerte Jobs (APScheduler)**
- Konfigurierbare Sync-Zeiten (z.B. 09:00, 14:30)
- Device Monitor: stündlich
- Bot Manager: alle 5 Minuten
- Graceful Shutdown mit SIGTERM/SIGINT

✅ **Lokale Konfiguration**
- JSON-basierte Config (config.json)
- Singleton Pattern mit Laufzeit-Updates
- Environment Variable Fallback (SUPABASE_KEY)

✅ **Optional: FastAPI Web UI**
- Dashboard mit System-Status
- Device-Verwaltung (Blacklist, Restart)
- Manueller Sync & Device Check Trigger
- Config-Editor
- Job Schedule Übersicht

## Installation

### Voraussetzungen
- Python 3.11+
- pip / venv

### Setup

```bash
# Clone/navigate zum Verzeichnis
cd mactool

# Virtual environment (optional aber empfohlen)
python3 -m venv venv
source venv/bin/activate

# Dependencies installieren
pip install -r requirements.txt

# Config erstellen
cp config.json.example config.json
# → config.json anpassen (SUPABASE_KEY, server_name, etc.)
```

## Konfiguration

### config.json

```json
{
  "server_name": "mac04",
  "sync_times": ["09:00", "14:30"],
  "blacklist": [],
  "supabase_url": "https://fxreaveeihaawkusmybi.supabase.co",
  "supabase_key": "YOUR_SERVICE_ROLE_KEY_HERE",
  "bot_app_path": "/Applications/botapp.app/Contents/MacOS/BotApp",
  "adb_path": "adb",
  "sqlite_db_path": "super.db",
  "webhook_url": "https://n8n.srv882018.hstgr.cloud/webhook/...",
  "device_check_interval_hours": 1,
  "bot_check_interval_minutes": 5,
  "log_level": "INFO"
}
```

### Environment Variables (Optional)

```bash
export SUPABASE_KEY="your-service-role-key"
export SERVER_NAME="mac04"
```

## Verwendung

### Daemon-Modus (Background)

```bash
python3 main.py
```

Logs: `logs/mactool.log` (Rotating, 5MB, 3 Backups)

### Mit Web UI

```bash
python3 main.py --web-ui --web-port 8000
```

Öffne: http://localhost:8000

### One-Shot Commands

```bash
# Nur Sync ausführen und beenden
python3 main.py --sync

# Geräte checken und beenden
python3 main.py --check-devices

# Bot neustarten und beenden
python3 main.py --bot-restart

# Debug Logging
python3 main.py --debug
```

## API Endpoints (bei Web UI)

### Dashboard & Status

| Method | Path | Beschreibung |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/status` | System Status JSON |

### Sync

| Method | Path | Beschreibung |
|--------|------|-------------|
| POST | `/api/sync` | Manueller Sync Trigger |

### Devices

| Method | Path | Beschreibung |
|--------|------|-------------|
| GET | `/api/devices` | Alle Geräte + Status |
| POST | `/api/devices/check` | Device Monitor Job |
| POST | `/api/devices/{id}/restart` | Gerät neustarten |
| POST | `/api/devices/{id}/blacklist` | Zur Blacklist hinzufügen |
| DELETE | `/api/devices/{id}/blacklist` | Von Blacklist entfernen |

### Config

| Method | Path | Beschreibung |
|--------|------|-------------|
| GET | `/api/config` | Aktuelle Config |
| PUT | `/api/config` | Config updaten |

### Bot

| Method | Path | Beschreibung |
|--------|------|-------------|
| POST | `/api/bot/start` | Bot starten |
| POST | `/api/bot/stop` | Bot stoppen |
| POST | `/api/bot/restart` | Bot neustarten |

## Architektur

```
mactool/
├── config.py           # Config-Management (Singleton)
├── sync.py             # SQLite → Supabase Sync
├── device_monitor.py   # ADB Monitoring + Webhooks
├── bot_manager.py      # Bot.app Start/Stop/Restart
├── scheduler.py        # APScheduler (Cron + Interval)
├── api.py              # FastAPI Web UI (optional)
├── main.py             # Entry Point + Logging + Signals
├── requirements.txt    # Python Dependencies
├── config.json         # Lokale Konfiguration
└── logs/               # Rotating Log Files
```

## Fehlerbehandlung

### Sync Fehler
- **Retry-Logik**: 3 Versuche mit exponential backoff (2^n Sekunden)
- **Temp DB**: Garantiert keine Lock-Konflikte mit laufender App
- **Cleanup-Fehler**: Werden geloggt, stoppen nicht den Sync

### Device Monitor
- **ADB Timeout**: 5s Timeout pro Device, graceful fallback
- **Webhook Fehler**: 3 Retries mit backoff
- **State Cache**: Verhindert Webhook-Spam bei flaky Devices

### Bot Manager
- **Start Fehler**: Wird alle 5min erneut versucht
- **Graceful Shutdown**: SIGTERM/SIGINT Handler

## Logging

```
logs/mactool.log          # Main log file (rotating)
logs/mactool.log.1        # Backup 1 (5MB rotierend)
logs/mactool.log.2        # Backup 2
logs/mactool.log.3        # Backup 3
```

Log Level: `INFO` (default), `DEBUG` mit `--debug`

Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

## Performance

- **Batch UPSERT**: 500 Records pro Supabase Request
- **Temp DB**: RO Copy verhindert Locks
- **Device State Cache**: In-Memory, verhindert Webhook-Spam
- **Scheduler**: Non-blocking Background Jobs

## Systemanforderungen

| Komponente | Anforderung |
|-----------|-----------|
| Python | 3.11+ |
| SQLite | Im Code (Kopie aus super.db) |
| ADB | Optional (für Device Check) |
| Supabase | URL + Service Role Key |
| macOS | 10.13+ |

## Troubleshooting

### Supabase Key nicht gesetzt

```
SUPABASE_KEY not set in environment or config.json
```

→ Setze `supabase_key` in config.json oder `SUPABASE_KEY` env var.

### ADB nicht gefunden

```
ADB not found. Ensure it's installed and in PATH.
```

→ Device Monitor ignoriert offline Devices graceful.
→ Installiere Android SDK Platform Tools wenn nötig.

### BotApp nicht starten

```
BotApp not found at /Applications/botapp.app/Contents/MacOS/BotApp
```

→ Überprüfe `bot_app_path` in config.json.

### Webhook Fehler

→ Überprüfe n8n URL in config.json.
→ Logs zeigen jeden Retry mit Timestamp.

## Lizenz

Internal Tool — EBM

## Support

Siehe `logs/mactool.log` für Details.
