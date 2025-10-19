"""Test admin unlock account feature"""
import requests
from requests.auth import HTTPBasicAuth

BASE_URL = "http://127.0.0.1:8000"

print("\n" + "="*60)
print("Testing Admin Unlock Account Feature")
print("="*60)

# Step 1: Try to lock the test user by making 5 failed login attempts
print("\n1. Locking a test account with 5 failed attempts...")
username = "testuser"
password = "wrongpass"

for i in range(5):
    response = requests.post(
        f"{BASE_URL}/login",
        data={"username": username, "password": password},
        allow_redirects=False
    )
    if i == 4:
        print(f"   ✓ Attempt 5: Status {response.status_code}")

# Verify account is locked
response = requests.post(
    f"{BASE_URL}/login",
    data={"username": username, "password": password},
    allow_redirects=False
)

if "locked" in response.text.lower():
    print(f"   ✓ Account is LOCKED")
else:
    print(f"   ✗ Account should be locked")

# Step 2: Login as admin and visit users page
print("\n2. Admin checking users page...")
session = requests.Session()

# Login as admin
login_response = session.post(
    f"{BASE_URL}/login",
    data={"username": "admin", "password": "admin"},
    allow_redirects=False
)

if login_response.status_code == 303:
    print(f"   ✓ Admin logged in successfully")

# Get users page
users_page = session.get(f"{BASE_URL}/admin/users")
if "testuser" in users_page.text and "LOCKED" in users_page.text:
    print(f"   ✓ Locked user visible on admin page")
    print(f"   ✓ Lock status displayed")
else:
    print(f"   ✗ Locked status not shown on admin page")

# Step 3: Find the user ID from the admin page
print("\n3. Attempting to unlock account via admin...")
# For this test, we'll assume testuser has id=2 (admin is usually 1)
# In a real scenario, we'd parse the HTML to find the actual ID

# Try to unlock - we'll use ID 2 as a test
unlock_response = session.post(f"{BASE_URL}/admin/users/2/unlock")

if unlock_response.status_code == 303:
    print(f"   ✓ Unlock request successful (redirected)")

# Verify account is unlocked
print("\n4. Verifying account is now unlocked...")
# Try login with wrong password again
response = requests.post(
    f"{BASE_URL}/login",
    data={"username": username, "password": password},
    allow_redirects=False
)

if "locked" not in response.text.lower() and "Invalid username or password" in response.text:
    print(f"   ✓ Account is UNLOCKED - normal login behavior restored")
    print(f"   ✓ User can attempt login again")
else:
    print(f"   Status: {response.status_code}")

print("\n" + "="*60)
print("✓ Admin unlock account test complete!")
print("="*60 + "\n")
