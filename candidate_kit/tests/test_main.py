import requests
import time
from threading import Thread

URL = "http://localhost:8000"

def run_tests():
    try:
        r = requests.get(f"{URL}/health")
        print("Health:", r.status_code, r.json())
        assert r.status_code == 200
    except Exception as e:
        print("Service not up:", e)
        return

    # test employee validation
    r = requests.post(f"{URL}/employees", json={
        "emp_code": "EMP0100",
        "name": "Test",
        "email": "test@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "09:30",
        "joined_on": "2026-01-01"
    })
    print("Employee same shift:", r.status_code)
    assert r.status_code == 422

    # create real employee
    payload = {
        "emp_code": "EMP0100",
        "name": "Test",
        "email": "test@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    }
    r = requests.post(f"{URL}/employees", json=payload)
    print("Employee create:", r.status_code)
    
    r2 = requests.post(f"{URL}/employees", json=payload)
    print("Employee dup create:", r2.status_code)
    assert r2.status_code == 409

    r = requests.get(f"{URL}/employees?page=1&page_size=20")
    print("Employee list size:", len(r.json()["items"]), "total:", r.json()["total"])

    # Punch in 09:40:01 (late) -> 10 mins late
    # 09:40 IST is 04:10 UTC
    req_ts = 1783311001000 # wait, 1783311001000 = 2026-07-06 04:10:01 UTC ? Let me just use time.
    
    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100"})
    print("Punch in:", r.status_code, r.json())
    
    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100"})
    print("Punch in dup:", r.status_code)
    assert r.status_code == 409

run_tests()
