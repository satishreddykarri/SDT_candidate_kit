import requests
import os

URL = os.getenv("API_URL", "http://localhost:8000")

def run_tests():
    emp_code = "EMP0001"
    month = "2026-07"
    
    r = requests.get(f"{URL}/analytics/employees/{emp_code}/monthly", params={"month": month})
    print("Employee monthly:", r.status_code, r.json())
    assert r.status_code == 200

run_tests()
