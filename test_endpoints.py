import requests
import threading
import os
from datetime import datetime, timezone
from unittest.mock import patch

URL = os.getenv("API_URL", "http://localhost:8000")


def assert_stale_patch_returns_conflict(db):
    from fastapi import HTTPException
    from app import main as api

    collection = api.db.attendance_logs
    record = collection.find_one({"emp_code": "EMP0200", "date": "2026-07-06"})
    original_update_one = collection.update_one
    winning_punch_out = 1783350000000

    def as_utc_datetime(epoch_ms):
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(tzinfo=None)

    winning_history_entry = {
        "at": datetime.now(timezone.utc).replace(tzinfo=None),
        "by": "concurrent_writer",
        "reason": "winning concurrent correction",
        "changes": {
            "punch_out": {
                "from": record["punch_out"],
                "to": as_utc_datetime(winning_punch_out),
            },
            "work_hours": {"from": 10.5, "to": 11.0},
            "overtime_minutes": {"from": 90, "to": 120},
        },
    }

    def commit_winner_then_attempt_stale_update(filter_doc, update_doc):
        winner_result = original_update_one(
            {
                "_id": record["_id"],
                "history": {"$size": len(record.get("history", []))},
            },
            {
                "$set": {
                    "punch_out": as_utc_datetime(winning_punch_out),
                    "work_hours": 11.0,
                    "overtime_minutes": 120,
                },
                "$push": {"history": winning_history_entry},
            },
        )
        assert winner_result.modified_count == 1
        winning_document = collection.find_one({"_id": record["_id"]})
        stale_result = original_update_one(filter_doc, update_doc)
        assert stale_result.modified_count == 0
        return stale_result, winning_document

    class RacingCollection:
        def __getattr__(self, name):
            return getattr(collection, name)

        def update_one(self, filter_doc, update_doc):
            result, winning_document = commit_winner_then_attempt_stale_update(
                filter_doc, update_doc
            )
            self.winning_document = winning_document
            return result

    class RacingDatabase:
        employees = api.db.employees
        attendance_logs = RacingCollection()

    racing_database = RacingDatabase()
    with patch.object(api, "db", racing_database):
        try:
            api.regularize_attendance(
                "EMP0200",
                "2026-07-06",
                api.RegularizeIn(
                    reason="stale competing correction",
                    regularized_by="hr_person",
                    punch_out=1783353600000,
                ),
            )
        except HTTPException as exc:
            assert exc.status_code == 409, exc.detail
        else:
            raise AssertionError("Expected stale compare-and-set to return HTTP 409")

    final_document = db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )
    assert final_document == racing_database.attendance_logs.winning_document
    assert len(final_document["history"]) == len(record.get("history", [])) + 1
    assert final_document["punch_out"] == as_utc_datetime(winning_punch_out)
    assert final_document["work_hours"] == 11.0
    assert final_document["overtime_minutes"] == 120


def assert_punch_out_race_returns_conflict_without_losing_update(db):
    from fastapi import HTTPException
    from app import main as api

    collection = api.db.attendance_logs
    record = collection.find_one({"emp_code": "EMP0200", "date": "2026-07-06"})
    original_update_one = collection.update_one
    winning_punch_out = 1783351800000

    def as_utc_datetime(epoch_ms):
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).replace(tzinfo=None)

    def commit_punch_out_then_attempt_stale_update(filter_doc, update_doc):
        winner_result = original_update_one(
            {"_id": record["_id"]},
            {"$set": {
                "punch_out": as_utc_datetime(winning_punch_out),
                "work_hours": 11.0,
                "overtime_minutes": 120
            }}
        )
        assert winner_result.modified_count == 1
        winning_document = collection.find_one({"_id": record["_id"]})
        stale_result = original_update_one(filter_doc, update_doc)
        assert stale_result.modified_count == 0
        return stale_result, winning_document

    class RacingCollection:
        def __getattr__(self, name):
            return getattr(collection, name)

        def update_one(self, filter_doc, update_doc):
            result, winning_document = commit_punch_out_then_attempt_stale_update(
                filter_doc, update_doc
            )
            self.winning_document = winning_document
            return result

    class RacingDatabase:
        employees = api.db.employees
        attendance_logs = RacingCollection()

    with patch.object(api, "db", RacingDatabase()):
        try:
            api.regularize_attendance(
                "EMP0200",
                "2026-07-06",
                api.RegularizeIn(
                    reason="stale update after punch-out",
                    regularized_by="hr_person",
                    status="WFH"
                )
            )
        except HTTPException as exc:
            assert exc.status_code == 409, exc.detail
        else:
            raise AssertionError("Expected stale PATCH after punch-out to return HTTP 409")

    final_document = db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )
    assert final_document == RacingDatabase.attendance_logs.winning_document
    assert final_document["status"] == record["status"]
    assert final_document["punch_out"] == as_utc_datetime(winning_punch_out)
    assert final_document["work_hours"] == 11.0
    assert final_document["overtime_minutes"] == 120
    assert final_document["history"] == record.get("history", [])


def run_tests():
    import pymongo
    client = pymongo.MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    db = client[os.getenv("MONGO_DB", "attendance_db")]
    db.employees.delete_many({"emp_code": "EMP0200"})
    db.attendance_logs.delete_many({"emp_code": "EMP0200"})

    from app.main import get_attendance_date
    assert get_attendance_date(
        datetime(2026, 7, 7, 0, 29, 59, tzinfo=timezone.utc), "22:00", "06:00"
    ) == "2026-07-06"
    assert get_attendance_date(
        datetime(2026, 7, 7, 0, 30, 0, tzinfo=timezone.utc), "22:00", "06:00"
    ) == "2026-07-07"

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

    invalid_join_date = requests.post(f"{URL}/employees", json={
        "emp_code": "EMP0201",
        "name": "Invalid date",
        "email": "invalid-date@example.com",
        "department": "IT",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-02-30"
    })
    assert invalid_join_date.status_code == 422, invalid_join_date.text

    # punch in
    # let's say 2026-07-06 09:30 IST is 04:00 UTC
    ts_in = 1783310400000
    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0200", "punched_at": ts_in})
    assert r.status_code == 201, r.text

    for invalid_timestamp in (ts_in + 0.0, str(ts_in)):
        invalid_punch = requests.post(
            f"{URL}/attendance/punch-in",
            json={"emp_code": "EMP0200", "punched_at": invalid_timestamp}
        )
        assert invalid_punch.status_code == 422, invalid_punch.text

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
        "reason": "invalid absent punch",
        "regularized_by": "hr_person",
        "status": "ABSENT",
        "punch_out": ts_out
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
    no_op_history = db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )["history"]
    assert no_op_history == []

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

    history_after_single_patch = db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )["history"]
    assert len(history_after_single_patch) == 1
    r = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
        "reason": "still unchanged",
        "regularized_by": "hr_person"
    })
    assert r.status_code == 422, r.text
    assert db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )["history"] == history_after_single_patch

    assert_stale_patch_returns_conflict(db)
    assert_punch_out_race_returns_conflict_without_losing_update(db)

    # Exercise overlapping clients without assuming which requests read before a commit.
    concurrent_punch_outs = [1783350060000 + minute * 60000 for minute in range(5)]
    
    patch_results = []
    patch_barrier = threading.Barrier(5)

    def do_patch(punch_out):
        patch_barrier.wait()
        res = requests.patch(f"{URL}/attendance/EMP0200/2026-07-06", json={
            "reason": f"parallel update {punch_out}",
            "regularized_by": "hr_person",
            "punch_out": punch_out
        })
        patch_results.append((punch_out, res.status_code, res.text))
        
    history_before_concurrency = len(db.attendance_logs.find_one(
        {"emp_code": "EMP0200", "date": "2026-07-06"}
    )["history"])
    threads = [
        threading.Thread(target=do_patch, args=(punch_out,))
        for punch_out in concurrent_punch_outs
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    # Requests that read stale state conflict; requests that read newer state can commit.
    patch_statuses = [status for _, status, _ in patch_results]
    assert all(status in (200, 409) for status in patch_statuses), patch_results
    assert all("Concurrent regularization conflict" in body
               for _, status, body in patch_results if status == 409), patch_results
    print("Concurrent PATCH statuses:", patch_statuses)
    
    stored = db.attendance_logs.find_one({"emp_code": "EMP0200", "date": "2026-07-06"})
    successful_reasons = {
        f"parallel update {punch_out}"
        for punch_out, status, _ in patch_results
        if status == 200
    }
    assert len(stored["history"]) == history_before_concurrency + patch_statuses.count(200)
    assert {
        entry["reason"]
        for entry in stored["history"]
        if entry["reason"].startswith("parallel update ")
    } == successful_reasons
    assert stored["work_hours"] is not None
    assert stored["overtime_minutes"] >= 120
    print("Final Mongo attendance:", {
        "work_hours": stored["work_hours"],
        "punch_out": stored["punch_out"],
        "history": stored["history"],
    })

    print("Success Part B tests")

run_tests()
