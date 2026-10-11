import requests
import os
import json
from datetime import datetime, timezone
from pymongo import MongoClient

URL = os.getenv("API_URL", "http://localhost:8000")

def test_weekend_record_does_not_count_as_present_day():
    db = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))[
        os.getenv("MONGO_DB", "attendance_db")
    ]
    record_date = "2099-12-26"
    query = {"emp_code": "EMP0001", "date": record_date}
    assert db.attendance_logs.find_one(query) is None, f"Test record already exists: {query}"
    db.attendance_logs.insert_one({
        **query,
        "status": "PRESENT",
        "punch_in": datetime(2099, 12, 26, 4, 17, tzinfo=timezone.utc),
        "punch_out": datetime(2099, 12, 26, 14, 0, tzinfo=timezone.utc),
        "work_hours": 9.72,
        "late_minutes": 17,
        "overtime_minutes": 35,
        "half_day": False,
        "history": []
    })
    try:
        monthly = requests.get(
            f"{URL}/analytics/employees/EMP0001/monthly",
            params={"month": "2099-12"}
        )
        assert monthly.status_code == 200, monthly.text
        metrics = monthly.json()
        assert metrics["present_days"] == 0.0, metrics
        assert metrics["late_count"] == 1, metrics
        assert metrics["total_late_minutes"] == 17, metrics
        assert metrics["total_overtime_minutes"] == 35, metrics

        summary = requests.get(
            f"{URL}/analytics/departments/summary",
            params={"month": "2099-12", "department": "Engineering"}
        )
        assert summary.status_code == 200, summary.text
        engineering = summary.json()["items"][0]
        assert engineering["present_days"] == 0.0, engineering
        assert engineering["late_count"] == 1, engineering
        assert engineering["total_late_minutes"] == 17, engineering
    finally:
        db.attendance_logs.delete_one(query)

def test_dept_summary():
    month = "2026-07"
    r = requests.get(f"{URL}/analytics/departments/summary", params={"month": month})
    print("Department summary:", r.status_code, r.json())
    assert r.status_code == 200, r.text
    departments = {item["department"]: item for item in r.json()["items"]}
    assert departments["Engineering"]["headcount"] == 2, departments
    assert departments["Engineering"]["present_days"] == 3.0, departments
    assert departments["Sales"]["present_days"] == 1.5, departments
    assert departments["Support"]["present_days"] == 2.0, departments

def test_weekday_trend():
    r = requests.get(
        f"{URL}/analytics/departments/Engineering/trend",
        params={"from": "2026-07-06", "to": "2026-07-12"}
    )
    print("Department trend:", r.status_code, r.json())
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 7, items
    assert items[0]["date"] == "2026-07-06", items[0]
    assert items[0]["is_working_day"] is True, items[0]
    assert items[0]["attendance_rate"] == 1.0, items[0]
    assert items[5]["date"] == "2026-07-11", items[5]
    assert items[5]["is_working_day"] is False, items[5]
    assert items[5]["attendance_rate"] is None, items[5]
    assert items[6]["date"] == "2026-07-12", items[6]
    assert items[6]["is_working_day"] is False, items[6]
    assert items[6]["attendance_rate"] is None, items[6]

    oversized_range = requests.get(
        f"{URL}/analytics/departments/Engineering/trend",
        params={"from": "2026-07-01", "to": "2026-10-01"}
    )
    assert oversized_range.status_code == 422, oversized_range.text

def test_late_leaderboard():
    r = requests.get(f"{URL}/analytics/leaderboard/late", params={"month": "2026-07"})
    print("Late leaderboard:", r.status_code, r.json())
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["rank"] == 1, r.json()
    assert r.json()["items"][0]["emp_code"] == "EMP0003", r.json()
    assert r.json()["items"][0]["total_late_minutes"] == 35, r.json()

def test_explain_endpoints():
    cases = [
        ("attendance_list", {}),
        ("employee_monthly", {"emp_code": "EMP0001", "month": "2026-07"}),
        ("department_summary", {"month": "2026-07"}),
        ("late_leaderboard", {"month": "2026-07"}),
        ("department_trend", {"department": "Engineering", "from": "2026-07-06", "to": "2026-07-12"})
    ]
    for endpoint, params in cases:
        r = requests.get(f"{URL}/admin/explain/{endpoint}", params=params)
        print(f"Explain {endpoint}:", r.status_code)
        assert r.status_code == 200, f"{endpoint}: {r.text}"
        details = json.dumps(r.json()["explain"])
        assert any(key in details for key in ("executionStats", "totalDocsExamined", "indexesUsed")), r.json()

test_dept_summary()
test_weekend_record_does_not_count_as_present_day()
test_weekday_trend()
test_late_leaderboard()
test_explain_endpoints()
