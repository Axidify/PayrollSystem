#!/usr/bin/env python
"""Import models and payouts from an Excel workbook into the payroll database."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal
from app.importers.excel_importer import ImportOptions, RunOptions, import_from_excel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import models and payouts from Excel.")
    parser.add_argument("--workbook", required=True, type=Path, help="Path to the Excel workbook")
    parser.add_argument("--model-sheet", default="Models", help="Sheet name for model data")
    parser.add_argument("--payout-sheet", default="Payouts", help="Sheet name for payout data")
    parser.add_argument("--schedule-run-id", type=int, help="Reuse an existing schedule run id")
    parser.add_argument("--create-schedule-run", action="store_true", help="Create a new schedule run if one is not provided")
    parser.add_argument("--target-year", type=int, help="Target year when creating a schedule run")
    parser.add_argument("--target-month", type=int, help="Target month (1-12) when creating a schedule run")
    parser.add_argument("--currency", default="USD", help="Currency code for a new schedule run")
    parser.add_argument("--export-dir", default="exports", help="Export directory value for new schedule runs")
    parser.add_argument("--update-existing", action="store_true", help="Update existing models when codes match")
    parser.add_argument(
        "--auto-runs",
        action="store_true",
        help="Auto-create schedule runs based on payout pay dates (one run per month)",
    )
    parser.add_argument("--db-url", help="Override database URL (defaults to application setting)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without committing")
    return parser.parse_args()


def get_session_factory(db_url: Optional[str]):
    if db_url:
        custom_engine = create_engine(db_url, future=True)
        return sessionmaker(bind=custom_engine, autoflush=False, autocommit=False, future=True)
    return SessionLocal


def main() -> int:
    args = parse_args()
    if not args.workbook.exists():
        print(f"[ERROR] Workbook not found: {args.workbook}")
        return 1

    try:
        workbook_bytes = args.workbook.read_bytes()
    except OSError as exc:
        print(f"[ERROR] Could not read workbook: {exc}")
        return 1

    SessionCls = get_session_factory(args.db_url)
    session: Session = SessionCls()

    try:
        import_options = ImportOptions(
            model_sheet=args.model_sheet,
            payout_sheet=args.payout_sheet,
            update_existing=args.update_existing,
        )
        run_options = RunOptions(
            schedule_run_id=args.schedule_run_id,
            create_schedule_run=args.create_schedule_run,
            target_year=args.target_year,
            target_month=args.target_month,
            currency=args.currency,
            export_dir=args.export_dir,
            auto_generate_runs=args.auto_runs,
        )

        summary = import_from_excel(session, workbook_bytes, import_options, run_options)

        if summary.model_errors:
            print("[WARN] Issues during model import:")
            for message in summary.model_errors:
                print(f"  - {message}")
        else:
            print("[OK] Models sheet processed without validation errors")

        if summary.payout_errors:
            print("[WARN] Issues during payout import:")
            for message in summary.payout_errors:
                print(f"  - {message}")
        else:
            print("[OK] Payout sheet processed without validation errors")

        adjustment_activity = (
            summary.adjustments_created
            + summary.adjustments_updated
            + len(summary.adjustment_errors)
        )
        if adjustment_activity:
            if summary.adjustment_errors:
                print("[WARN] Issues during compensation adjustments import:")
                for message in summary.adjustment_errors:
                    print(f"  - {message}")
            else:
                print("[OK] Compensation adjustments processed without validation errors")

        print()
        print("[SUMMARY] Import results:")
        print(f"  - Models created: {summary.models_created}")
        print(f"  - Models updated: {summary.models_updated}")
        print(f"  - Payouts created: {summary.payouts_created}")
        if adjustment_activity:
            print(f"  - Adjustments created: {summary.adjustments_created}")
            print(f"  - Adjustments updated: {summary.adjustments_updated}")
        if summary.schedule_run_ids:
            if len(summary.schedule_run_ids) == 1:
                print(f"  - Schedule run id: {summary.schedule_run_ids[0]}")
            else:
                run_list = ", ".join(str(run_id) for run_id in summary.schedule_run_ids)
                print(f"  - Schedule run ids: {run_list}")
        else:
            print("  - Schedule run id: none")

        if args.dry_run:
            session.rollback()
            print("[INFO] Dry run enabled - no changes were committed")
        else:
            session.commit()
            print("[OK] Database updated successfully")
        return 0
    except Exception as exc:
        session.rollback()
        print(f"[ERROR] Import failed: {exc}")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
