#!/usr/bin/env python3
"""Test script to verify login flow works correctly."""

import requests
from urllib.parse import urljoin

BASE_URL = "http://127.0.0.1:8000"

session = requests.Session()

print("=" * 60)
print("Testing Authentication Flow")
print("=" * 60)

# Step 1: Get login page
print("\n1. Fetching login page...")
resp = session.get(urljoin(BASE_URL, "/login"))
print(f"   Status: {resp.status_code}")
print(f"   Has login form: {'form' in resp.text.lower()}")

# Step 2: Try to access dashboard without logging in (should fail)
print("\n2. Accessing dashboard without login (should fail with 401)...")
resp = session.get(urljoin(BASE_URL, "/dashboard"))
print(f"   Status: {resp.status_code}")
if resp.status_code == 401:
    print("   ✓ Correctly rejected unauthenticated access")
else:
    print(f"   ✗ Unexpected status code: {resp.status_code}")

# Step 3: Submit login form with correct credentials
print("\n3. Submitting login form with admin/admin...")
resp = session.post(
    urljoin(BASE_URL, "/login"),
    data={"username": "admin", "password": "admin"},
    allow_redirects=False,  # Don't follow redirect
)
print(f"   Status: {resp.status_code}")
print(f"   Location: {resp.headers.get('location')}")
print(f"   Cookies in session: {session.cookies.get_dict()}")

# Step 4: Try to access dashboard after login (should succeed)
print("\n4. Accessing dashboard after login...")
resp = session.get(urljoin(BASE_URL, "/dashboard"), allow_redirects=False)
print(f"   Status: {resp.status_code}")
if resp.status_code == 200:
    print("   ✓ Successfully accessed dashboard after login")
elif resp.status_code == 401:
    print(f"   ✗ Still getting 401 - cookie not sent with request")
    print(f"   Cookies: {session.cookies}")
    print(f"   Headers: {dict(session.headers)}")
else:
    print(f"   Status: {resp.status_code}")

print("\n" + "=" * 60)
