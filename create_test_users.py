import requests
import json

base_url = "http://localhost:5000/api/auth/signup"

users = [
    {"email": "company@test.com", "password": "password123", "name": "테스트기업", "role": "company"},
    {"email": "consultant@test.com", "password": "password123", "name": "김컨설턴트", "role": "consultant"}
]

for user in users:
    try:
        response = requests.post(base_url, json=user)
        print(f"Registering {user['email']}: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error registering {user['email']}: {e}")
