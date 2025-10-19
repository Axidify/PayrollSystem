# Payroll Desk

Automates recurring payroll schedules for the agency, supports manual roster imports via CLI, and now provides a web UI for managing models and schedule runs with persistent storage.

## Requirements

Install dependencies with:

```powershell
pip install -r requirements.txt
```

## CLI Usage

Generate an export from CSV or Excel using the existing command-line interface:

```powershell
python payroll.py --month 2025-11 --input models_sample.csv --out dist --preview
```

The CLI writes Excel and CSV bundles to the chosen output directory and prints a summary line to the console.

## Web Application

Launch the FastAPI server to manage models, run schedules, and download exports via the browser:

```powershell
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000` and use the navigation links to:

- Add, edit, or delete model records
- Trigger payroll runs for the target month
- Inspect payout schedules and validation findings
- Download Excel/CSV exports generated for each run

The application stores data in `data/payroll.db` (SQLite). Override the location by setting the `PAYROLL_DATABASE_URL` environment variable.

## Running Tests

```powershell
python -m pytest
```

Sample data is available in `models_sample.csv` for quick experimentation.
