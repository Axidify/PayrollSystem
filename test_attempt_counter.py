"""Quick test to verify attempt counter display"""
import requests

BASE_URL = "http://127.0.0.1:8000"

print("\n" + "="*60)
print("Testing Attempt Counter Display")
print("="*60)

# Get the login page
print("\n1. Getting login page...")
response = requests.get(f"{BASE_URL}/login")
print(f"   Status: {response.status_code}")

# Try 3 failed attempts
for attempt in range(1, 4):
    print(f"\n{attempt}. Attempting login with wrong password...")
    response = requests.post(
        f"{BASE_URL}/login",
        data={"username": "admin", "password": "wrongpassword123"},
        allow_redirects=False
    )
    
    print(f"   Status: {response.status_code}")
    
    # Check if attempt counter is in the response
    if "attempt" in response.text.lower():
        # Extract the relevant line
        for line in response.text.split('\n'):
            if 'attempt' in line.lower() and ('remaining' in line.lower() or 'error-message' in line):
                print(f"   ✓ Found: {line.strip()[:80]}")
                break
    
    if "Invalid username or password" in response.text:
        print(f"   ✓ Error message displayed")

print("\n" + "="*60)
print("✓ Attempt counter display test complete!")
print("="*60 + "\n")
