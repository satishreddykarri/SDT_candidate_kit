import requests
import os

URL = os.getenv("API_URL", "http://localhost:8000")

def test_dept_summary():
    month = "2026-07"
    r = requests.get(f"{URL}/analytics/departments/summary", params={"month": month})
    print("Department summary:", r.status_code, r.json())
    assert r.status_code == 200

test_dept_summary()
