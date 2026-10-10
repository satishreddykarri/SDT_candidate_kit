import requests
URL = "http://localhost:8000"

# A. Punch-in status validation
def test_status_validation():
    print("Testing status validation...")
    valid_statuses = ["PRESENT", "WFH", "ON_DUTY"]
    for status in valid_statuses:
        r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100", "status": status})
        assert r.status_code == 201, f"Failed status {status}"
        # Cleanup

    r = requests.post(f"{URL}/attendance/punch-in", json={"emp_code": "EMP0100", "status": "ABSENT"})
    assert r.status_code == 422, f"Expected 422 for ABSENT, got {r.status_code}"
    print("Status validation passed")

# C. Work-hour rounding
def test_work_hour_rounding():
    print("Testing work-hour rounding...")
    # 09:30 to 14:00 = 4.5 hours
    # 09:30 to 13:59:59 = 4.499... -> rounded to 4.50
    # 09:30 to 13:59:58 = 4.4994... -> rounded to 4.50
    # Let's test a value that rounds differently

    # Needs a real attendance record. I'll regularize.

    # I'll just check logic in Python or hit the endpoint
    pass

if __name__ == "__main__":
    test_status_validation()
    test_work_hour_rounding()
