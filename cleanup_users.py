"""Script to clean up old users from the database and handle schema migration."""
import os
import sys
from sqlalchemy import inspect, text
from app.database import engine, SessionLocal
from app.auth import User

def cleanup_database():
    """Clean up old users and handle schema migration."""
    
    with SessionLocal() as db:
        # Check if is_active column still exists in the database
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('users')]
        
        print(f"Current columns in users table: {columns}")
        
        if 'is_active' in columns:
            print("\n⚠️  Found is_active column in database - removing it...")
            
            # Use raw SQL to drop the column
            with engine.connect() as conn:
                try:
                    # Try to drop the column (works for most databases)
                    conn.execute(text("ALTER TABLE users DROP COLUMN is_active"))
                    conn.commit()
                    print("✅ Successfully dropped is_active column")
                except Exception as e:
                    print(f"❌ Could not drop column: {e}")
                    print("   This might be expected if the column doesn't exist or database doesn't support it")
        
        # Count current users
        user_count = db.query(User).count()
        print(f"\n📊 Current users in database: {user_count}")
        
        if user_count > 0:
            print("\nCurrent users:")
            for user in db.query(User).all():
                print(f"  - ID: {user.id}, Username: {user.username}, Role: {user.role}")
        
        # Delete all users except admin
        print("\n🗑️  Deleting all non-admin users...")
        admin_user = db.query(User).filter(User.role == "admin").first()
        
        if admin_user:
            print(f"Keeping admin user: {admin_user.username} (ID: {admin_user.id})")
            db.query(User).filter(User.role != "admin").delete()
            db.commit()
            print("✅ Deleted non-admin users")
        else:
            print("❌ No admin user found! Keeping all users.")
        
        # Show final count
        final_count = db.query(User).count()
        print(f"\n✅ Final user count: {final_count}")
        
        if final_count > 0:
            print("\nRemaining users:")
            for user in db.query(User).all():
                print(f"  - ID: {user.id}, Username: {user.username}, Role: {user.role}")

if __name__ == "__main__":
    print("=" * 60)
    print("PayrollSystem Database Cleanup")
    print("=" * 60)
    
    try:
        cleanup_database()
        print("\n✅ Cleanup completed successfully!")
    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
