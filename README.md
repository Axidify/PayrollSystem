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

# PayrollSystem

PayrollSystem is a lightweight payroll administration web app built with FastAPI, SQLAlchemy and Jinja2 templates. It provides tools to run payroll, track payouts, manage ad-hoc payments, and monitor data quality.

## Features

- Schedule runs (monthly payroll runs) with associated payouts
- Ad hoc payments management (create, view, update status)
- Validation issues capture for data quality
- Dashboard with high-level metrics and quick links to detailed tables
- Excel import (to create runs/payouts) and Excel export for reporting
- Role-based UI: admin actions (run payroll, delete runs, update ad-hoc status)

## Quickstart (development)

Prerequisites:
- Python 3.10+
- A virtual environment (recommended)

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the dev server:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Important files

- `app/main.py` - application entry
- `app/routers/` - FastAPI routes (dashboard, schedules, models, auth, admin)
- `app/templates/` - Jinja2 templates for all views
- `app/static/css/styles.css` - global styles
- `app/crud.py` - database access and helper functions
- `app/models.py` - SQLAlchemy models
- `app/importers/excel_importer.py` - Excel import logic

## Development notes

- Dashboard aggregates are assembled in `app/routers/dashboard.py` which uses helpers in `app/crud.py`.
- Schedules views and exports are in `app/routers/schedules.py` and templates under `app/templates/schedules/`.
- Ad hoc payments are modeled by `AdhocPayment`, with monthly summaries calculated in `schedules` code.
- Use `pandas` and `openpyxl` for Excel exports.

## Tests

Run tests with pytest:

```powershell
pytest -q
```

## Contributing

Open a PR against `main`. Keep UI/style changes scoped to templates and `app/static/css/styles.css`.

---

If you'd like, I can expand this README with deployment steps, environment variables, or an architecture diagram.
