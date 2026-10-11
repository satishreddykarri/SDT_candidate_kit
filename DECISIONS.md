# Decisions

## 1. Indexes
The following indexes were chosen to support the query patterns defined in the business rules and analytics requirements:
- `employees.emp_code`: Unique index for efficient employee lookup and to enforce uniqueness.
- `attendance_logs.emp_code`, `attendance_logs.date`: Compound unique index to prevent duplicate punch-ins per employee per day and optimize queries by ID and date.
- `employees.department`: Optimized department-based filtering and aggregation.
- `attendance_logs.date`, `attendance_logs.emp_code`: Optimized attendance listing and sorting by date.
- `attendance_logs.status`: Optimized status-based filtering.

## 2. Duplicate Concurrent Punch-in Handling
Concurrent punch-in requests are handled using a `try/except` block with a `DuplicateKeyError` catch while attempting to insert a record into `attendance_logs`. A compound unique index on `(emp_code, date)` in MongoDB ensures only one record is successfully created; duplicates return HTTP 409.

## 3. Leaderboard Competition Ranking
Standard competition ranking logic (`rank` 1, 2, 2, 4) is implemented using MongoDB's `$setWindowFields` aggregation stage with the `$rank` operator. The rank is based only on `total_late_minutes`, so equal totals share a rank and the subsequent rank is skipped. A separate sort by employee code makes tied rows deterministic without affecting their ranks. Limits apply strictly to the rank value (`rank <= limit`).

## 4. Department Headcount Calculation
Department headcount per R9 includes all employees with `joined_on` on or before the analysis day, regardless of their attendance status or number of logs. This uses a MongoDB `$lookup` and `$size` operation against the `employees` collection filtered by `joined_on`.

## 5. Atomic attendance corrections
PATCH compares the fields it read (including punch times and computed values) and the current history-array size in its MongoDB update filter. The update writes attendance fields with `$set` and appends the audit entry with `$push` in the same single-document operation. If another writer changed the record after the read, the filter matches nothing and the API returns 409; it does not retry using newer state.

## 6. Working-day analytics
MongoDB computes weekday membership in the aggregation pipelines. `$dayOfWeek` numbers Sunday as 1 and Saturday as 7, so values 2 through 6 represent Monday through Friday. Monthly and department-summary `present_days` exclude weekend records; late and overtime metrics continue to include them. Department trend reports daily presence on all days but returns a null attendance rate on weekends as specified by the contract.

## 7. Scaling to 100x Dataset Size
- **Aggregation Pipeline Optimization:** Heavy use of index-backed `$match` filters at the start of all pipelines is essential. Avoiding `COLLSCAN` via proper compound indexes (`index(emp_code, date)`) is mandatory for operations that list or aggregate logs.
- **Pre-computation vs Aggregation:** For massive datasets, analytics endpoints (`/monthly`, `/summary`, `/trend`) should evaluate moving to materialized views or an incremental pre-calculation approach rather than expensive on-the-fly aggregations if performance requirements significantly exceed current latency targets.
- **Hardware/Sharding:** At 100x scale, distributing the sharded collection based on `emp_code` would allow database-level horizontal scaling if concurrent load bottlenecks single-node ingest.
