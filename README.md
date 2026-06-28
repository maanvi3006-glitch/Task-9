# PlaceMux · Phase 2 · Task 9
## Revenue Intelligence System — Failure Handling & Resilience

> **Decision-grade revenue analytics for PlaceMux.**
> Every number is sourced, validated, and explainable on the spot.

---

## What this is

A production-style Python analytics project that builds a complete
**Revenue Intelligence System** on top of a SQLite backend.
It tracks ARPU, cohort revenue, payment failure rates, and data
quality — and surfaces everything through a live Streamlit dashboard.

This is Task 9 of Phase 2 (Week 3) of the PlaceMux Data Analyst programme.

---

## Project structure

```
placemux/
│
├── config.py              Central config — all paths, thresholds, constants
├── create_database.py     Create/reset SQLite schema (5 tables, 9 indexes)
├── generate_data.py       Synthetic data generation via Faker
├── load_data.py           CSV → SQLite bulk loader (idempotent)
├── metrics_engine.py      Core KPI computation via SQL
├── cohort_engine.py       Cohort revenue matrix and retention analytics
├── payment_failure.py     Failure monitoring, retry analysis, reconciliation
├── validation.py          8-point KPI and data quality validation suite
├── dashboard.py           4-page Streamlit Revenue Intelligence dashboard
│
├── requirements.txt
├── README.md
├── SCHEMA.md
├── placemux.db            SQLite database (generated on first run)
│
├── data/
│   ├── users.csv
│   ├── payments.csv
│   ├── applications.csv
│   ├── revenue_events.csv
│   └── payment_failures.csv
│
├── sql/
│   ├── arpu.sql
│   ├── cohort_revenue.sql
│   ├── revenue_growth.sql
│   └── failure_rate.sql
│
├── reports/
│   ├── revenue_report.csv
│   ├── cohort_report.csv
│   └── quality_report.csv
│
└── docs/
    ├── architecture.md
    └── metrics_dictionary.md
```

---

## Quickstart

### 1 — Prerequisites

- Python 3.11+
- pip

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Run the full pipeline (one command each)

```bash
# Step 1: Create the database schema
python create_database.py

# Step 2: Generate synthetic data (CSVs land in data/)
python generate_data.py

# Step 3: Load CSVs into SQLite
python load_data.py

# Step 4: Launch the dashboard
streamlit run dashboard.py
```

### 4 — Full reset (if you want a clean slate)

```bash
python create_database.py --reset
python generate_data.py
python load_data.py
streamlit run dashboard.py
```

---

## Run individual modules

```bash
# Compute and print all revenue KPIs
python metrics_engine.py

# Print cohort revenue matrix and export CSV
python cohort_engine.py

# Run failure monitor, retry analysis, reconciliation
python payment_failure.py

# Run all 8 validation checks and export quality_report.csv
python validation.py
```

---

## Dashboard pages

| Page | What it shows |
|---|---|
| **Revenue Overview** | Total Revenue, ARPU, Daily Trend, MoM Growth, Gateway Revenue |
| **Cohort Revenue** | Cohort Heatmap, Retention %, ARPU by Cohort, Conversion Rate |
| **Failure Monitoring** | Recovery KPIs, Gateway Health, Failure Trend, Reason Breakdown |
| **Data Quality** | 8-check validation scorecard, Duplicates, Null scan, Freshness |

---

## Data model (summary)

| Table | Rows (approx.) | Purpose |
|---|---|---|
| `users` | 2,000 | Signed-up users with cohort month |
| `payments` | 7,650 | All payment attempts incl. failures and duplicates |
| `applications` | 5,000 | User–company applications with fee |
| `revenue_events` | 8,265 | Event log for every money movement |
| `payment_failures` | ~1,350 | Detailed failure records with recovery tracking |

See `SCHEMA.md` for full DDL and column definitions.

---

## Key metrics

| Metric | Formula | Decision |
|---|---|---|
| **ARPU** | Total Revenue / Paying Users | Pricing & retention signal |
| **MoM Growth %** | (This Month − Last Month) / Last Month | Trend alert |
| **Failure Rate** | Failed Payments / Total Payments | Gateway SLA alert |
| **Recovery Rate** | Recovered Failures / Total Failures | Retry logic health |
| **Revenue Lost** | Sum of unrecovered failure amounts | Leakage prioritisation |
| **Cohort Retention %** | Cohort Revenue(M) / Cohort Revenue(M0) | Monetisation health |

See `docs/metrics_dictionary.md` for full definitions and SQL sources.

---

## Realistic dirty data (intentional)

The generated dataset includes:

- **150 duplicate payment rows** (~2% rate) — detectable by `user_id + amount + date` fingerprint
- **~18 NULL active flags** in `users.active` (~1% rate) — simulates incomplete user records
- **Payment spikes** in March and September — simulates campus hiring season campaigns
- **35% failure recovery rate** — with 1–7 day recovery windows and partial amounts
- **3% chargeback rate** on successful payments — delayed by 1–30 days

All of the above are detected and reported on the Data Quality dashboard page.

---

## Validation checks

| Check | PASS condition |
|---|---|
| `validate_arpu` | ARPU ≥ ₹1 and paying_users > 0 |
| `validate_revenue` | Total revenue > 0, no null/negative amounts |
| `validate_cohort` | All cohort_months valid, no pre-signup payments |
| `validate_failure_rate` | Failure rate ≤ 40% |
| `detect_duplicates` | Duplicate rate ≤ 5% |
| `detect_nulls` | No nulls in PK/amount columns; null rate ≤ 10% |
| `freshness_check` | Latest payment within 24h (WARN in dev — data ends 2024-12-31) |
| `orphan_check` | All payments have a matching user row |

---

## Deployment (Streamlit Cloud)

1. Push the repo to GitHub (include `placemux.db` or the pipeline scripts).
2. Set the main file to `dashboard.py`.
3. No secrets required — SQLite runs entirely from the local filesystem.
4. The dashboard queries live SQLite on every page load (5-minute cache TTL).

---

## Engineering principles followed

- **SQL does the work** — no metric is computed in Python if SQL can do it
- **Modular files** — one responsibility per module, no giant scripts
- **Idempotent pipeline** — every step is safe to re-run
- **Structured validation** — `ValidationResult` dataclass, not bare print statements
- **Centralised config** — all constants in `config.py`, zero magic numbers elsewhere
- **Error handling** — every SQL call wrapped in try/except with structured logging
- **FK-safe loading** — parent tables before child tables, FK re-enabled after load

---

## Author

PlaceMux · Altrodav Technologies Pvt. Ltd. · Phase 2 Industry Immersion
Data Analyst Track · Task 9 · Week 3
