#!/usr/bin/env python
"""
Migration script to import historical payout records into the system.

Usage:
    python migrate_historical_payouts.py \
        --input historical_payments.csv \
        --run-id 1 \
        --currency USD
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Payout, ScheduleRun, Model, PAYOUT_STATUS_ENUM
import os


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Import historical payout records into the system."
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to CSV file with historical payouts",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        type=int,
        help="Schedule run ID to associate payouts with",
    )
    parser.add_argument(
        "--currency",
        default="USD",
        help="Currency code (default: USD)",
    )
    parser.add_argument(
        "--db-url",
        help="Database URL (default: from PAYROLL_DATABASE_URL env var or SQLite)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without making changes",
    )
    return parser.parse_args()


def get_db_url(url_override: Optional[str] = None) -> str:
    """Get database URL from override or environment variable."""
    if url_override:
        return url_override
    
    env_url = os.getenv("PAYROLL_DATABASE_URL")
    if env_url:
        return env_url
    
    # Default to SQLite
    return "sqlite:///./data/payroll.db"


def validate_payout_row(row: dict, row_num: int) -> tuple[bool, str]:
    """Validate a payout row and return (is_valid, error_message)."""
    required = ["schedule_run_id", "code", "working_name", "payment_method", 
                "payment_frequency", "amount", "status", "pay_date"]
    
    for col in required:
        if col not in row or not row[col].strip():
            return False, f"Row {row_num}: Missing required column '{col}'"
    
    # Validate status
    if row["status"].lower() not in PAYOUT_STATUS_ENUM:
        return False, f"Row {row_num}: Invalid status '{row['status']}'. Must be one of: {', '.join(PAYOUT_STATUS_ENUM)}"
    
    # Validate amount
    try:
        amount = Decimal(row["amount"])
        if amount <= 0:
            return False, f"Row {row_num}: Amount must be > 0, got {amount}"
    except (ValueError, TypeError):
        return False, f"Row {row_num}: Invalid amount '{row['amount']}'"
    
    # Validate date
    try:
        datetime.strptime(row["pay_date"], "%Y-%m-%d")
    except ValueError:
        return False, f"Row {row_num}: Invalid date format '{row['pay_date']}'. Use YYYY-MM-DD"
    
    return True, ""


def import_payouts(
    csv_path: Path,
    run_id: int,
    db_url: str,
    dry_run: bool = False,
) -> None:
    """Import payout records from CSV file."""
    
    # Validate input file
    if not csv_path.exists():
        print(f"❌ File not found: {csv_path}")
        return
    
    print(f"📁 Reading from: {csv_path}")
    print(f"🗄️  Database: {db_url}")
    print(f"📅 Schedule Run ID: {run_id}")
    print()
    
    # Connect to database
    engine = create_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine)
    session = Session(engine)
    
    try:
        # Verify schedule run exists
        run = session.query(ScheduleRun).filter(ScheduleRun.id == run_id).first()
        if not run:
            print(f"❌ Schedule run {run_id} not found in database")
            return
        
        print(f"✓ Found schedule run: {run.target_month}/{run.target_year}")
        print()
        
        # Read CSV
        payouts_to_create = []
        errors = []
        
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                print("❌ CSV file is empty")
                return
            
            for row_num, row in enumerate(reader, start=2):  # Start at 2 (skip header)
                # Normalize keys
                row = {k.strip(): v for k, v in row.items()}
                
                # Validate row
                is_valid, error_msg = validate_payout_row(row, row_num)
                if not is_valid:
                    errors.append(error_msg)
                    continue
                
                # Check if model exists
                code = row["code"].strip()
                model = session.query(Model).filter(Model.code == code).first()
                if not model:
                    errors.append(f"Row {row_num}: Model with code '{code}' not found in database")
                    continue
                
                # Parse data
                pay_date = datetime.strptime(row["pay_date"], "%Y-%m-%d").date()
                amount = Decimal(row["amount"])
                
                payout = Payout(
                    schedule_run_id=run_id,
                    model_id=model.id,
                    pay_date=pay_date,
                    code=code,
                    real_name=model.real_name,
                    working_name=row["working_name"].strip(),
                    payment_method=row["payment_method"].strip(),
                    payment_frequency=row["payment_frequency"].strip().lower(),
                    amount=amount,
                    status=row["status"].strip().lower(),
                    notes=row.get("notes", "").strip() or None,
                )
                payouts_to_create.append(payout)
        
        # Print errors
        if errors:
            print("⚠️  Validation errors found:\n")
            for error in errors:
                print(f"  {error}")
            print()
        
        # Print summary
        print(f"📊 Results:")
        print(f"  ✓ Valid payouts to import: {len(payouts_to_create)}")
        print(f"  ✗ Errors: {len(errors)}")
        print()
        
        if not payouts_to_create:
            print("❌ No valid payouts to import")
            return
        
        # Show preview
        print("📋 Preview of first 5 payouts:")
        print()
        for i, payout in enumerate(payouts_to_create[:5], start=1):
            print(f"  {i}. {payout.pay_date} | {payout.code} | {payout.working_name:20} | "
                  f"${payout.amount:>8.2f} | {payout.status}")
        
        if len(payouts_to_create) > 5:
            print(f"  ... and {len(payouts_to_create) - 5} more")
        print()
        
        # Perform import if not dry run
        if dry_run:
            print("🔄 DRY RUN: No changes made to database")
        else:
            print("💾 Importing payouts into database...")
            session.add_all(payouts_to_create)
            session.commit()
            print(f"✅ Successfully imported {len(payouts_to_create)} payouts")
    
    except Exception as e:
        print(f"❌ Error during import: {e}")
        session.rollback()
    finally:
        session.close()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    db_url = get_db_url(args.db_url)
    import_payouts(
        csv_path=args.input,
        run_id=args.run_id,
        db_url=db_url,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
