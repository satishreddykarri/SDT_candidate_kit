import requests
import time
import threading

URL = "http://localhost:8000"

def run_tests():
    import pymongo
    client = pymongo.MongoClient("mongodb://localhost:27017")
    db = client["attendance_db"]
    db.employees.delete_many({"emp_code": "EMP0200"})
    db.attendance_logs.delete_many({"emp_code": "EMP0200"})

    # create employee
    r = requests.post(f"{URL}/employees", json={
        "emp_code": "EMP0200",
        "name": "Test",
        "email": "test2@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01"
    })
    assert r.status_code == 201

    # punch in
    # let's say 2026-07-06 09:30 IST is 04:00 UTC
    ts_in = 1783310400000
    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0200", "punched_at": ts_in})
    assert r.status_code == 201, r.text

    # try punch out with time < punch_in (error 422)
    r = requests.post(f"{URL}/attendance/punch-out", json={"emp_code": "EMP0200", "punched_at": ts_in - 1000})
    assert r.status_code == 422

    # concurrent punch out
    ts_out = 1783346400000 # 10 hours later = 14:00 UTC = 19:30 IST
    
    results = []
    def do_punch_out():
        res = requests.post(f"{URL}/attendance/punch-out", json={"emp_code": "EMP0200", "punched_at": ts_out})
        results.append(res.status_code)
    
    threads = [threading.Thread(target=do_punch_out) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    # should be exactly one 200, rest 409
    assert results.count(200) == 1, results
    assert results.count(409) == 4, results

    # Get record to check work_hours, overtime
    # 09:30 to 19:30 = 10.0 hours. Overtime = 1 hr (60 mins)
    rec = requests.get(f"{URL}/attendance?emp_code=EMP0200").json()["items"][0]
    assert rec["work_hours"] == 10.0
    assert rec["overtime_minutes"] == 60
    assert rec["half_day"] is False

    # Manual correction - no change yields 422
    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "forgot punch",
        "regularized_by": "hr_person"
    })
    assert r.status_code == 422

    # Manual correction - change punch_out to 20:30 IST (15:00 UTC)
    ts_new_out = 1783350000000 
    
    patch_results = []
    def do_patch():
        res = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
            "reason": "updated punch",
            "regularized_by": "hr_person",
            "punch_out": ts_new_out
        })
        patch_results.append(res.status_code)
        
    threads = [threading.Thread(target=do_patch) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    # exactly one 200, rest 409
    assert patch_results.count(200) == 1, patch_results
    assert patch_results.count(409) == 4, patch_results
    
    # Check updated record
    rec = requests.get(f"{URL}/attendance?emp_code=EMP0200").json()["items"][0]
    assert rec["work_hours"] == 11.0 # 09:30 to 20:30
    assert len(rec["history"]) == 1
    
    hist = rec["history"][0]
    assert hist["reason"] == "updated punch"
    assert hist["by"] == "hr_person"
    assert hist["changes"]["punch_out"]["to"] == ts_new_out
    assert hist["changes"]["work_hours"]["to"] == 11.0

    print("Success Part B tests")

run_tests()
