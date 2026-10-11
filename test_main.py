import requests
import time
import os

URL = os.getenv("API_URL", "http://localhost:8000")

def run_tests():
    for _ in range(10):
        try:
            r = requests.get(f"{URL}/health")
            if r.status_code in [200, 503]:
                break
        except Exception:
            time.sleep(1)
    else:
        print("giving up")
        return

    try:
        r = requests.get(f"{URL}/health")
        print("Health:", r.status_code, r.json())
        assert r.status_code == 200
    except Exception as e:
        print("Service not up:", e)
        return

    # cleanup from previous runs
    import pymongo
    client = pymongo.MongoClient("mongodb://localhost:27017")
    db = client["attendance_db"]
    db.employees.delete_many({"emp_code": "EMP0100"})
    db.attendance_logs.delete_many({"emp_code": "EMP0100"})

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
    assert r.status_code == 201
    
    r2 = requests.post(f"{URL}/employees", json=payload)
    print("Employee dup create:", r2.status_code)
    assert r2.status_code == 409

    r = requests.get(f"{URL}/employees?page=1&page_size=20")
    print("Employee list size:", len(r.json()["items"]), "total:", r.json()["total"])

    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100"})
    print("Punch in:", r.status_code, r.json())
    assert r.status_code == 201

    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100"})
    print("Punch in dup:", r.status_code)
    assert r.status_code == 409
    
    print("ALL TESTS PASSED")

run_tests()
