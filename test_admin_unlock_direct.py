"""Verify admin unlock works with direct database operations"""
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import SessionLocal
from app.auth import User
from app.security import unlock_account, is_account_locked

db = SessionLocal()

print("\n" + "="*60)
print("Testing Admin Unlock Account Feature (Direct DB)")
print("="*60)

# Step 1: Find or create a test user
print("\n1. Finding test user...")
test_user = db.query(User).filter(User.username == "testunlock").first()

if not test_user:
    # Create test user
    test_user = User(
        username="testunlock",
        password_hash=User.hash_password("password123"),
        role="user",
        is_locked=False,
        failed_login_count=0
    )
    db.add(test_user)
    db.commit()
    print(f"   ✓ Created test user: testunlock")
else:
    print(f"   ✓ Found existing test user: testunlock")

# Step 2: Lock the account manually
print("\n2. Locking the account...")
test_user.is_locked = True
test_user.locked_until = datetime.now() + timedelta(minutes=15)
test_user.failed_login_count = 5
db.add(test_user)
db.commit()
print(f"   ✓ Account locked")
print(f"   - is_locked: {test_user.is_locked}")
print(f"   - locked_until: {test_user.locked_until}")
print(f"   - failed_login_count: {test_user.failed_login_count}")

# Step 3: Verify account is locked
print("\n3. Verifying account is locked...")
is_locked, msg = is_account_locked(db, "testunlock")
print(f"   ✓ is_account_locked() returned: {is_locked}")
print(f"   - Message: {msg}")

# Step 4: Admin unlocks the account
print("\n4. Admin unlocking account...")
unlock_account(db, "testunlock")
print(f"   ✓ unlock_account() called")

# Step 5: Verify account is unlocked
print("\n5. Verifying account is unlocked...")
test_user = db.query(User).filter(User.username == "testunlock").first()
print(f"   ✓ After unlock:")
print(f"   - is_locked: {test_user.is_locked}")
print(f"   - locked_until: {test_user.locked_until}")
print(f"   - failed_login_count: {test_user.failed_login_count}")

is_locked, msg = is_account_locked(db, "testunlock")
print(f"   ✓ is_account_locked() returned: {is_locked}")

if not is_locked:
    print("\n   ✓ PASS: Account successfully unlocked by admin!")
else:
    print("\n   ✗ FAIL: Account still appears locked")

db.close()

print("\n" + "="*60)
print("✓ Admin unlock feature test complete!")
print("="*60 + "\n")
