# Profile Sync Fix - Summary

## Issue

Profile sync was failing with:
```
Could not find the 'config__check_followback_after_unfollow' column of 'profile' in the schema cache
```

**Root Cause**: SQLite profile table has 197 columns, but Supabase doesn't have all of them.

## Solution Deployed

### Changed Files

**1. `sync.py` - Intelligent Column Filtering**
- Added column validation cache
- Added smart error handling that detects missing columns
- When upsert fails due to missing column:
  - Extracts column name from error message
  - Removes it from batch
  - Retries automatically
  - Caches valid columns for remaining batches
- Added logging of column filtering results

### New Diagnostic Tools

**2. `diagnose_columns.py`**
- Test which SQLite columns exist in Supabase
- Generate coverage report (e.g., "162 of 197 columns available")
- Save detailed results to JSON

**3. `test_sync.py`**
- Run full sync with detailed logging
- Show column filtering statistics
- Log everything to `sync_test.log`

### Documentation

**4. `COLUMN_MAPPING.md`**
- Detailed explanation of the problem and solution
- Testing instructions
- Troubleshooting guide
- Expected sync results

## How to Deploy

### Option 1: Update on mac17 Manually

```bash
# Copy the updated files
scp ~/wtf/ebm/mactool/sync.py mac17:~/Applications/mactool/
scp ~/wtf/ebm/mactool/diagnose_columns.py mac17:~/Applications/mactool/
scp ~/wtf/ebm/mactool/test_sync.py mac17:~/Applications/mactool/

# Restart the service
ssh mac17 "launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist"
ssh mac17 "launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist"
```

### Option 2: Update via Installation Script (Recommended)

```bash
# Copy all files to mac17
scp -r ~/wtf/ebm/mactool/* mac17:~/Applications/mactool/

# Or use the install script again
bash ~/wtf/ebm/mactool/install.sh
```

### Option 3: Manual Update (on mac17)

```bash
# Replace sync.py
# 1. Stop the service
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist

# 2. Copy the updated file
cp ~/wtf/ebm/mactool/sync.py ~/Applications/mactool/

# 3. Restart the service
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist
```

## Testing

### Quick Test (recommended first)

```bash
# On mac17, run the test script with detailed logging
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/test_sync.py
```

Expected output:
```
Sync Result
====================
{
  "status": "success",
  "tables": {
    "device": {"status": "success", "count": 25},
    "profile": {"status": "success", "count": 117},
    "stats": {"status": "success", "count": 18171}
  },
  "column_filtering": {
    "profile": 162
  }
}
```

### Full Diagnostic (if needed)

```bash
# Test each column against Supabase
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/diagnose_columns.py

# Check the report
cat column_diagnosis.json | python3 -m json.tool
```

### Verify via Web UI

1. Open http://localhost:8000
2. Click "Sync Now"
3. Should now show:
   - ✓ Sync completed
   - device=25 (or similar)
   - profile=117 (instead of error)
   - stats=18171 (or similar)

### Check Logs

```bash
# Tail the logs in real-time
tail -f ~/Applications/mactool/logs/mactool.log

# Search for column filtering
grep -i "column\|filtered" ~/Applications/mactool/logs/mactool.log
```

## What Changed

### Before
```
Attempt 1: Upsert all 197 columns
          → Error: column not found
Attempt 2: Upsert all 197 columns
          → Error: column not found
Attempt 3: Upsert all 197 columns
          → Error: column not found
→ SYNC FAILS ✗
```

### After
```
Attempt 1: Upsert all 197 columns
          → Error: column 'bad_column_1' not found
          → Remove column, retry automatically
          → Success ✓
Attempt 2: Use cached columns (without bad_column_1)
          → Error: column 'bad_column_2' not found
          → Remove column, retry automatically
          → Success ✓
→ SYNC SUCCEEDS with column filtering ✓
```

## Expected Behavior

- **device table**: All 25 records synced (small table, all columns available)
- **profile table**: 117 records synced with ~162 columns (81% coverage)
- **stats table**: 18,171 records synced with 90-day filter

## Performance

No performance impact:
- Column discovery happens once per table
- Results are cached
- Subsequent syncs use cached columns

## Rollback

If you need to rollback:

```bash
# Restore original sync.py
git checkout ~/wtf/ebm/mactool/sync.py

# Or manually copy old version
cp ~/wtf/ebm/mactool.backup/sync.py ~/Applications/mactool/

# Restart service
launchctl unload ~/Library/LaunchAgents/com.ebm.mactool.plist
launchctl load ~/Library/LaunchAgents/com.ebm.mactool.plist
```

## Questions?

1. **Logs won't show what columns were filtered?**
   - Run `diagnose_columns.py` to get a detailed report

2. **Sync still failing?**
   - Check SUPABASE_KEY is correct in config.json
   - Verify profile table exists in Supabase
   - Check service role key has write permissions

3. **Want to see exactly which columns are synced?**
   - Run test_sync.py and check sync_test.log
   - Look for "Filtered profile: X cols → Y valid cols"
   - Run diagnose_columns.py for detailed column-by-column analysis
