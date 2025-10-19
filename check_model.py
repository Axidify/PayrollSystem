#!/usr/bin/env python
"""Check models in the database."""
import sqlite3

conn = sqlite3.connect('data/payroll.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f'Tables in database: {[t[0] for t in tables]}')

# Get all models if table exists
try:
    cursor.execute('SELECT id, code, status, real_name FROM models ORDER BY code')
    models = cursor.fetchall()
    print(f'\nTotal models: {len(models)}')
    for model in models:
        print(f'  - {model["code"]} (ID: {model["id"]}) - Status: {model["status"]} - Real Name: {model["real_name"]}')
except Exception as e:
    print(f'\nError reading models: {e}')

# Get all payouts if table exists
try:
    cursor.execute('SELECT model_id, pay_date, amount, status FROM payouts ORDER BY model_id, pay_date')
    payouts = cursor.fetchall()
    print(f'\nTotal payouts: {len(payouts)}')
    for payout in payouts:
        print(f'  - Model ID: {payout["model_id"]}, Pay Date: {payout["pay_date"]}, Amount: {payout["amount"]}, Status: {payout["status"]}')
        
    # Group by model
    print(f'\nPayouts by model:')
    cursor.execute('SELECT model_id, COUNT(*) as count FROM payouts GROUP BY model_id')
    model_payouts = cursor.fetchall()
    for mp in model_payouts:
        cursor.execute('SELECT code FROM models WHERE id = ?', (mp["model_id"],))
        model = cursor.fetchone()
        code = model["code"] if model else "Unknown"
        print(f'  - Model {code} (ID: {mp["model_id"]}): {mp["count"]} payouts')
except Exception as e:
    print(f'\nError reading payouts: {e}')

conn.close()
