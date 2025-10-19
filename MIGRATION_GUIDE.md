# Data Migration Guide

This guide explains how to migrate existing payment data into the Schedules system.

## Data Structure Overview

The system has three main data entities:

1. **Models** - Roster of payment recipients with their metadata
2. **Schedule Runs** - Monthly payroll cycles generated for a specific month/year
3. **Payouts** - Individual payments within a schedule run with status tracking

## Migration Approaches

### Option 1: Import Models (Recommended for New Setup)

Import your roster of recipients as **Models** first, then use the system to generate payroll schedules.

#### Step 1: Prepare Your Models CSV

Create a CSV file with the following columns:

```
status,code,real_name,working_name,start_date,payment_method,payment_frequency,amount_monthly
Active,CODE001,John Doe,John,2025-01-01,ACH,monthly,5000.00
Active,CODE002,Jane Smith,Jane,2025-01-15,Wire Transfer,biweekly,3000.00
Inactive,CODE003,Bob Johnson,Bob,2024-12-01,Check,weekly,2000.00
```

**Required Columns:**
- `status` - "Active" or "Inactive"
- `code` - Unique identifier (e.g., employee ID, contractor code)
- `real_name` - Full legal name
- `working_name` - Short display name
- `start_date` - Payment eligibility start date (YYYY-MM-DD format)
- `payment_method` - Payment delivery method (e.g., ACH, Wire Transfer, Check, Crypto)
- `payment_frequency` - "weekly", "biweekly", or "monthly"
- `amount_monthly` - Monthly equivalent amount (system calculates per-pay amounts)

**Optional Columns:**
- `crypto_wallet` - Crypto wallet address (if payment_method is Crypto)

#### Step 2: Import Models via Web UI

1. Navigate to **Models** page (`/models/`)
2. Click **Add Model** button
3. Fill in the form for each model, or use batch import via the CLI:

```bash
# Using the CLI (requires database setup)
python payroll.py --month 2025-11 --input your_models.csv --preview
```

#### Step 3: Generate Payroll Schedules

1. Go to **Schedules** → **Run Schedule**
2. Select the target month and year
3. Click **Generate Schedule**
4. The system will:
   - Calculate payment dates based on frequency
   - Generate individual payout records
   - Create a CSV export with all payouts

---

### Option 2: Direct Database Import (Historical Payments)

If you have historical payment records that need to be imported as completed payouts:

#### Step 1: Prepare Your Historical Payment Data CSV

Create a CSV with this structure:

```
schedule_run_id,code,working_name,payment_method,payment_frequency,amount,status,pay_date,notes
1,CODE001,John,ACH,monthly,5000.00,paid,2025-10-15,Payment processed
1,CODE002,Jane,Wire,biweekly,1500.00,paid,2025-10-15,On time
1,CODE003,Bob,Check,weekly,500.00,paid,2025-10-08,Mailed
```

**Required Columns:**
- `schedule_run_id` - ID of the schedule run this payout belongs to
- `code` - Model code (must match existing Model)
- `working_name` - Display name
- `payment_method` - How payment was sent
- `payment_frequency` - Payment schedule
- `amount` - Payment amount
- `status` - "paid", "on_hold", or "not_paid"
- `pay_date` - Payment date (YYYY-MM-DD)

**Optional Columns:**
- `notes` - Any context about the payment

#### Step 2: Create a Migration Script

Use the provided `migrate_historical_payouts.py` script:

```bash
python migrate_historical_payouts.py \
  --input historical_payments.csv \
  --run-id 1 \
  --currency USD
```

**What it does:**
- Reads your historical payment CSV
- Creates Payout records in the database
- Links payouts to existing Models (by code)
- Sets the status and payment information

---

### Option 3: Hybrid Approach (Recommended)

1. **First:** Import your roster as Models
2. **Then:** Generate schedules for future months using the system
3. **Finally:** Import historical payment records for past months as reference

This ensures:
- ✅ Your recipient list is up-to-date
- ✅ Future payroll is automatically calculated
- ✅ Historical records are preserved for auditing

---

## Step-by-Step Migration Walkthrough

### Walkthrough: Migrate Contractors + Historical Payments

#### Phase 1: Set Up Models (Day 1)

1. **Export your current contractor list** from your existing system (HR, ADP, Guidepoint, etc.)
   - Ensure it has: codes, names, payment methods, frequencies, amounts
   
2. **Transform to CSV format:**
   ```bash
   # Example: if you have an Excel file
   # Open in Excel → Save As → CSV (UTF-8)
   ```

3. **Upload Models:**
   - Go to `/models/` 
   - Click "Add Model" for each contractor
   - Or use CLI: `python payroll.py --month 2025-10 --input contractors.csv --preview`

4. **Verify:**
   - Check Models list page to confirm all records imported
   - Filter by status, code, or payment method to spot-check

#### Phase 2: Generate Future Schedules (Day 1-2)

1. Navigate to **Schedules** → **Run Schedule** (`/schedules/new`)
2. Select October 2025 (or your current month)
3. Click **Run Schedule** to generate payroll
4. Review the generated table:
   - Verify pay dates are correct
   - Confirm amounts match expectations
   - Check frequency calculations (e.g., biweekly = 2 payouts/month)
5. Export the schedule as CSV for record-keeping

#### Phase 3: Record Historical Payments (Day 2-3)

1. **Gather historical data** from your existing system (spreadsheets, accounting software, etc.)
   - For each month with payment history:
     - Dates payments were made
     - Amounts paid
     - Payment method used
     - Status (paid, held, failed)

2. **Create historical CSV:**
   ```
   schedule_run_id,code,working_name,payment_method,payment_frequency,amount,status,pay_date,notes
   1,CODE001,John,ACH,monthly,5000.00,paid,2025-09-15,Sept payroll
   1,CODE002,Jane,Wire,biweekly,1500.00,paid,2025-09-15,On time
   ```

3. **Import using migration script:**
   ```bash
   python migrate_historical_payouts.py --input sept_2025_payments.csv --run-id 1
   ```

4. **Verify in UI:**
   - Go to `Schedules` detail page
   - Filter by status to confirm all historical payments show as "paid"
   - Spot-check amounts and dates

#### Phase 4: Start Using for Live Payroll (Day 3+)

1. Each month, go to `/schedules/new` and run payroll for that month
2. Review the generated schedule
3. Use the UI to track payment status:
   - Initially all payouts show as "Not Paid"
   - Change status as payments are made
   - Add notes (reference numbers, issues, etc.)
4. Export schedule at end of month for accounting/audit trail

---

## CSV File Format Reference

### Models CSV

```csv
status,code,real_name,working_name,start_date,payment_method,payment_frequency,amount_monthly,crypto_wallet
Active,001,Alice Anderson,Alice,2024-01-01,ACH,monthly,6000.00,
Active,002,Bob Brown,Bob,2024-03-15,Wire Transfer,biweekly,4500.00,
Active,003,Carol Chen,Carol,2024-06-01,Crypto,weekly,1750.00,0x742d35Cc6634C0532925a3b844Bc7e7595f1234
Inactive,004,David Davis,Dave,2023-12-01,Check,monthly,3500.00,
```

### Historical Payouts CSV

```csv
schedule_run_id,code,working_name,payment_method,payment_frequency,amount,status,pay_date,notes
1,001,Alice,ACH,monthly,6000.00,paid,2025-10-15,October payroll batch
1,002,Bob,Wire Transfer,biweekly,2250.00,paid,2025-10-01,First half
1,002,Bob,Wire Transfer,biweekly,2250.00,paid,2025-10-15,Second half
1,003,Carol,Crypto,weekly,1750.00,paid,2025-10-06,Week 1
1,003,Carol,Crypto,weekly,1750.00,paid,2025-10-13,Week 2
1,003,Carol,Crypto,weekly,1750.00,paid,2025-10-20,Week 3
1,003,Carol,Crypto,weekly,1750.00,paid,2025-10-27,Week 4
```

---

## Data Validation

The system validates data during import:

### Model Validation Rules
- ✅ Status must be "Active" or "Inactive"
- ✅ Code must be unique
- ✅ Amount must be > 0
- ✅ Payment frequency must be "weekly", "biweekly", or "monthly"
- ✅ Start date must be valid date (YYYY-MM-DD)

### Payout Validation Rules
- ✅ Status must be "paid", "on_hold", or "not_paid"
- ✅ Code must match an existing Model
- ✅ Amount must be > 0
- ✅ Pay date must be valid

If validation fails, check the error message and fix your CSV before retrying.

---

## Troubleshooting

### "Duplicate code" error
- **Cause:** A Model with this code already exists
- **Fix:** Use a unique code or update the existing Model instead

### "Code not found" when importing payouts
- **Cause:** Payout references a Model code that doesn't exist
- **Fix:** First import Models, then import payouts

### "Invalid payment frequency"
- **Cause:** Frequency is misspelled or not one of: weekly, biweekly, monthly
- **Fix:** Check spelling in your CSV

### Missing columns in CSV
- **Cause:** CSV is missing required columns
- **Fix:** Add all required columns (see CSV Reference section)

### Dates not importing correctly
- **Cause:** Date format is not YYYY-MM-DD
- **Fix:** Reformat dates. Excel: use `TEXT(A1,"YYYY-MM-DD")`

---

## Next Steps

After migration:

1. **Monthly workflow:**
   - On first business day of month: Run payroll schedule (`/schedules/new`)
   - Review generated payouts
   - Process payments
   - Update status in UI as each payment completes
   - Export CSV for accounting/reconciliation

2. **Ongoing maintenance:**
   - Add new contractors to Models when hired
   - Update Model details if amounts or methods change
   - Mark contractors as "Inactive" when they depart

3. **Reporting:**
   - Use exported CSVs for audit trails
   - Filter by status to track paid vs. pending
   - Review validation messages for data quality

---

## Support

For issues or questions:
- Check error messages in the UI or CLI output
- Review validation messages in the database
- Consult this guide's Troubleshooting section
