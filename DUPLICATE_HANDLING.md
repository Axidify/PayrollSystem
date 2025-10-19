# Duplicate Data Handling Guide

## Overview

The Payroll System has multiple import points that handle data in different ways. This guide explains how duplicate data is detected and prevented at each layer.

---

## 1. Model Roster Imports (`/models/import`)

**Single Source of Truth:** Model `code` field

### Prevention Strategy: Unique Code Constraint

When importing a CSV of models:

```
status,code,real_name,working_name,start_date,payment_method,payment_frequency,amount_monthly
Active,CODE001,John Doe,John,2025-01-01,ACH,monthly,5000.00
Active,CODE002,Jane Smith,Jane,2025-01-15,Wire Transfer,biweekly,3000.00
```

**Duplicate Detection:**
- Before importing each row, the system checks if `code` already exists via `crud.get_model_by_code(db, code)`
- If found, the row is **rejected with error**: `"Model code '{code}' already exists"`
- Import continues with remaining rows (partial success)

**Example Error Response:**
```
Import failed. Errors: 
- Row 2: Model code 'CODE001' already exists
- Row 5: Model code 'CODE003' already exists
Rows 3 and 4 imported successfully
```

### User Actions for Duplicates

If you encounter duplicate code errors:

| Scenario | Action |
|----------|--------|
| **Re-importing same file by mistake** | Stop and discard the file; codes already exist |
| **Updating existing model data** | Use individual Edit form at `/models/{id}/edit` |
| **Combining rosters from multiple sources** | Manually ensure codes are unique before importing |
| **Merging with historical data** | Use unique prefixes per source (e.g., `EMP_001` vs `CTR_001`) |

---

## 2. Payroll Schedule Runs (`/schedules/`)

**Single Source of Truth:** (ScheduleRun + Payout) tuple by `(year, month)`

### Prevention Strategy: Smart Refresh (not true duplicates)

When generating payroll for a month that already has a run:

```python
existing_runs = crud.list_schedule_runs(
    db, target_year=target_year, target_month=target_month
)

if existing_runs:
    # REUSE and REFRESH the existing run
    run = existing_runs[0]
    # Preserve status and notes from old payouts
    old_payout_data = {(code, pay_date): {...} for payout in run.payouts}
    # Clear and regenerate with current model roster
    crud.clear_schedule_data(db, run)
```

**Key Behavior:**
- ✅ **Same month, same run ID:** If you generate payroll for October 2025 twice, both use run_id=1
- ✅ **Status preservation:** Previous status marks (paid/on_hold) are preserved during refresh
- ✅ **Notes preserved:** Admin notes on payouts survive the refresh
- ❌ **No true duplicates:** Payouts table never has duplicate (schedule_run_id, model_id, pay_date) combinations

**Example:**

```
October 2025 - Run 1 (Initial) → 3 payouts created
October 2025 - Run 1 (Refresh) → Old 3 deleted, new 3 created with same IDs, status preserved
Result: Payouts table has exactly 3 October 2025 payouts (no duplicates)
```

### Prevented Scenarios

| Scenario | What Prevents It | Result |
|----------|------------------|--------|
| **Accidentally run payroll twice** | Smart refresh logic | Old run replaced cleanly |
| **Change model roster → re-run October** | Refresh logic + old_payout_data | Payouts regenerated, old notes kept |
| **Add new model → re-run October** | Clear + regenerate | New model included, others unchanged |
| **Delete inactive model → re-run** | Refresh filters by current roster | Inactive model's payout removed |

---

## 3. Historical Payment Imports (`migrate_historical_payouts.py`)

**Single Source of Truth:** ScheduleRun ID (must exist first)

### Prevention Strategy: Pre-validation

```python
def import_payouts(csv_path, run_id, db_url):
    # Step 1: Verify schedule run exists
    run = session.query(ScheduleRun).filter(ScheduleRun.id == run_id).first()
    if not run:
        raise Error(f"Schedule run {run_id} not found")
    
    # Step 2: Validate each payout row
    for row_num, row in enumerate(reader, start=2):
        # Check model code exists
        model = session.query(Model).filter(Model.code == code).first()
        if not model:
            errors.append(f"Row {row_num}: Model with code '{code}' not found")
            continue
        
        # Validate pay_date, amount, status
        # ... validation logic ...
        
        payouts_to_create.append(payout)
    
    # Step 3: All-or-nothing import
    if not payouts_to_create and errors:
        raise Error("Import failed - see errors above")
    
    session.add_all(payouts_to_create)
    session.commit()
```

**Duplicate Detection:**
- No explicit duplicate check (database allows multiple payouts per model per run)
- **Instead:** ScheduleRun must exist first; you specify run_id in command
- Prevents accidental creation of orphaned payouts
- Admin responsibility: don't import the same CSV twice to same run_id

### CSV Format for Historical Payouts

```csv
code,working_name,payment_method,payment_frequency,pay_date,amount,status,notes
CODE001,John,ACH,monthly,2024-10-01,5000.00,paid,Imported from legacy system
CODE001,John,ACH,monthly,2024-11-01,5000.00,paid,Imported from legacy system
CODE002,Jane,Wire,biweekly,2024-10-15,3000.00,on_hold,Pending verification
```

### User Actions for Historical Imports

| Scenario | Action |
|----------|--------|
| **Accidentally import same CSV twice** | Delete the ScheduleRun and recreate; then import once |
| **Import to wrong run_id** | Delete old payouts manually; re-import with correct run_id |
| **Partial import failed** | Fix CSV errors and retry (previous failures already skipped) |

---

## 4. Payroll Export & Re-import Cycle

### Export → Modify → Re-import (Not Recommended)

**Scenario:** You export payroll, modify it externally, try to re-import

**What Happens:**
1. Export creates CSV with all payouts for models in filter
2. User modifies amounts, dates, or statuses externally
3. User attempts to import via `/models/import` (wrong endpoint!)

**Prevention:**
- ❌ `/models/import` only handles Model data (rosters), not Payouts
- ✅ To modify payouts, edit them directly in `/schedules/{run_id}/`
- ✅ Use `migrate_historical_payouts.py` script for bulk historical data only

**Correct Workflow:**
```
Modify payouts → Via UI (/schedules/) → View in Payment History (/models/{id})
Export for external audit → Download CSV → View-only (don't re-import)
```

---

## 5. Database-Level Constraints

### Model Table
```sql
UNIQUE(code)  -- Only one model per code
```

### Payout Table
```sql
FOREIGN KEY(schedule_run_id, model_id)  -- Links to schedule run and model
-- No unique constraint; multiple payouts per model per run allowed by design
-- (e.g., weekly frequency = 4 payouts per month)
```

### ScheduleRun Table
```sql
UNIQUE(target_year, target_month)  -- Only one active run per month
-- This is enforced via application logic (refresh behavior)
```

---

## 6. Import Best Practices

### ✅ DO

- **Import rosters first:** Create all Models before generating payroll
- **Use unique codes:** Ensure Model codes never overlap (use prefixes for different sources)
- **Preserve status:** When refreshing payroll, system automatically keeps paid status
- **Validate dates:** Use YYYY-MM-DD format consistently
- **Document sources:** Add notes (e.g., "Imported from legacy system on 2025-01-15")
- **Backup before bulk imports:** Export current state before importing changes
- **Use dry-run mode:** Test historical payouts import with `--dry-run` flag

### ❌ DON'T

- **Re-import the same CSV twice:** Will hit duplicate code errors (intentional safety)
- **Import payouts before models:** Foreign key constraints will reject missing codes
- **Mix data sources without prefixes:** Could create unintended duplicate codes
- **Manually edit CSV exports and re-import:** Will fail; use UI for modifications
- **Delete ScheduleRuns without confirming:** Historical payouts become orphaned

---

## 7. Troubleshooting Common Scenarios

### Scenario: "Code already exists" error on import

**Diagnosis:**
```python
existing = crud.get_model_by_code(db, code)
if existing:
    error = f"Model code '{code}' already exists"
```

**Resolution:**
1. Check if you meant to update existing model → Use Edit form
2. Check if codes are correct → Verify no typos/duplicates in your CSV
3. Check if this is a test import → Delete old models first, or use unique test codes

### Scenario: Payroll generated but amounts are wrong

**Likely Causes:**
- Model with wrong `amount_monthly` value
- Model set to "Inactive" (excluded from calculation)
- Wrong payment frequency selected

**Resolution:**
- Check Model via `/models/{id}` → Verify amount_monthly and status
- Edit Model if needed
- Re-run payroll (refresh will use updated values, preserve paid status)

### Scenario: Historical payouts won't import

**Diagnosis:**
```
Row 5: Model with code 'BADCODE' not found in database
```

**Resolution:**
1. First ensure all Model codes exist: `SELECT code FROM models;`
2. Fix CSV to use correct, existing codes
3. Verify ScheduleRun ID is correct: `python migrate_historical_payouts.py --run-id 1 ...`
4. Use `--dry-run` first to test: `python migrate_historical_payouts.py --dry-run ...`

### Scenario: Two models accidentally merged

**Prevention Didn't Work:**
- Import succeeded with duplicate code (shouldn't happen)
- Or two different codes actually merged in UI

**Recovery:**
1. View Model details at `/models/{id}`
2. Check if this is truly a duplicate or just similar data
3. Use Edit form to separate if needed, or Delete to remove
4. Re-import data if necessary

---

## 8. Audit Trail

### What's Tracked
- ✅ Model creation/updates: `Model.created_at`, `Model.updated_at`
- ✅ Payroll runs: `ScheduleRun.created_at`, `ScheduleRun.target_year/month`
- ✅ Payout status changes: `Payout.status` updates preserved on refresh
- ✅ Notes on payouts: `Payout.notes` preserved during refresh
- ❌ Import history: Not explicitly logged (rely on CSV export with timestamps)

### Viewing Import Results
- Check `/schedules/` for ScheduleRun records and their payouts
- Export CSV to verify data: `/models/export?include_payments=true`
- Check `/models/{id}` Payment History for individual model payouts

---

## Summary: Duplicate Prevention by Layer

| Layer | Data Type | Duplicate Prevention | Enforcement |
|-------|-----------|----------------------|--------------|
| **Models** | Roster (code) | Unique constraint on `code` | Database + App check |
| **Payroll Runs** | Schedule (year/month) | Smart refresh (replace, don't duplicate) | App logic |
| **Payouts** | Individual payments | Foreign key to valid run + model | Database constraint |
| **Imports** | Historical data | Admin specifies target run_id | Manual control |

**Golden Rule:** Use descriptive codes, import in order (models → runs → historical), and leverage the refresh logic to avoid orphaned data.

---

