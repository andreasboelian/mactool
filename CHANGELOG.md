# Changelog

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
