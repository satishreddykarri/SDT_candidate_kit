import requests
import os

URL = os.getenv("API_URL", "http://localhost:8000")

def run_tests():
    emp_code = "EMP0001"
    month = "2026-07"
    
    r = requests.get(f"{URL}/analytics/employees/{emp_code}/monthly", params={"month": month})
    print("Employee monthly:", r.status_code, r.json())
    assert r.status_code == 200
    monthly = r.json()
    assert monthly["working_days"] == 23, monthly
    assert monthly["present_days"] == 2.0, monthly
    assert monthly["attendance_pct"] == 8.70, monthly

    zero_days = requests.get(
        f"{URL}/analytics/employees/{emp_code}/monthly",
        params={"month": "1900-01"}
    )
    print("Zero-working-day monthly:", zero_days.status_code, zero_days.json())
    assert zero_days.status_code == 200, zero_days.text
    assert zero_days.json()["working_days"] == 0, zero_days.json()
    assert zero_days.json()["attendance_pct"] is None, zero_days.json()

run_tests()
