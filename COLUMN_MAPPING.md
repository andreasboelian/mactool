# Column Mapping & Sync Fix

## Problem

The profile table in SQLite has 197 columns, but Supabase's profile table doesn't have all of them. When syncing, the upsert would fail with errors like:

```
Could not find the 'config__check_followback_after_unfollow' column of 'profile' in the schema cache
```

This caused the entire profile sync to fail.

## Solution

The sync system now implements **intelligent column filtering**:

1. **Auto-detection**: When a column doesn't exist in Supabase, the error is caught
2. **Smart retry**: The problematic column is removed from the batch and upsert is retried
3. **Caching**: Valid columns are cached to avoid repeated errors
4. **Logging**: All filtering operations are logged for visibility

## How It Works

### During Sync

```python
# Attempt upsert with all columns
↓ (error: column not found)
↓ Extract bad column name from error
↓ Remove bad column from batch
↓ Retry upsert
↓ Cache valid columns
↓ Continue with remaining batches (using cached columns)
```

### Benefits

- **No data loss**: All available data is synced, only unavailable columns are skipped
- **Automatic recovery**: No manual column mapping needed
- **Efficient**: Discovered columns are cached for performance
- **Transparent**: Logs show exactly what was filtered out

## Testing

### Run a Test Sync

```bash
cd ~/Applications/mactool
./venv/bin/python3 test_sync.py
```

This will:
- Run a full sync with detailed logging
- Show which columns were filtered
- Save a detailed log to `sync_test.log`

### Diagnose Column Coverage

To see exactly which columns exist in Supabase:

```bash
cd ~/Applications/mactool
./venv/bin/python3 diagnose_columns.py
```

This will:
- Test each SQLite column against Supabase
- Show which ones exist and which are missing
- Save results to `column_diagnosis.json`

## Sync Results

After the fix, expect results like:

```json
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

The `column_filtering` field shows how many columns were actually synced (after removing unavailable ones).

## Logs

Check the main logs for column filtering details:

```bash
tail -f ~/Applications/mactool/logs/mactool.log | grep -i column
```

Look for lines like:
- `Column error detected: ...`
- `Removing problematic column: config__xyz`
- `Filtered profile: 197 cols → 162 valid cols`

## What Gets Synced

- **device**: All columns (should be fully available)
- **profile**: Core columns available in Supabase + metadata fields
- **stats**: 18,171 records from last 90 days + metadata fields

### Metadata Fields Added During Sync

These are added to every record:
- `mac_id`: Server name for multi-tenant support
- `ig_server`: Server name (duplicate for Supabase schema)
- `imported_at`: Sync timestamp
- `change_at`: NULL on initial import (updated on changes)
- ID prefixes: `{server_name}_{original_id}`

## Troubleshooting

### Sync Still Failing?

1. Check the logs:
   ```bash
   tail -50 ~/Applications/mactool/logs/mactool.log
   ```

2. Run the test script:
   ```bash
   ~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/test_sync.py
   ```

3. Run the diagnostic:
   ```bash
   ~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/diagnose_columns.py
   ```

### No Columns Being Synced?

If `column_filtering` shows 0 columns synced, there may be a fundamental schema mismatch. Check:

1. Supabase connection: Is `SUPABASE_KEY` correct?
2. Table exists: Does the profile table exist in Supabase?
3. Permissions: Does the service role key have write permissions?

## Manual Sync

To manually trigger a sync from the command line:

```bash
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/main.py --sync
```

Or with test logging:

```bash
~/Applications/mactool/venv/bin/python3 ~/Applications/mactool/test_sync.py
```
