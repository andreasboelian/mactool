# Quick Deployment Guide - Column Mapping Fix

## Files Changed/Added

**Updated:**
- `sync.py` - Added intelligent column filtering

**New:**
- `diagnose_columns.py` - Column coverage diagnostic
- `test_sync.py` - Full sync test with logging
- `COLUMN_MAPPING.md` - Detailed documentation
- `FIX_SUMMARY.md` - Complete fix overview
- `DEPLOY.md` - This file

## Deploy to mac17

### Method 1: Direct File Copy (Fastest)

```bash
# From your machine:
scp ~/wtf/ebm/mactool/sync.py mac17:~/Applications/mactool/

# Restart service on mac17:
ssh mac17 'launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist && sleep 1 && launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist'
```

### Method 2: Copy All Files

```bash
# From your machine:
scp ~/wtf/ebm/mactool/*.py mac17:~/Applications/mactool/

# Restart on mac17:
ssh mac17 'launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist && sleep 1 && launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist'
```

## Verify on mac17

### Quick Test (30 seconds)

```bash
# SSH to mac17
ssh mac17

# Run test
cd ~/Applications/mactool
./venv/bin/python3 test_sync.py
```

Expected output:
```
status: "success"
tables:
  device: count 25
  profile: count 117  ← Should work now (was failing before)
  stats: count 18171
column_filtering:
  profile: 162  ← Shows columns that were synced
```

### Via Web UI (also 30 seconds)

1. Open http://mac17:8000
2. Click "Sync Now"
3. Should show: ✓ Sync completed (not error anymore)

### Full Diagnostic (if needed)

```bash
ssh mac17
cd ~/Applications/mactool
./venv/bin/python3 diagnose_columns.py
```

Shows which columns exist in Supabase vs SQLite.

## What Gets Fixed

### Before
```
Click "Sync Now" →
  device: ✓ 25 records
  profile: ✗ ERROR
  stats: ✓ 18171 records
```

### After
```
Click "Sync Now" →
  device: ✓ 25 records
  profile: ✓ 117 records (with column filtering)
  stats: ✓ 18171 records
```

## Check Logs

After deployment, check if it's working:

```bash
ssh mac17
tail -20 ~/Applications/mactool/logs/mactool.log
```

Look for:
- ✓ "Sync completed successfully"
- Column filtering info (if columns were removed)

## Rollback (if needed)

```bash
ssh mac17
cd ~/Applications/mactool

# Restore original file from git (if available)
git checkout sync.py

# Or manually restore from backup
# Then restart service:
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist
```

## Need More Info?

- **Technical details**: See `FIX_SUMMARY.md`
- **Column mapping details**: See `COLUMN_MAPPING.md`
- **Test on mac17**: Run `test_sync.py` or `diagnose_columns.py`

## Support Commands

These work on mac17 after deployment:

```bash
# Full test with logging
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/test_sync.py

# Diagnostic (tests each column)
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/diagnose_columns.py

# Manual sync (same as "Sync Now" button)
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/main.py --sync

# Check status
launchctl list | grep com.ebm.mactool

# View logs
tail -50 ~/Applications/mactool/logs/mactool.log
```
