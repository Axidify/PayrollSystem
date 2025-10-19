"""Test script to verify payroll deduplication when running payroll multiple times per month."""
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal

# Set up the path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal, init_db
from app.auth import User
from app.models import Model
from app.services import PayrollService
from app import crud

def test_payroll_deduplication():
    """Test that running payroll twice in the same month replaces the first run."""
    
    print("=" * 70)
    print("PAYROLL DEDUPLICATION TEST")
    print("=" * 70)
    
    # Initialize database
    init_db()
    db = SessionLocal()
    
    try:
        # Clean up any existing data
        print("\n1. Setting up test data...")
        db.query(Model).delete()
        db.query(crud.ScheduleRun).delete()
        db.commit()
        
        # Create test models
        model1 = Model(
            code="M001",
            real_name="Alice",
            working_name="Alice A",
            status="Active",
            start_date=datetime(2025, 1, 1).date(),
            payment_method="Bank Transfer",
            payment_frequency="Monthly",
            amount_monthly=Decimal("1000.00"),
        )
        model2 = Model(
            code="M002",
            real_name="Bob",
            working_name="Bob B",
            status="Active",
            start_date=datetime(2025, 1, 1).date(),
            payment_method="Bank Transfer",
            payment_frequency="Monthly",
            amount_monthly=Decimal("1500.00"),
        )
        
        db.add(model1)
        db.add(model2)
        db.commit()
        
        print(f"   ✓ Created 2 models: M001 (Alice), M002 (Bob)")
        
        # Run payroll for October 2025 - First time
        print("\n2. Running payroll for October 2025 (First time with 2 models)...")
        service = PayrollService(db)
        
        export_dir = Path("exports")
        schedule_df1, models_df1, validation_df1, summary1, run_id1 = service.run_payroll(
            target_year=2025,
            target_month=10,
            currency="USD",
            include_inactive=False,
            output_dir=export_dir,
        )
        
        run1 = db.query(crud.ScheduleRun).filter(crud.ScheduleRun.id == run_id1).first()
        payouts1 = db.query(crud.Payout).filter(crud.Payout.schedule_run_id == run_id1).all()
        
        print(f"   ✓ Run ID: {run_id1}")
        print(f"   ✓ Payouts created: {len(payouts1)}")
        for payout in payouts1:
            print(f"     - {payout.code} ({payout.real_name}): ${payout.amount}")
        
        # Add a new model
        print("\n3. Adding a new model (Model 3)...")
        model3 = Model(
            code="M003",
            real_name="Charlie",
            working_name="Charlie C",
            status="Active",
            start_date=datetime(2025, 1, 1).date(),
            payment_method="Bank Transfer",
            payment_frequency="Monthly",
            amount_monthly=Decimal("2000.00"),
        )
        db.add(model3)
        db.commit()
        
        print(f"   ✓ Created new model: M003 (Charlie)")
        print(f"   ✓ Total models now: 3")
        
        # Run payroll again for October 2025
        print("\n4. Running payroll for October 2025 (Second time with 3 models)...")
        schedule_df2, models_df2, validation_df2, summary2, run_id2 = service.run_payroll(
            target_year=2025,
            target_month=10,
            currency="USD",
            include_inactive=False,
            output_dir=export_dir,
        )
        
        run2 = db.query(crud.ScheduleRun).filter(crud.ScheduleRun.id == run_id2).first()
        payouts2 = db.query(crud.Payout).filter(crud.Payout.schedule_run_id == run_id2).all()
        
        print(f"   ✓ Run ID: {run_id2}")
        print(f"   ✓ Payouts created: {len(payouts2)}")
        for payout in payouts2:
            print(f"     - {payout.code} ({payout.real_name}): ${payout.amount}")
        
        # Verify deduplication
        print("\n5. Verification...")
        
        # Check all runs in database
        all_runs = db.query(crud.ScheduleRun).all()
        print(f"   Runs in database: {len(all_runs)}")
        for run in all_runs:
            print(f"     - Run {run.id}: {run.target_year}-{run.target_month:02d}")
        
        # Check that old run was deleted
        old_run = db.query(crud.ScheduleRun).filter(crud.ScheduleRun.id == run_id1).first()
        if old_run is None:
            print("   ✓ Old run (Run 1) was deleted")
        else:
            print(f"   ℹ️  Old run still exists (Run ID {old_run.id}). This is OK if the second run got a new ID.")
            print(f"      Run 1 ID: {run_id1}, Run 2 ID: {run_id2}")
            if run_id1 != run_id2:
                print("   ✓ Different run IDs - deduplication created new run (acceptable)")
            else:
                print("   ℹ️  Same run ID - database reused the autoincrement ID")
        
        # The key test: should only have ONE run for October 2025
        october_runs = db.query(crud.ScheduleRun).filter(
            crud.ScheduleRun.target_year == 2025,
            crud.ScheduleRun.target_month == 10
        ).all()
        
        if len(october_runs) == 1:
            print(f"   ✓ Only ONE run for October 2025 (old one was deleted)")
        else:
            print(f"   ✗ ERROR: Expected 1 run for October 2025, found {len(october_runs)}")
            return False
        
        # Check that new run has all 3 models
        if len(payouts2) == 3:
            print(f"   ✓ New run has all 3 models (was {len(payouts1)}, now {len(payouts2)})")
        else:
            print(f"   ✗ ERROR: Expected 3 payouts, got {len(payouts2)}")
            return False
        
        # Check that new run includes the new model
        model3_payout = next((p for p in payouts2 if p.code == "M003"), None)
        if model3_payout:
            print(f"   ✓ New model M003 included in Run 2: ${model3_payout.amount}")
        else:
            print("   ✗ ERROR: New model M003 not found in Run 2")
            return False
        
        # Check total payouts in system
        all_payouts = db.query(crud.Payout).all()
        if len(all_payouts) == 3:
            print(f"   ✓ Total payouts in database: {len(all_payouts)} (no duplicates)")
        else:
            print(f"   ✗ ERROR: Expected 3 total payouts, got {len(all_payouts)}")
            return False
        
        print("\n" + "=" * 70)
        print("✅ ALL TESTS PASSED!")
        print("=" * 70)
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = test_payroll_deduplication()
    sys.exit(0 if success else 1)
