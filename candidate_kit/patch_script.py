import re

with open("app/main.py", "r") as f:
    code = f.read()

old_block = """    # find the employee's most recent open record (no filter on ts so we can fail explicitly on <= zero diffs)
    doc = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_out": None},
        sort=[("punch_in", -1)]
    )
    if not doc:
        raise HTTPException(404, "No eligible open record found")"""

new_block = """    # find the employee's most recent open record
    doc = db.attendance_logs.find_one(
        {"emp_code": body.emp_code},
        sort=[("punch_in", -1)]
    )
    if not doc or doc.get("status") in ("ABSENT", "LEAVE"):
        raise HTTPException(404, "No eligible open record found")
        
    if doc.get("punch_out") is not None:
        raise HTTPException(409, "Record already punched out")"""

code = code.replace(old_block, new_block)

with open("app/main.py", "w") as f:
    f.write(code)

print("done")
