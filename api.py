"""FastAPI web interface for mactool."""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_config, reload_config
from sync import trigger_sync
from device_monitor import (
    get_adb_devices,
    get_devices_from_db,
    restart_adb_device,
    run_device_monitor_job,
    get_device_state,
    reset_device_reported,
    reset_all_reported,
)
from bot_manager import is_bot_running, start_bot, stop_bot, restart_bot, is_auto_restart_enabled
from updater import check_for_updates, perform_update
from scheduler import get_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(title="EBM Mactool API", version="1.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic models
class ConfigUpdate(BaseModel):
    """Configuration update model."""

    server_name: str | None = None
    sync_times: list[str] | None = None
    blacklist: list[str] | None = None


class DeviceAction(BaseModel):
    """Device action model."""

    device_id: str


class BlacklistAction(BaseModel):
    """Blacklist action model."""

    device_id: str


# Routes


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Get dashboard HTML."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EBM Mactool Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 20px; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #333; margin-bottom: 20px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }
            .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
            .card h2 { font-size: 18px; margin-bottom: 15px; color: #333; }
            .status { display: flex; justify-content: space-between; align-items: center; margin: 10px 0; }
            .status-value { font-weight: bold; color: #0066cc; }
            .btn { background: #0066cc; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; margin-right: 10px; margin-top: 10px; }
            .btn:hover { background: #0052a3; }
            .btn:disabled { background: #ccc; cursor: not-allowed; }
            .btn-danger { background: #d32f2f; }
            .btn-danger:hover { background: #b71c1c; }
            .btn-small { padding: 5px 10px; font-size: 12px; margin-right: 5px; }
            .table { width: 100%; border-collapse: collapse; margin-top: 10px; }
            .table th, .table td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
            .table th { background: #f0f0f0; font-weight: bold; }
            .online { color: #4caf50; font-weight: bold; }
            .offline { color: #f44336; font-weight: bold; }
            .status-msg { padding: 10px; margin-top: 10px; border-radius: 4px; font-size: 13px; }
            .status-msg.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
            .status-msg.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
            .status-msg.loading { background: #e7f3ff; color: #0066cc; border: 1px solid #b3d9ff; }
            .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #0066cc; border-top-color: transparent; border-radius: 50%; animation: spin 0.6s linear infinite; }
            @keyframes spin { to { transform: rotate(360deg); } }
            .sync-times-edit { margin-top: 10px; }
            .sync-times-edit input { padding: 8px; font-size: 14px; margin-right: 5px; border: 1px solid #ddd; border-radius: 4px; width: 80%; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 EBM Mactool Dashboard</h1>

            <div class="grid">
                <div class="card">
                    <h2>System Status</h2>
                    <div class="status">
                        <span>Bot App:</span>
                        <span class="status-value" id="bot-status">Loading...</span>
                    </div>
                    <div class="status">
                        <span>Auto-Restart:</span>
                        <span class="status-value" id="auto-restart-status">Loading...</span>
                    </div>
                    <div class="status">
                        <span>Scheduler:</span>
                        <span class="status-value" id="scheduler-status">Loading...</span>
                    </div>
                    <button class="btn" id="sync-btn" onclick="triggerSync()">Sync Now</button>
                    <button class="btn" id="device-check-btn" onclick="triggerDeviceCheck()">Check Devices</button>
                    <button class="btn" id="bot-start-btn" onclick="botStart()" style="background:#4caf50;">Start Bot</button>
                    <button class="btn btn-danger" id="bot-stop-btn" onclick="botStop()">Stop Bot</button>
                    <button class="btn" id="update-btn" onclick="triggerUpdate()" style="background:#ff9800;">Update</button>
                    <div id="sync-status"></div>
                    <div id="device-check-status"></div>
                    <div id="bot-status-msg"></div>
                    <div id="update-status"></div>
                </div>

                <div class="card">
                    <h2>Configuration</h2>
                    <div class="status">
                        <span>Version:</span>
                        <span class="status-value" id="app-version" style="font-size:12px;">-</span>
                    </div>
                    <div class="status">
                        <span>Server:</span>
                        <span class="status-value" id="server-name">Loading...</span>
                    </div>
                    <div class="status">
                        <span>Sync Times:</span>
                        <span class="status-value" id="sync-times">Loading...</span>
                    </div>
                    <button class="btn btn-small" onclick="editSyncTimes()">Edit</button>
                    <div id="sync-times-editor" class="sync-times-edit" style="display:none;">
                        <input type="text" id="sync-times-input" placeholder="e.g., 09:00,14:30" />
                        <button class="btn btn-small" onclick="saveSyncTimes()">Save</button>
                        <button class="btn btn-small" onclick="cancelSyncTimes()" style="background:#999;">Cancel</button>
                        <div id="sync-times-msg"></div>
                    </div>
                </div>

                <div class="card">
                    <h2>Job Schedule</h2>
                    <div id="jobs-list" style="font-size: 12px;">Loading...</div>
                </div>
            </div>

            <div class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <h2>Devices</h2>
                    <div>
                        <button class="btn btn-small" onclick="resetAllReported()" style="background:#ff9800;">Reset All Reported</button>
                        <button class="btn" onclick="restartAllDevices()" style="background:#e65100;">Restart All</button>
                    </div>
                </div>
                <div style="margin-bottom:10px;">
                    <strong>Blacklist:</strong> <span id="blacklist-display" style="font-size:12px;color:#666;">Loading...</span>
                </div>
                <table class="table">
                    <thead>
                        <tr>
                            <th>Serial</th>
                            <th>Name</th>
                            <th>Status</th>
                            <th>Reported</th>
                            <th>Blacklist</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="devices-tbody">
                        <tr><td colspan="6" style="text-align: center;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            let currentSyncTimes = [];

            async function loadStatus() {
                try {
                    const resp = await fetch('/api/status');
                    const data = await resp.json();

                    document.getElementById('bot-status').textContent = data.bot_running ? '✓ Running' : '✗ Stopped';
                    document.getElementById('bot-status').style.color = data.bot_running ? '#4caf50' : '#f44336';

                    const arEl = document.getElementById('auto-restart-status');
                    arEl.textContent = data.auto_restart ? '✓ Active' : '✗ Disabled';
                    arEl.style.color = data.auto_restart ? '#4caf50' : '#f44336';

                    // Show/hide start/stop buttons based on state
                    document.getElementById('bot-start-btn').style.display = data.bot_running ? 'none' : '';
                    document.getElementById('bot-stop-btn').style.display = data.bot_running ? '' : 'none';

                    document.getElementById('scheduler-status').textContent = '✓ Running';
                    document.getElementById('server-name').textContent = data.server_name;
                    currentSyncTimes = data.sync_times;
                    document.getElementById('sync-times').textContent = data.sync_times.join(', ');

                    // Show version info
                    if (data.version) {
                        const vEl = document.getElementById('app-version');
                        vEl.textContent = data.version;
                        if (data.update_available) {
                            vEl.textContent += ' (update available)';
                            vEl.style.color = '#ff9800';
                        }
                    }

                    if (data.jobs) {
                        let html = '';
                        for (const job of data.jobs) {
                            const nextTime = job.next_run_time ? new Date(job.next_run_time).toLocaleString() : 'N/A';
                            html += `<div style="margin-bottom: 8px;"><strong>${job.name}</strong><br/>Next: ${nextTime}</div>`;
                        }
                        document.getElementById('jobs-list').innerHTML = html;
                    }
                } catch (e) {
                    console.error('Failed to load status:', e);
                }
            }

            async function loadDevices() {
                try {
                    const resp = await fetch('/api/devices');
                    const devices = await resp.json();

                    const tbody = document.getElementById('devices-tbody');
                    if (!devices || devices.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No devices</td></tr>';
                        return;
                    }

                    // Show blacklist summary
                    const blDisplay = document.getElementById('blacklist-display');
                    const blacklisted = devices.filter(d => d.blacklisted).map(d => d.serial);
                    blDisplay.textContent = blacklisted.length > 0
                        ? blacklisted.join(', ')
                        : '(none)';

                    let html = '';
                    for (const dev of devices) {
                        const status = dev.status || 'unknown';
                        const statusClass = status === 'online' ? 'online' : (status === 'unknown' ? '' : 'offline');
                        const blClass = dev.blacklisted ? 'style="opacity:0.5;"' : '';
                        const blBtn = dev.blacklisted
                            ? `<button class="btn btn-small" onclick="toggleBlacklist('${dev.serial}', false)" style="background:#4caf50;">Unblock</button>`
                            : `<button class="btn btn-small" onclick="toggleBlacklist('${dev.serial}', true)" style="background:#999;">Block</button>`;
                        const nameShort = (dev.name || '-').substring(0, 60);
                        const reportedCell = dev.reported
                            ? `<span style="color:#f44336;font-weight:bold;">Yes</span> <button class="btn btn-small" onclick="resetReported('${dev.serial}')" style="background:#ff9800;font-size:10px;">Reset</button>`
                            : `<span style="color:#999;">No</span>`;
                        html += `
                            <tr ${blClass}>
                                <td style="font-family:monospace;font-size:12px;">${dev.serial || dev.id}</td>
                                <td style="font-size:12px;">${nameShort}</td>
                                <td class="${statusClass}">${status}${dev.blacklisted ? ' (blocked)' : ''}</td>
                                <td>${reportedCell}</td>
                                <td>${blBtn}</td>
                                <td>
                                    <button class="btn btn-small" onclick="restartDevice('${dev.serial || dev.id}')">Restart</button>
                                </td>
                            </tr>
                        `;
                    }
                    tbody.innerHTML = html;
                } catch (e) {
                    console.error('Failed to load devices:', e);
                }
            }

            async function triggerSync() {
                const btn = document.getElementById('sync-btn');
                const statusDiv = document.getElementById('sync-status');

                btn.disabled = true;
                statusDiv.innerHTML = '<div class="status-msg loading"><span class="spinner"></span> Syncing...</div>';

                try {
                    const resp = await fetch('/api/sync', { method: 'POST' });
                    const result = await resp.json();

                    if (resp.ok) {
                        let msg = '';
                        let hasError = false;
                        if (result.tables) {
                            const parts = Object.entries(result.tables).map(([k, v]) => {
                                if (v.status === 'error') {
                                    hasError = true;
                                    return `${k}=ERROR: ${v.error || 'unknown'}`;
                                }
                                return `${k}=${v.count !== undefined ? v.count : v.status}`;
                            });
                            msg = parts.join(', ');
                        }
                        if (hasError || result.status === 'partial_error') {
                            statusDiv.innerHTML = `<div class="status-msg error">Sync partial: ${msg}</div>`;
                        } else {
                            statusDiv.innerHTML = `<div class="status-msg success">Sync OK: ${msg}</div>`;
                        }
                    } else {
                        statusDiv.innerHTML = `<div class="status-msg error">Sync error: ${result.error || result.detail || 'Unknown'}</div>`;
                    }
                    loadStatus();
                } catch (e) {
                    statusDiv.innerHTML = `<div class="status-msg error">Sync failed: ${e.message}</div>`;
                } finally {
                    btn.disabled = false;
                    setTimeout(() => { statusDiv.innerHTML = ''; }, 15000);
                }
            }

            async function triggerDeviceCheck() {
                const btn = document.getElementById('device-check-btn');
                const statusDiv = document.getElementById('device-check-status');

                btn.disabled = true;
                statusDiv.innerHTML = '<div class="status-msg loading"><span class="spinner"></span> Checking...</div>';

                try {
                    const resp = await fetch('/api/devices/check', { method: 'POST' });
                    const result = await resp.json();

                    if (resp.ok) {
                        if (result.status === 'no_devices') {
                            statusDiv.innerHTML = `<div class="status-msg success">No devices in database</div>`;
                        } else if (result.status === 'adb_not_found') {
                            statusDiv.innerHTML = `<div class="status-msg error">ADB not found. Install Android SDK or set adb_path in config.json</div>`;
                        } else if (result.status === 'error') {
                            statusDiv.innerHTML = `<div class="status-msg error">Monitor error: ${result.error || ''}</div>`;
                        } else {
                            const on = result.online || 0;
                            const off = result.offline || 0;
                            const bl = result.blacklisted || 0;
                            statusDiv.innerHTML = `<div class="status-msg success">Checked ${result.checked || 0}: online=${on}, offline=${off}, blacklisted=${bl}</div>`;
                        }
                    } else {
                        statusDiv.innerHTML = `<div class="status-msg error">Check error</div>`;
                    }
                    loadDevices();
                } catch (e) {
                    statusDiv.innerHTML = `<div class="status-msg error">Check failed: ${e.message}</div>`;
                } finally {
                    btn.disabled = false;
                    setTimeout(() => { statusDiv.innerHTML = ''; }, 15000);
                }
            }

            function editSyncTimes() {
                document.getElementById('sync-times').style.display = 'none';
                document.getElementById('sync-times-editor').style.display = 'block';
                document.getElementById('sync-times-input').value = currentSyncTimes.join(',');
            }

            function cancelSyncTimes() {
                document.getElementById('sync-times-editor').style.display = 'none';
                document.getElementById('sync-times').style.display = 'block';
                document.getElementById('sync-times-msg').innerHTML = '';
            }

            async function saveSyncTimes() {
                const input = document.getElementById('sync-times-input').value;
                const times = input.split(',').map(t => t.trim()).filter(t => t);
                const msgDiv = document.getElementById('sync-times-msg');

                if (times.length === 0) {
                    msgDiv.innerHTML = '<div class="status-msg error">✗ At least one time required</div>';
                    return;
                }

                try {
                    const resp = await fetch('/api/config', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ sync_times: times })
                    });

                    if (resp.ok) {
                        msgDiv.innerHTML = '<div class="status-msg success">✓ Saved! Service will reload...</div>';
                        setTimeout(() => {
                            loadStatus();
                            cancelSyncTimes();
                        }, 1000);
                    } else {
                        msgDiv.innerHTML = '<div class="status-msg error">✗ Save failed</div>';
                    }
                } catch (e) {
                    msgDiv.innerHTML = `<div class="status-msg error">✗ Error: ${e.message}</div>`;
                }
            }

            async function toggleBlacklist(serial, add) {
                try {
                    const method = add ? 'POST' : 'DELETE';
                    const resp = await fetch(`/api/devices/${serial}/blacklist`, { method });
                    if (resp.ok) {
                        loadDevices();
                    } else {
                        alert('Failed to update blacklist');
                    }
                } catch (e) {
                    alert('Error: ' + e.message);
                }
            }

            async function restartDevice(deviceId) {
                if (!confirm('Restart device ' + deviceId + '?')) return;
                try {
                    const resp = await fetch(`/api/devices/${deviceId}/restart`, { method: 'POST' });
                    if (resp.ok) {
                        alert('✓ Reboot command sent to ' + deviceId);
                        setTimeout(loadDevices, 5000);
                    } else {
                        const err = await resp.json().catch(() => ({}));
                        alert('✗ Restart failed: ' + (err.detail || 'unknown error'));
                    }
                } catch (e) {
                    alert('✗ Restart failed: ' + e.message);
                }
            }

            async function restartAllDevices() {
                if (!confirm('Restart ALL online devices?')) return;
                try {
                    const resp = await fetch('/api/devices/restart-all', { method: 'POST' });
                    const result = await resp.json();
                    if (result.status === 'no_devices') {
                        alert('No online devices found');
                    } else {
                        alert(`✓ Restarted ${result.restarted}/${result.total} devices`);
                        setTimeout(loadDevices, 5000);
                    }
                } catch (e) {
                    alert('✗ Restart all failed: ' + e.message);
                }
            }

            async function resetReported(serial) {
                try {
                    const resp = await fetch(`/api/devices/${serial}/reset-reported`, { method: 'POST' });
                    if (resp.ok) loadDevices();
                    else alert('Failed to reset reported status');
                } catch (e) {
                    alert('Error: ' + e.message);
                }
            }

            async function resetAllReported() {
                if (!confirm('Reset reported status for all devices? They will be re-reported if still offline.')) return;
                try {
                    const resp = await fetch('/api/devices/reset-all-reported', { method: 'POST' });
                    if (resp.ok) {
                        const result = await resp.json();
                        alert(`Reset ${result.count} device(s)`);
                        loadDevices();
                    } else {
                        alert('Failed to reset');
                    }
                } catch (e) {
                    alert('Error: ' + e.message);
                }
            }

            async function botStart() {
                const msgDiv = document.getElementById('bot-status-msg');
                msgDiv.innerHTML = '<div class="status-msg loading"><span class="spinner"></span> Starting Bot...</div>';
                try {
                    const resp = await fetch('/api/bot/start', { method: 'POST' });
                    const result = await resp.json();
                    if (result.running) {
                        msgDiv.innerHTML = '<div class="status-msg success">Bot started. Auto-restart enabled.</div>';
                    } else {
                        msgDiv.innerHTML = '<div class="status-msg error">Bot failed to start.</div>';
                    }
                    setTimeout(loadStatus, 1000);
                } catch (e) {
                    msgDiv.innerHTML = `<div class="status-msg error">Start failed: ${e.message}</div>`;
                }
                setTimeout(() => { msgDiv.innerHTML = ''; }, 10000);
            }

            async function botStop() {
                if (!confirm('Stop Bot? Auto-restart will be disabled until you click Start again.')) return;
                const msgDiv = document.getElementById('bot-status-msg');
                msgDiv.innerHTML = '<div class="status-msg loading"><span class="spinner"></span> Stopping Bot...</div>';
                try {
                    const resp = await fetch('/api/bot/stop', { method: 'POST' });
                    const result = await resp.json();
                    if (!result.running) {
                        msgDiv.innerHTML = '<div class="status-msg success">Bot stopped. Auto-restart disabled.</div>';
                    } else {
                        msgDiv.innerHTML = '<div class="status-msg error">Bot still running.</div>';
                    }
                    setTimeout(loadStatus, 1000);
                } catch (e) {
                    msgDiv.innerHTML = `<div class="status-msg error">Stop failed: ${e.message}</div>`;
                }
                setTimeout(() => { msgDiv.innerHTML = ''; }, 10000);
            }

            async function triggerUpdate() {
                if (!confirm('Update from GitHub? Service will restart after update.')) return;
                const btn = document.getElementById('update-btn');
                const statusDiv = document.getElementById('update-status');

                btn.disabled = true;
                statusDiv.innerHTML = '<div class="status-msg loading"><span class="spinner"></span> Updating...</div>';

                try {
                    const resp = await fetch('/api/update', { method: 'POST' });
                    const result = await resp.json();

                    if (result.status === 'success') {
                        statusDiv.innerHTML = `<div class="status-msg success">Updated to ${result.version}. Restarting...</div>`;
                        setTimeout(() => { location.reload(); }, 5000);
                    } else {
                        statusDiv.innerHTML = `<div class="status-msg error">Update failed: ${result.error || 'unknown'}</div>`;
                    }
                } catch (e) {
                    statusDiv.innerHTML = `<div class="status-msg error">Update error: ${e.message}</div>`;
                } finally {
                    btn.disabled = false;
                    setTimeout(() => { statusDiv.innerHTML = ''; }, 15000);
                }
            }

            // Load data on page load and refresh every 30s
            loadStatus();
            loadDevices();
            setInterval(loadStatus, 30000);
            setInterval(loadDevices, 30000);
        </script>
    </body>
    </html>
    """


@app.get("/api/status")
async def get_status():
    """Get system status."""
    try:
        config = get_config()
        scheduler = get_scheduler()

        update_info = check_for_updates()

        return {
            "server_name": config.server_name,
            "bot_running": is_bot_running(),
            "auto_restart": is_auto_restart_enabled(),
            "sync_times": config.sync_times,
            "jobs": scheduler.get_jobs(),
            "version": update_info.get("current") or update_info.get("version", "-"),
            "update_available": update_info.get("status") == "update_available",
        }
    except Exception as e:
        logger.error(f"Failed to get status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sync")
async def sync_now():
    """Trigger sync immediately."""
    try:
        result = trigger_sync()
        return result
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/devices")
async def get_devices():
    """Get all devices: merge DB profiles + live ADB connections."""
    try:
        config = get_config()
        blacklist = set(config.blacklist)
        db_devices = get_devices_from_db()
        device_state = get_device_state()
        adb_online = get_adb_devices()  # set of serials currently connected

        # Index DB devices by serial
        seen_serials: set[str] = set()
        result = []
        for device in db_devices:
            serial = device["serial"]
            seen_serials.add(serial)

            if serial in adb_online:
                device["status"] = "online"
                device["reported"] = False
            else:
                state_entry = device_state.get(serial) or device_state.get(device["id"])
                if isinstance(state_entry, dict):
                    device["status"] = state_entry.get("status", "unknown")
                    device["reported"] = state_entry.get("reported", False)
                elif isinstance(state_entry, str):
                    device["status"] = state_entry
                    device["reported"] = state_entry == "offline"
                else:
                    device["status"] = "unknown"
                    device["reported"] = False

            device["blacklisted"] = serial in blacklist or device["id"] in blacklist
            result.append(device)

        # Add ADB devices not in DB
        for serial in sorted(adb_online):
            if serial not in seen_serials:
                result.append({
                    "id": serial,
                    "name": "(not in database)",
                    "serial": serial,
                    "status": "online",
                    "reported": False,
                    "blacklisted": serial in blacklist,
                })

        return result
    except Exception as e:
        logger.error(f"Failed to get devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/check")
async def check_devices():
    """Check all devices."""
    try:
        result = run_device_monitor_job()
        return result
    except Exception as e:
        logger.error(f"Device check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/restart-all")
async def restart_all_devices():
    """Restart all online ADB devices."""
    import asyncio
    try:
        online = await asyncio.to_thread(get_adb_devices)
        if not online:
            return {"status": "no_devices", "restarted": 0}

        results = {}
        for serial in sorted(online):
            success = await asyncio.to_thread(restart_adb_device, serial)
            results[serial] = "ok" if success else "failed"

        ok_count = sum(1 for v in results.values() if v == "ok")
        return {"status": "done", "restarted": ok_count, "total": len(online), "details": results}
    except Exception as e:
        logger.error(f"Restart all devices failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/reset-all-reported")
async def reset_all_reported_endpoint():
    """Reset reported flag for all devices."""
    count = reset_all_reported()
    return {"status": "reset", "count": count}


@app.post("/api/devices/{device_id}/reset-reported")
async def reset_device_reported_endpoint(device_id: str):
    """Reset reported flag for a single device."""
    success = reset_device_reported(device_id)
    if success:
        return {"status": "reset", "device_id": device_id}
    raise HTTPException(status_code=404, detail="Device not found in state cache")


@app.post("/api/devices/{device_id}/restart")
async def restart_device(device_id: str):
    """Restart a specific device via ADB reboot."""
    import asyncio
    try:
        success = await asyncio.to_thread(restart_adb_device, device_id)
        if success:
            return {"status": "restart_initiated", "device_id": device_id}
        else:
            raise HTTPException(status_code=500, detail="ADB reboot failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Device restart failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/devices/{device_id}/blacklist")
async def add_to_blacklist(device_id: str):
    """Add device to blacklist."""
    try:
        config = get_config()
        if device_id not in config.blacklist:
            config.blacklist.append(device_id)
            config.save()
            logger.info(f"Added {device_id} to blacklist")

        return {"status": "added", "device_id": device_id, "blacklist": config.blacklist}
    except Exception as e:
        logger.error(f"Blacklist operation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/devices/{device_id}/blacklist")
async def remove_from_blacklist(device_id: str):
    """Remove device from blacklist."""
    try:
        config = get_config()
        if device_id in config.blacklist:
            config.blacklist.remove(device_id)
            config.save()
            logger.info(f"Removed {device_id} from blacklist")

        return {"status": "removed", "device_id": device_id, "blacklist": config.blacklist}
    except Exception as e:
        logger.error(f"Blacklist operation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config_endpoint():
    """Get current configuration."""
    try:
        config = get_config()
        return {
            "server_name": config.server_name,
            "sync_times": config.sync_times,
            "blacklist": config.blacklist,
            "bot_app_path": config.bot_app_path,
        }
    except Exception as e:
        logger.error(f"Failed to get config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    """Update configuration."""
    try:
        config = get_config()

        if update.server_name is not None:
            config.server_name = update.server_name
        if update.sync_times is not None:
            config.sync_times = update.sync_times
        if update.blacklist is not None:
            config.blacklist = update.blacklist

        config.save()
        logger.info("Configuration updated")

        return {
            "status": "updated",
            "config": {
                "server_name": config.server_name,
                "sync_times": config.sync_times,
                "blacklist": config.blacklist,
            },
        }
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/restart")
async def restart_bot_endpoint():
    """Restart Bot.app."""
    import asyncio
    try:
        success = await asyncio.to_thread(restart_bot)
        return {
            "status": "success" if success else "failed",
            "running": is_bot_running(),
        }
    except Exception as e:
        logger.error(f"Bot restart failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/start")
async def start_bot_endpoint():
    """Start Bot.app."""
    import asyncio
    try:
        success = await asyncio.to_thread(start_bot)
        return {
            "status": "success" if success else "failed",
            "running": is_bot_running(),
        }
    except Exception as e:
        logger.error(f"Bot start failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/stop")
async def stop_bot_endpoint():
    """Stop Bot.app."""
    import asyncio
    try:
        success = await asyncio.to_thread(stop_bot)
        return {
            "status": "success" if success else "failed",
            "running": is_bot_running(),
        }
    except Exception as e:
        logger.error(f"Bot stop failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/update/check")
async def check_update():
    """Check if an update is available."""
    return check_for_updates()


@app.post("/api/update")
async def do_update():
    """Pull latest code from GitHub and restart."""
    try:
        result = perform_update()
        return result
    except Exception as e:
        logger.error(f"Update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
