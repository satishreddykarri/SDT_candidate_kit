"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv(override=False)

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent index creation
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.employees.create_index("department")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index("status")
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def truncate_to_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)

def dt_to_epoch(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    return int(dt.timestamp() * 1000)

def format_attendance_doc(d: dict) -> dict:
    d.pop("_id", None)
    d["punch_in"] = dt_to_epoch(d.get("punch_in"))
    d["punch_out"] = dt_to_epoch(d.get("punch_out"))
    for hs in d.get("history", []):
        hs["at"] = dt_to_epoch(hs.get("at"))
        for k in ["punch_in", "punch_out"]:
            if k in hs.get("changes", {}):
                hs["changes"][k]["from"] = dt_to_epoch(hs["changes"][k].get("from"))
                hs["changes"][k]["to"] = dt_to_epoch(hs["changes"][k].get("to"))
    return d

def get_attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    t_ist = punch_in_utc.astimezone(IST)
    if shift_end <= shift_start:
        h, m = map(int, shift_end.split(":"))
        if t_ist.hour < h or (t_ist.hour == h and t_ist.minute < m) or (t_ist.hour == h and t_ist.minute == m and t_ist.second == 0):
            return (t_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    return t_ist.strftime("%Y-%m-%d")

def compute_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start_ist = datetime.strptime(attendance_date, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=IST)
    delta_seconds = (punch_in_utc - start_ist).total_seconds()
    if delta_seconds > 10 * 60:
        return int(delta_seconds // 60)
    return 0

def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    # Truncate to seconds per R1
    duration = truncate_to_seconds(punch_out_utc) - truncate_to_seconds(punch_in_utc)
    diff_seconds = Decimal(str(duration.total_seconds()))
    hours = diff_seconds / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out_utc: datetime, shift_end: str, shift_start: str, attendance_date: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end_ist = datetime.strptime(attendance_date, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=IST)
    if shift_end <= shift_start:
        end_ist += timedelta(days=1)
    delta_seconds = (punch_out_utc - end_ist).total_seconds()
    minutes = int(delta_seconds // 60)
    return minutes if minutes >= 30 else 0


class EmployeeMonthly(BaseModel):
    emp_code: str
    month: str
    working_days: int
    present_days: float
    leave_days: int
    late_count: int
    total_late_minutes: int
    total_overtime_minutes: int
    attendance_pct: Optional[float]

class DepartmentSummaryItem(BaseModel):
    department: str
    headcount: int
    present_days: float
    avg_work_hours: Optional[float]
    late_count: int
    total_late_minutes: int
    leave_count: int
    on_duty_count: int

class DepartmentSummary(BaseModel):
    month: str
    items: list[DepartmentSummaryItem]

class LeaderboardItem(BaseModel):
    rank: int
    emp_code: str
    name: str
    department: str
    total_late_minutes: int
    late_count: int

class Leaderboard(BaseModel):
    month: str
    items: list[LeaderboardItem]

class TrendItem(BaseModel):
    date: str
    is_working_day: bool
    headcount: int
    present_count: float
    late_count: int
    attendance_rate: Optional[float]
    moving_avg_7d: Optional[float]

class Trend(BaseModel):
    department: str
    items: list[TrendItem]

class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field("18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str

    @model_validator(mode='after')
    def check_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)


@app.get("/analytics/employees/{emp_code}/monthly", response_model=EmployeeMonthly)
def get_employee_monthly(emp_code: str, month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    year, m = map(int, month.split('-'))
    import calendar
    days_in_month = calendar.monthrange(year, m)[1]

    start_date = datetime(year, m, 1, tzinfo=IST)
    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d").replace(tzinfo=IST)

    effective_start = max(start_date, joined_on)
    end_date = datetime(year, m, days_in_month, 23, 59, 59, tzinfo=IST)

    working_days = 0
    curr = effective_start
    while curr <= end_date:
        if curr.weekday() < 5:
            working_days += 1
        curr += timedelta(days=1)

    # Aggregate data
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_date.strftime("%Y-%m-%d"), "$lte": end_date.strftime("%Y-%m-%d")}
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$in": [
                                    {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                    [2, 3, 4, 5, 6]
                                ]}
                            ]},
                            {"$cond": ["$half_day", 0.5, 1.0]},
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]

    res = list(db.attendance_logs.aggregate(pipeline))
    data = res[0] if res else {"present_days": 0.0, "leave_days": 0, "late_count": 0, "total_late_minutes": 0, "total_overtime_minutes": 0}

    attendance_pct = None
    if working_days > 0:
        pct = (Decimal(str(data["present_days"])) / Decimal(str(working_days))) * Decimal("100")
        attendance_pct = float(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": data["present_days"],
        "leave_days": data["leave_days"],
        "late_count": data["late_count"],
        "total_late_minutes": data["total_late_minutes"],
        "total_overtime_minutes": data["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary", response_model=DepartmentSummary)
def get_department_summary(month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), department: Optional[str] = None):
    year, m = map(int, month.split('-'))
    import calendar
    days_in_month = calendar.monthrange(year, m)[1]
    month_end_date = f"{year}-{m:02d}-{days_in_month}"

    match_emp = {"joined_on": {"$lte": month_end_date}}
    if department:
        match_emp["department"] = department

    # Get headcounts per department
    headcounts = list(db.employees.aggregate([
        {"$match": match_emp},
        {"$group": {"_id": "$department", "headcount": {"$sum": 1}}}
    ]))
    headcount_map = {h["_id"]: h["headcount"] for h in headcounts}

    # Get attendance metrics
    pipeline = [
        {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.append({
        "$group": {
            "_id": "$emp.department",
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": [
                                {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                [2, 3, 4, 5, 6]
                            ]}
                        ]},
                        {"$cond": ["$half_day", 0.5, 1.0]},
                        0
                    ]
                }
            },
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "work_hours_sum": {"$sum": "$work_hours"},
            "work_hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$ne": ["$work_hours", None]}]}, 1, 0]}}
        }
    })

    stats = list(db.attendance_logs.aggregate(pipeline))

    items = []

    # Merge and format
    for dept, headcount in headcount_map.items():
        dept_stat = next((s for s in stats if s["_id"] == dept), None)

        avg_work_hours = None
        if dept_stat and dept_stat["work_hours_count"] > 0:
            avg_work = Decimal(str(dept_stat["work_hours_sum"])) / Decimal(str(dept_stat["work_hours_count"]))
            avg_work_hours = float(avg_work.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        items.append({
            "department": dept,
            "headcount": headcount,
            "present_days": dept_stat["present_days"] if dept_stat else 0.0,
            "avg_work_hours": avg_work_hours,
            "late_count": dept_stat["late_count"] if dept_stat else 0,
            "total_late_minutes": dept_stat["total_late_minutes"] if dept_stat else 0,
            "leave_count": dept_stat["leave_count"] if dept_stat else 0,
            "on_duty_count": dept_stat["on_duty_count"] if dept_stat else 0
        })

    items.sort(key=lambda x: x["department"])
    return {"month": month, "items": items}

@app.get("/health")
def health():
    try:
        client.admin.command('ping')
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = truncate_to_seconds(datetime.now(timezone.utc))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = dt_to_epoch(doc["created_at"])
    return doc

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    for item in items:
        item["created_at"] = dt_to_epoch(item.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000.0, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    ts = truncate_to_seconds(ts)

    d = get_attendance_date(ts, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return format_attendance_doc(doc)

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Unknown employee")

    if body.punched_at:
        ts = datetime.fromtimestamp(body.punched_at / 1000.0, tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    ts = truncate_to_seconds(ts)

    # find the employee's most recent open record
    doc = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", -1)]
    )
    if not doc or doc.get("status") in ("ABSENT", "LEAVE"):
        raise HTTPException(404, "No eligible open record found")
        
    if doc.get("punch_out") is not None:
        raise HTTPException(409, "Record already punched out")

    punch_in_utc = doc["punch_in"].replace(tzinfo=timezone.utc)
    diff_sec = (ts - punch_in_utc).total_seconds()
    if diff_sec <= 0 or diff_sec > 24 * 3600:
        raise HTTPException(422, "punched_at strictly > punch_in and <= 24 hours required")

    work_hours = compute_work_hours(punch_in_utc, ts)
    overtime_min = compute_overtime(ts, emp["shift_end"], emp["shift_start"], doc["date"])
    half_day = work_hours < 4.50

    res = db.attendance_logs.update_one(
        {"_id": doc["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime_min,
            "half_day": half_day
        }}
    )
    if res.modified_count == 0:
        raise HTTPException(409, "Record already punched out concurrently")

    updated_doc = db.attendance_logs.find_one({"_id": doc["_id"]})
    return format_attendance_doc(updated_doc)

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from > date_to")

    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)

    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
    items = [format_attendance_doc(d) for d in cursor]

    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Unknown employee")

    doc = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not doc:
        raise HTTPException(404, "Record not found for given date")

    old_status = doc.get("status")
    old_in_val = doc.get("punch_in")
    old_in = old_in_val.replace(tzinfo=timezone.utc) if old_in_val else None

    old_out_val = doc.get("punch_out")
    old_out = old_out_val.replace(tzinfo=timezone.utc) if old_out_val else None

    new_status = body.status if body.status is not None else old_status

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
        new_in = None
        new_out = None
    else:
        if body.punch_in is not None:
            new_in = truncate_to_seconds(datetime.fromtimestamp(body.punch_in / 1000.0, tz=timezone.utc))
        else:
            new_in = old_in

        if body.punch_out is not None:
            new_out = truncate_to_seconds(datetime.fromtimestamp(body.punch_out / 1000.0, tz=timezone.utc))
        else:
            new_out = old_out

        if not new_in:
            raise HTTPException(422, "Presence status requires a punch_in")

        if get_attendance_date(new_in, emp["shift_start"], emp["shift_end"]) != date:
            raise HTTPException(422, "punch_in date maps to a different attendance date")

        if new_out:
            diff_sec = (new_out - new_in).total_seconds()
            if diff_sec <= 0 or diff_sec > 24 * 3600:
                raise HTTPException(422, "punch_out must be strictly after punch_in and within 24 hours")

    new_late = compute_late_minutes(new_in, emp["shift_start"], date) if new_in else 0
    if new_in and new_out:
        new_work = compute_work_hours(new_in, new_out)
        new_overtime = compute_overtime(new_out, emp["shift_end"], emp["shift_start"], date)
        new_half = (new_work < 4.50)
    else:
        new_work = None
        new_overtime = 0
        new_half = False

    old_work = doc.get("work_hours")
    old_late = doc.get("late_minutes", 0)
    old_overtime = doc.get("overtime_minutes", 0)
    old_half = doc.get("half_day", False)

    changes = {}
    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}
    if new_in != old_in:
        changes["punch_in"] = {"from": old_in, "to": new_in}
    if new_out != old_out:
        changes["punch_out"] = {"from": old_out, "to": new_out}
    if new_work != old_work:
        changes["work_hours"] = {"from": old_work, "to": new_work}
    if new_late != old_late:
        changes["late_minutes"] = {"from": old_late, "to": new_late}
    if new_overtime != old_overtime:
        changes["overtime_minutes"] = {"from": old_overtime, "to": new_overtime}
    if new_half != old_half:
        changes["half_day"] = {"from": old_half, "to": new_half}

    if not changes:
        raise HTTPException(422, "No actual changes detected")

    history_entry = {
        "at": truncate_to_seconds(datetime.now(timezone.utc)),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    old_history = doc.get("history", [])
    filter_doc = {"_id": doc["_id"]}
    if old_history:
        filter_doc["history"] = {"$size": len(old_history)}
    else:
        filter_doc["$or"] = [{"history": {"$size": 0}}, {"history": {"$exists": False}}]

    update_doc = {
        "$set": {
            "status": new_status,
            "punch_in": new_in,
            "punch_out": new_out,
            "work_hours": new_work,
            "late_minutes": new_late,
            "overtime_minutes": new_overtime,
            "half_day": new_half
        },
        "$push": {"history": history_entry}
    }

    res = db.attendance_logs.update_one(filter_doc, update_doc)
    if res.modified_count == 0:
        raise HTTPException(409, "Concurrent regularization conflict")

    updated_doc = db.attendance_logs.find_one({"_id": doc["_id"]})
    return format_attendance_doc(updated_doc)



# --------------------------------------------------------------------------- #
@app.get("/analytics/leaderboard/late", response_model=Leaderboard)
def get_late_leaderboard(month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    pipeline = [
        {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"}
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}}
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}}
    ])

    res = list(db.attendance_logs.aggregate(pipeline))
    items = [{
        "rank": r["rank"],
        "emp_code": r["_id"],
        "name": r["emp"]["name"],
        "department": r["emp"]["department"],
        "total_late_minutes": r["total_late_minutes"],
        "late_count": r["late_count"]
    } for r in res]

    return {"month": month, "items": items}

@app.get("/analytics/departments/{department}/trend", response_model=Trend)
def get_department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, "from and to must be valid YYYY-MM-DD dates")

    if from_date > to_date:
        raise HTTPException(422, "from > to")

    if (end - start).days >= 92:
        raise HTTPException(422, "Range exceeds 92 days")

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(404, "Unknown department")

    pipeline = [
        {"$documents": [{"date": start}]},
        {"$densify": {
            "field": "date",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [start, end + timedelta(days=1)]
            }
        }},
        {"$addFields": {"date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"day": "$date_str"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "emp"
                }},
                {"$unwind": "$emp"},
                {"$match": {"emp.department": department}}
            ],
            "as": "logs"
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$date_str",
            "date": {"$first": "$date_str"},
            "calendar_date": {"$first": "$date"},
            "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$cond": ["$logs.half_day", 0.5, 1.0]}, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"day": "$date"},
            "pipeline": [
                {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}}
            ],
            "as": "dept_emps"
        }},
        {"$addFields": {
            "headcount": {"$size": "$dept_emps"},
            "is_working_day": {"$in": [{"$dayOfWeek": "$calendar_date"}, [2, 3, 4, 5, 6]]}
        }},
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", {"$convert": {"input": "$headcount", "to": "double"}}]},
                    None
                ]
            }
        }},
        {"$sort": {"date": 1}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]}
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "date": "$date",
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }},
        {"$sort": {"date": 1}}
    ]

    res = list(db.aggregate(pipeline))

    # Apply rounding for rate and moving_avg_7d
    final_items = []
    for item in res:
        # _id is the date string
        formatted_item = {
            "date": item["date"],
            "is_working_day": item["is_working_day"],
            "headcount": item["headcount"],
            "present_count": item["present_count"],
            "late_count": item["late_count"],
            "attendance_rate": None,
            "moving_avg_7d": None
        }
        if item["attendance_rate"] is not None:
            formatted_item["attendance_rate"] = float(Decimal(str(item["attendance_rate"])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        if item["moving_avg_7d"] is not None:
            formatted_item["moving_avg_7d"] = float(Decimal(str(item["moving_avg_7d"])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        final_items.append(formatted_item)

    return {"department": department, "items": final_items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100)
):
    if endpoint == "attendance_list":
        page = page or 1
        page_size = page_size or 20
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size
            },
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month required")
        year, m = map(int, month.split('-'))
        start_date = datetime(year, m, 1, tzinfo=IST)
        days_in_month = (datetime(year + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)).day
        end_date = datetime(year, m, days_in_month, 23, 59, 59, tzinfo=IST)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": start_date.strftime("%Y-%m-%d"), "$lte": end_date.strftime("%Y-%m-%d")}}},
            {"$group": {
                "_id": None,
                "present_days": {"$sum": {"$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$in": [
                            {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                            [2, 3, 4, 5, 6]
                        ]}
                    ]},
                    {"$cond": ["$half_day", 0.5, 1.0]},
                    0
                ]}},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }}
        ]
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month required")
        pipeline = [
            {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}}},
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
            {"$unwind": "$emp"},
        ]
        if department:
            pipeline.append({"$match": {"emp.department": department}})
        pipeline.append({"$group": {
            "_id": "$emp.department",
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$in": [
                        {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                        [2, 3, 4, 5, 6]
                    ]}
                ]},
                {"$cond": ["$half_day", 0.5, 1.0]},
                0
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "work_hours_sum": {"$sum": "$work_hours"},
            "work_hours_count": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$ne": ["$work_hours", None]}
                ]},
                1,
                0
            ]}}
        }})
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month required")
        limit = limit or 10
        pipeline = [
            {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}},
            {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}},
            {"$unwind": "$emp"}
        ]
        if department:
            pipeline.append({"$match": {"emp.department": department}})
        pipeline.extend([
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}}
        ])
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422, "department, from, and to required")
        try:
            start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(422, "from and to must be valid YYYY-MM-DD dates")
        if from_date > to_date:
            raise HTTPException(422, "from > to")
        if (end - start).days >= 92:
            raise HTTPException(422, "Range exceeds 92 days")
        pipeline = [
            {"$documents": [{"date": start}]},
            {"$densify": {"field": "date", "range": {"step": 1, "unit": "day", "bounds": [start, end + timedelta(days=1)]}}},
            {"$addFields": {"date_str": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}}}},
            {"$lookup": {"from": "attendance_logs", "let": {"day": "$date_str"}, "pipeline": [
                {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
                {"$unwind": "$emp"},
                {"$match": {"emp.department": department}}
            ], "as": "logs"}},
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {"$group": {
                "_id": "$date_str",
                "date": {"$first": "$date_str"},
                "calendar_date": {"$first": "$date"},
                "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, {"$cond": ["$logs.half_day", 0.5, 1.0]}, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}}
            }},
            {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [
                {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}}
            ], "as": "dept_emps"}},
            {"$addFields": {
                "headcount": {"$size": "$dept_emps"},
                "is_working_day": {"$in": [{"$dayOfWeek": "$calendar_date"}, [2, 3, 4, 5, 6]]}
            }},
            {"$addFields": {"attendance_rate": {"$cond": [
                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                {"$divide": ["$present_count", {"$convert": {"input": "$headcount", "to": "double"}}]},
                None
            ]}}},
            {"$sort": {"date": 1}},
            {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
            {"$project": {"_id": 0, "date": "$date", "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
            {"$sort": {"date": 1}}
        ]
        explain = db.command(
            "explain",
            {"aggregate": 1, "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

# --------------------------------------------------------------------------- #
