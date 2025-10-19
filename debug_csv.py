#!/usr/bin/env python
"""Debug CSV column detection."""
import csv
import io

# Sample CSV content - paste your actual headers here
csv_content = """Code	Status	Real Name	Working Name	Start Date	Payment Method	Payment Frequency	Monthly Amount	Crypto Wallet	Pay Date	Amount	Status (Payment)	Notes
test	Active	test	test	9/10/2025	wise	weekly	1000	test crypto wallet	31/10/2025	500	paid	
test2	active	test2	test2	1/9/2025	cash	biweekly	2000		14/9/2025	1000	paid	
test2	active	test2	test2	1/9/2025	cash	biweekly	2000		30/9/2025	1000	paid	"""

text_stream = io.StringIO(csv_content)
reader = csv.DictReader(text_stream)

print("Raw fieldnames from CSV:")
print(reader.fieldnames)
print()

print("Normalized field names:")
field_names_lower = {f.lower().replace(' ', '_').replace('(', '').replace(')', ''): f for f in reader.fieldnames}
for normalized, original in sorted(field_names_lower.items()):
    print(f"  '{normalized}' -> '{original}'")

print()
required_fields = {'status', 'code', 'real_name', 'working_name', 'start_date', 
                  'payment_method', 'payment_frequency', 'monthly_amount', 'crypto_wallet'}
optional_payment_fields = {'pay_date', 'amount', 'status_payment'}

print("Required fields present:", required_fields <= set(field_names_lower.keys()))
print("Optional payment fields present:", optional_payment_fields <= set(field_names_lower.keys()))
print("Missing required:", required_fields - set(field_names_lower.keys()))
print("Missing optional:", optional_payment_fields - set(field_names_lower.keys()))

print("\nFirst row values:")
for row in reader:
    print(row)
    break
