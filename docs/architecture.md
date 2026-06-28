# PlaceMux · Architecture
## Task 9 — Revenue Intelligence System

### Layer diagram

```
┌─────────────────────────────────────────────────────────┐
│                    dashboard.py                         │
│              Streamlit · 4-page dashboard               │
│      reads live SQLite  ·  5-min cache TTL              │
└──────────┬───────────────┬──────────────┬──────────────┘
           │               │              │
    metrics_engine   cohort_engine  payment_failure  validation
      (KPI SQL)      (cohort SQL)   (failure SQL)   (check SQL)
           │               │              │              │
           └───────────────┴──────────────┴──────────────┘
                                  │
                           config.py (DB_URL)
                                  │
                          placemux.db (SQLite)
                                  │
                     ┌────────────┴────────────┐
                     │                         │
               load_data.py            create_database.py
               (CSV → SQLite)          (DDL + indexes)
                     │
               generate_data.py
               (Faker → CSV)
```

### Data flow

```
generate_data.py  →  data/*.csv
       ↓
load_data.py      →  placemux.db  (5 tables)
       ↓
metrics_engine    →  KPI DataFrames  →  dashboard Page 1
cohort_engine     →  pivot matrices  →  dashboard Page 2
payment_failure   →  failure dicts   →  dashboard Page 3
validation        →  ValidationResult→  dashboard Page 4
```

### Design decisions

**SQLite over Postgres** — zero-config, single-file, ships with Python.
WAL mode gives the concurrent-read behaviour needed for Streamlit.

**SQL does the work** — all aggregations happen inside SQLite, not in
pandas. This keeps Python thin, makes queries auditable, and means the
SQL files in `sql/` can be run directly against the database for spot-checks.

**Modular files** — one module per concern. The dashboard imports
four engine classes; it never touches SQL strings directly.

**Idempotent pipeline** — `create_database.py` uses `IF NOT EXISTS`;
`load_data.py` truncates before inserting. Safe to re-run at any time.

**`@st.cache_data(ttl=300)`** — caches every data-loader for 5 minutes.
Streamlit re-runs the script on every interaction; without caching,
every widget click would fire all SQL queries simultaneously.
