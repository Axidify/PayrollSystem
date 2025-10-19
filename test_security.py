"""Test script for security features: rate limiting and account lockout"""
import sys
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal, Base, engine
from app.auth import User
from app.models import LoginAttempt
from app.security import (
    is_account_locked,
    increment_failed_login,
    reset_failed_login,
    record_login_attempt,
    get_failed_attempts_count,
    get_recent_login_attempts,
)

def setup_test_db():
    """Create test database tables"""
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables created")

def create_test_user(db: Session, username: str = "testuser"):
    """Create a test user"""
    user = db.query(User).filter(User.username == username).first()
    if user:
        db.delete(user)
        db.commit()
    
    user = User(
        username=username,
        password_hash="hashed_password_here",
        role="user",
        is_locked=False,
        failed_login_count=0
    )
    db.add(user)
    db.commit()
    print(f"✓ Created test user: {username}")
    return user

def test_rate_limiting():
    """Test rate limiting with failed login attempts"""
    print("\n" + "="*60)
    print("TEST 1: Rate Limiting (5 failed attempts)")
    print("="*60)
    
    db = SessionLocal()
    username = "testuser"
    
    # Create fresh test user
    user = create_test_user(db, username)
    
    # Simulate 5 failed login attempts
    print("\nSimulating 5 failed login attempts...")
    for i in range(1, 6):
        is_locked, msg = is_account_locked(db, username)
        print(f"\n  Attempt {i}:")
        print(f"    - Account locked? {is_locked}")
        
        if not is_locked:
            increment_failed_login(db, username)
            record_login_attempt(db, username, False, "127.0.0.1", "Test Browser")
            
            user = db.query(User).filter(User.username == username).first()
            print(f"    - Failed count: {user.failed_login_count}")
            print(f"    - ✓ Failed login recorded")
        else:
            print(f"    - ⚠ Account already locked: {msg}")
    
    # Check if account is locked after 5 attempts
    print("\n  After 5 attempts:")
    is_locked, msg = is_account_locked(db, username)
    user = db.query(User).filter(User.username == username).first()
    
    if is_locked:
        print(f"    ✓ PASS: Account is LOCKED")
        print(f"    ✓ Lockout message: {msg}")
        print(f"    ✓ Account locked until: {user.locked_until}")
    else:
        print(f"    ✗ FAIL: Account should be locked but is NOT")
    
    db.close()
    return is_locked

def test_auto_unlock():
    """Test auto-unlock after lockout period expires"""
    print("\n" + "="*60)
    print("TEST 2: Auto-Unlock After Timeout")
    print("="*60)
    
    db = SessionLocal()
    username = "testuser2"
    
    # Create fresh test user
    user = create_test_user(db, username)
    
    # Lock the account manually
    print("\nManually locking account...")
    user.is_locked = True
    user.locked_until = datetime.now() - timedelta(seconds=1)  # Expired lockout
    db.add(user)
    db.commit()
    print("  ✓ Account locked with expired timeout")
    
    # Check if it auto-unlocks
    print("\nChecking if account auto-unlocks...")
    is_locked, msg = is_account_locked(db, username)
    user = db.query(User).filter(User.username == username).first()
    
    if not is_locked:
        print(f"  ✓ PASS: Account AUTO-UNLOCKED")
        print(f"  ✓ is_locked field: {user.is_locked}")
        print(f"  ✓ locked_until: {user.locked_until}")
        print(f"  ✓ failed_login_count reset: {user.failed_login_count}")
    else:
        print(f"  ✗ FAIL: Account should auto-unlock but is still locked")
    
    db.close()
    return not is_locked

def test_reset_on_success():
    """Test that successful login resets failed count"""
    print("\n" + "="*60)
    print("TEST 3: Reset Failed Count on Successful Login")
    print("="*60)
    
    db = SessionLocal()
    username = "testuser3"
    
    # Create fresh test user
    user = create_test_user(db, username)
    
    # Simulate some failed attempts
    print("\nSimulating 3 failed login attempts...")
    for i in range(3):
        increment_failed_login(db, username)
        record_login_attempt(db, username, False, "127.0.0.1", "Test Browser")
    
    user = db.query(User).filter(User.username == username).first()
    print(f"  - Failed count after 3 attempts: {user.failed_login_count}")
    
    # Simulate successful login
    print("\nSimulating successful login...")
    reset_failed_login(db, username)
    record_login_attempt(db, username, True, "127.0.0.1", "Test Browser")
    
    user = db.query(User).filter(User.username == username).first()
    if user.failed_login_count == 0:
        print(f"  ✓ PASS: Failed count RESET to 0")
    else:
        print(f"  ✗ FAIL: Failed count should be 0 but is {user.failed_login_count}")
    
    db.close()
    return user.failed_login_count == 0

def test_login_audit_trail():
    """Test login attempt audit trail"""
    print("\n" + "="*60)
    print("TEST 4: Login Attempt Audit Trail")
    print("="*60)
    
    db = SessionLocal()
    username = "testuser4"
    
    # Create fresh test user
    user = create_test_user(db, username)
    
    # Record several login attempts
    print("\nRecording login attempts...")
    attempts = [
        (False, "127.0.0.1", "Chrome"),
        (False, "127.0.0.1", "Chrome"),
        (True, "127.0.0.1", "Chrome"),
    ]
    
    for success, ip, agent in attempts:
        record_login_attempt(db, username, success, ip, agent)
    
    print(f"  ✓ Recorded {len(attempts)} login attempts")
    
    # Retrieve audit trail
    print("\nRetrieving audit trail...")
    audit_trail = get_recent_login_attempts(db, username, limit=10)
    
    print(f"  ✓ Retrieved {len(audit_trail)} records from audit trail")
    for i, record in enumerate(audit_trail, 1):
        status = "✓ SUCCESS" if record.success else "✗ FAILED"
        print(f"    {i}. {status} - IP: {record.ip_address} - Agent: {record.user_agent}")
    
    if len(audit_trail) == len(attempts):
        print(f"\n  ✓ PASS: All attempts recorded in audit trail")
    else:
        print(f"\n  ✗ FAIL: Expected {len(attempts)} records, got {len(audit_trail)}")
    
    db.close()
    return len(audit_trail) == len(attempts)

def main():
    """Run all security feature tests"""
    print("\n" + "█"*60)
    print("█  PAYROLL SYSTEM - SECURITY FEATURES TEST SUITE")
    print("█"*60)
    
    try:
        setup_test_db()
        
        results = {
            "Rate Limiting": test_rate_limiting(),
            "Auto-Unlock": test_auto_unlock(),
            "Reset on Success": test_reset_on_success(),
            "Audit Trail": test_login_audit_trail(),
        }
        
        print("\n" + "█"*60)
        print("█  TEST RESULTS")
        print("█"*60)
        
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        
        for test_name, result in results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"  {status}: {test_name}")
        
        print(f"\n  Total: {passed}/{total} tests passed")
        print("█"*60 + "\n")
        
        return passed == total
        
    except Exception as e:
        print(f"\n✗ ERROR during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
