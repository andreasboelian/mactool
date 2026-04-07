# Changelog

## v1.0.107 — 2026-04-07
- Log-Retention von 90 auf 3 Tage reduziert
- Log-Cleanup läuft nur noch 1x pro Tag (nicht mehr bei jedem Sync)
- Log-Upload Throttling verschärft (5er Batches, 0.5s/5s Pausen)
- Rate-Limit Retry für einzelne Log-Uploads (1 Retry bei 429/503/Timeout)
- "Sync Now" Button lädt ALLE Phone-Logs hoch (nicht nur den letzten Timeslot); Auto-Sync bleibt unverändert
- device_monitor.py: Singleton Supabase-Client statt create_client() pro Aufruf
- device_monitor.py: adb_status Updates gebatched (max. 2 Requests statt 1 pro Device)
- sync.py: Bin-Table Stale-Cleanup batched via .in_() statt Loop

## v1.0.101 — 2026-03-25
- Versionierung: Git-Tags statt SHA, CHANGELOG.md
- Dashboard: Versionsanzeige mit Tag-Namen (z.B. v1.0.100)
- Dashboard: Version-Dropdown zum Wechseln auf ältere Versionen
- Update-System: Tag-basiert statt origin/main

## v1.0.100 — 2026-03-25
- Batched Webhook (ein gesammelter Webhook pro Monitor-Durchlauf)
- Reported-Status im Dashboard mit Reset-Button
- Supabase adb_status Spalte für externe Statusabfrage
- Externer Reset via Supabase (adb_status auf "online" setzen)
- Device Restart (einzeln + Restart All) via ADB reboot
- Bot Start/Stop mit Auto-Restart und Login-Shell für ADB-Zugriff
- Service-Restart nach Update via launchd KeepAlive
- Self-Update von GitHub mit Update-Button
- SQLite zu Supabase Sync Engine mit Schema-Discovery
- Device Monitor mit Webhook-Benachrichtigung bei Offline-Transition
- Blacklist-Verwaltung im Dashboard
- Multi-Mac Isolation (Supabase ID-Prefix Filterung)
