# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the API implementation. Start it with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: review findings and design decisions.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=attendance_db
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```

On Windows PowerShell, use `$env:MONGO_URI="mongodb://localhost:27017"` and
`$env:MONGO_DB="attendance_db"` instead of the `export` commands. `sample_seed.py` replaces the
`employees` and `attendance_logs` collections in the selected database; use it only with a disposable
development database.

MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.
The request-based checks are in `tests/` and at the project root; run them with the API server running
and `API_URL` set if it is not at `http://localhost:8000`.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.
