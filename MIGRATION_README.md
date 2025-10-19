# Migration Resources

This folder contains tools and documentation for migrating existing payment data into the Schedules system.

## Files

- **`MIGRATION_GUIDE.md`** - Comprehensive guide on how to migrate data into the system
- **`migrate_historical_payouts.py`** - Python script to import historical payout records from CSV
- **`sample_models.csv`** - Example CSV showing Model format
- **`sample_historical_payouts.csv`** - Example CSV showing historical Payout format

## Quick Start

### 1. Import Your Models (Recipients/Contractors)

**Prepare a CSV file** with columns: `status`, `code`, `real_name`, `working_name`, `start_date`, `payment_method`, `payment_frequency`, `amount_monthly`

See `sample_models.csv` for an example.

**Import via web UI:**
- Go to `/models/`
- Click "Add Model" for each recipient
- Fill in the form

**Or import via CLI:**
```bash
python payroll.py --month 2025-10 --input your_models.csv --preview
```

### 2. Generate Future Schedules

```bash
# Generate payroll for October 2025
python payroll.py --month 2025-10 --input your_models.csv --out ./exports
```

Or use the web UI: `/schedules/new` → Select month → Run Schedule

### 3. Import Historical Payment Records (Optional)

**Prepare a CSV file** with columns: `schedule_run_id`, `code`, `working_name`, `payment_method`, `payment_frequency`, `amount`, `status`, `pay_date`, `notes`

See `sample_historical_payouts.csv` for an example.

**Import using the migration script:**
```bash
python migrate_historical_payouts.py \
  --input historical_payments.csv \
  --run-id 1 \
  --currency USD \
  --dry-run  # Remove this flag to actually import
```

## CSV Format Details

### Models CSV

Required columns:
- `status` - "Active" or "Inactive"
- `code` - Unique identifier
- `real_name` - Full legal name
- `working_name` - Display name
- `start_date` - Eligibility start date (YYYY-MM-DD)
- `payment_method` - e.g., ACH, Wire Transfer, Check, Crypto
- `payment_frequency` - "weekly", "biweekly", or "monthly"
- `amount_monthly` - Monthly equivalent amount

Optional:
- `crypto_wallet` - Wallet address if Crypto is payment method

### Historical Payouts CSV

Required columns:
- `schedule_run_id` - ID of the schedule run
- `code` - Model code (must exist)
- `working_name` - Display name
- `payment_method` - How payment was sent
- `payment_frequency` - Payment schedule
- `amount` - Payment amount
- `status` - "paid", "on_hold", or "not_paid"
- `pay_date` - Payment date (YYYY-MM-DD)

Optional:
- `notes` - Context about payment

## Common Issues

### "Model not found"
The payout references a model code that doesn't exist. First import Models, then import payouts.

### "Duplicate code"
A Model with this code already exists. Either delete it first or use a different code.

### "Invalid date format"
Dates must be in YYYY-MM-DD format. If converting from Excel:
```excel
=TEXT(A1,"YYYY-MM-DD")
```

### "Invalid payment frequency"
Must be exactly: "weekly", "biweekly", or "monthly" (lowercase, no extra spaces)

## For More Help

See `MIGRATION_GUIDE.md` for:
- Detailed migration walkthrough
- Data validation rules
- Troubleshooting guide
- Step-by-step examples
