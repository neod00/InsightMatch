import os
from dotenv import load_dotenv

load_dotenv('server/.env')

keys = ['GOOGLE_API_KEY', 'DART_API_KEY', 'TYPE7_API_KEY']
print("Checking API Keys...")
for k in keys:
    val = os.environ.get(k)
    print(f"{k}: {'PRESENT' if val else 'MISSING'} (Len: {len(str(val)) if val else 0})")
