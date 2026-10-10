import requests
import time
import threading
import os
from datetime import timezone

URL = os.getenv("API_URL", "http://localhost:8000")

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

    # Invalid status and punch times remain validation errors.
    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "invalid status",
        "regularized_by": "hr_person",
        "status": "INVALID"
    })
    assert r.status_code == 422, r.text

    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "invalid punch",
        "regularized_by": "hr_person",
        "punch_out": ts_in - 1000
    })
    assert r.status_code == 422, r.text

    # Manual correction - no change yields 422
    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "forgot punch",
        "regularized_by": "hr_person"
    })
    assert r.status_code == 422, r.text

    # A valid PATCH succeeds by itself and appends one history item.
    ts_single_out = 1783348200000
    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "single update",
        "regularized_by": "hr_person",
        "punch_out": ts_single_out
    })
    assert r.status_code == 200, r.text
    assert len(r.json()["history"]) == 1
    print("Single PATCH status:", r.status_code)

    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "still unchanged",
        "regularized_by": "hr_person"
    })
    assert r.status_code == 422, r.text

    # Manual correction - change punch_out to 20:30 IST (15:00 UTC)
    ts_new_out = 1783350000000 
    
    patch_results = []
    patch_barrier = threading.Barrier(5)

    def do_patch():
        patch_barrier.wait()
        res = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
            "reason": "updated punch",
            "regularized_by": "hr_person",
            "punch_out": ts_new_out
        })
        patch_results.append((res.status_code, res.text))
        
    threads = [threading.Thread(target=do_patch) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    # exactly one 200, rest 409
    patch_statuses = [status for status, _ in patch_results]
    assert patch_statuses.count(200) == 1, patch_results
    assert patch_statuses.count(409) == 4, patch_results
    assert all("Concurrent regularization conflict" in body
               for status, body in patch_results if status == 409), patch_results
    print("Concurrent PATCH statuses:", patch_statuses)
    
    # Check updated record
    rec = requests.get(f"{URL}/attendance?emp_code=EMP0200").json()["items"][0]
    assert rec["work_hours"] == 11.0 # 09:30 to 20:30
    assert len(rec["history"]) == 2
    
    hist = rec["history"][-1]
    assert hist["reason"] == "updated punch"
    assert hist["by"] == "hr_person"
    assert hist["changes"]["work_hours"]["to"] == 11.0

    # Verify the stored BSON timestamps as UTC, independent of the host's local timezone.
    stored = db.attendance_logs.find_one({"emp_code": "EMP0200", "date": "2026-07-06"})
    assert int(stored["punch_out"].replace(tzinfo=timezone.utc).timestamp() * 1000) == ts_new_out
    assert len(stored["history"]) == 2
    assert [entry["reason"] for entry in stored["history"]] == ["single update", "updated punch"]
    stored_punch_out = stored["history"][-1]["changes"]["punch_out"]["to"]
    assert int(stored_punch_out.replace(tzinfo=timezone.utc).timestamp() * 1000) == ts_new_out
    print("Final Mongo attendance:", {
        "work_hours": stored["work_hours"],
        "punch_out": stored["punch_out"],
        "history": stored["history"],
    })

    print("Success Part B tests")

run_tests()
