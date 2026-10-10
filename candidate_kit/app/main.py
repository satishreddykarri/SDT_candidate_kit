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
    diff_seconds = Decimal(str((punch_out_utc - punch_in_utc).total_seconds()))
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


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
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
        {"emp_code": body.emp_code},
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
    status: Optional[str] = None,
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
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
