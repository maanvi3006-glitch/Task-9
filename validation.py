"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
validation.py

Responsibility:
    KPI validation, data quality checks, and freshness monitoring.
    Every check runs via SQL and returns a structured ValidationResult.

    A dashboard nobody trusts is worse than no dashboard.
    These checks are what make the numbers trustworthy.

Validators:
    validate_arpu()           — ARPU within sane bounds
    validate_revenue()        — total revenue is positive and non-null
    validate_cohort()         — cohort matrix is complete and non-negative
    validate_failure_rate()   — failure rate within expected threshold
    detect_duplicates()       — duplicate payment fingerprints
    detect_nulls()            — null counts in critical columns
    freshness_check()         — most recent data within SLA window
    run_all()                 — execute all checks and return summary

Usage:
    from validation import Validator
    v = Validator()
    results = v.run_all()
    for r in results:
        print(r)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

from config import (
    DB_URL,
    LOG_LEVEL,
    MAX_DUPLICATE_RATE,
    MAX_NULL_RATE,
    MAX_FAILURE_RATE,
    MIN_ARPU,
    FRESHNESS_SLA_HOURS,
    REPORTS_DIR,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("validation")


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------

class Status(str, Enum):
    PASS  = "PASS"
    WARN  = "WARN"
    FAIL  = "FAIL"
    ERROR = "ERROR"


@dataclass
class ValidationResult:
    """
    Structured result for a single validation check.
    Used by the dashboard Data Quality page.
    """
    check_name: str
    status:     Status
    message:    str
    metric:     Any = field(default=None)
    threshold:  Any = field(default=None)
    detail_df:  Any = field(default=None)

    def __str__(self) -> str:
        m = f"  metric={self.metric}"    if self.metric    is not None else ""
        t = f"  threshold={self.threshold}" if self.threshold is not None else ""
        return (
            f"[{self.status.value:5}]  {self.check_name:<42}"
            f"{m}{t}  — {self.message}"
        )

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "status":     self.status.value,
            "message":    self.message,
            "metric":     self.metric,
            "threshold":  self.threshold,
        }


# ---------------------------------------------------------------------------
# SQL library
# ---------------------------------------------------------------------------

SQL_VALIDATE_ARPU = """
SELECT
    ROUND(
        CAST(SUM(amount) AS REAL) / NULLIF(COUNT(DISTINCT user_id), 0), 2
    ) AS arpu,
    COUNT(DISTINCT user_id) AS paying_users,
    SUM(amount)             AS total_revenue
FROM payments
WHERE status = 'success';
"""

SQL_VALIDATE_REVENUE = """
SELECT
    SUM(CASE WHEN status = 'success' THEN amount ELSE 0 END) AS total_revenue,
    COUNT(CASE WHEN status = 'success' THEN 1 END)           AS success_count,
    COUNT(CASE WHEN amount <= 0       THEN 1 END)            AS non_positive_amounts,
    COUNT(CASE WHEN amount IS NULL    THEN 1 END)            AS null_amounts
FROM payments;
"""

SQL_VALIDATE_COHORT = """
SELECT
    COUNT(*) FILTER (WHERE cohort_month IS NULL)                          AS null_cohort_months,
    COUNT(*) FILTER (WHERE cohort_month != STRFTIME('%Y-%m', signup_date)) AS mismatched_cohort_months,
    COUNT(DISTINCT cohort_month)                                          AS distinct_cohorts
FROM users;
"""

SQL_VALIDATE_COHORT_PAYMENTS = """
SELECT COUNT(*) AS pre_signup_payments
FROM payments p
JOIN users u ON p.user_id = u.user_id
WHERE p.payment_date < u.signup_date
  AND p.status = 'success';
"""

SQL_VALIDATE_FAILURE_RATE = """
SELECT
    COUNT(*)                                                          AS total_payments,
    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)               AS failed_payments,
    ROUND(
        CAST(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                                 AS failure_rate_pct
FROM payments;
"""

SQL_DETECT_DUPLICATES = """
WITH dupes AS (
    SELECT
        user_id, payment_date, amount,
        COUNT(payment_id) AS cnt
    FROM payments
    GROUP BY user_id, payment_date, amount
    HAVING COUNT(payment_id) > 1
)
SELECT
    COUNT(*)                                                          AS duplicate_groups,
    SUM(cnt)                                                          AS total_duplicate_rows,
    SUM(cnt) - COUNT(*)                                               AS excess_rows,
    CAST((SUM(cnt) - COUNT(*)) AS REAL)
        / NULLIF((SELECT COUNT(*) FROM payments), 0)                  AS duplicate_rate
FROM dupes;
"""

SQL_DETECT_DUPLICATES_DETAIL = """
SELECT
    user_id, payment_date, amount, gateway,
    COUNT(payment_id)        AS duplicate_count,
    GROUP_CONCAT(payment_id) AS payment_ids
FROM payments
GROUP BY user_id, payment_date, amount
HAVING COUNT(payment_id) > 1
ORDER BY duplicate_count DESC, amount DESC
LIMIT 20;
"""

SQL_DETECT_NULLS = """
SELECT 'users.user_id'     AS column_ref,
    COUNT(*) FILTER (WHERE user_id IS NULL)     AS null_count,
    COUNT(*)                                    AS total_rows
FROM users
UNION ALL
SELECT 'users.cohort_month',
    COUNT(*) FILTER (WHERE cohort_month IS NULL), COUNT(*) FROM users
UNION ALL
SELECT 'users.active',
    COUNT(*) FILTER (WHERE active IS NULL), COUNT(*) FROM users
UNION ALL
SELECT 'payments.payment_id',
    COUNT(*) FILTER (WHERE payment_id IS NULL), COUNT(*) FROM payments
UNION ALL
SELECT 'payments.user_id',
    COUNT(*) FILTER (WHERE user_id IS NULL), COUNT(*) FROM payments
UNION ALL
SELECT 'payments.amount',
    COUNT(*) FILTER (WHERE amount IS NULL), COUNT(*) FROM payments
UNION ALL
SELECT 'payments.status',
    COUNT(*) FILTER (WHERE status IS NULL), COUNT(*) FROM payments
UNION ALL
SELECT 'payments.gateway',
    COUNT(*) FILTER (WHERE gateway IS NULL), COUNT(*) FROM payments
UNION ALL
SELECT 'payment_failures.failure_reason',
    COUNT(*) FILTER (WHERE failure_reason IS NULL), COUNT(*) FROM payment_failures
UNION ALL
SELECT 'payment_failures.gateway',
    COUNT(*) FILTER (WHERE gateway IS NULL), COUNT(*) FROM payment_failures;
"""

SQL_FRESHNESS_CHECK = """
SELECT
    MAX(payment_date) AS latest_payment_date,
    COUNT(*)          AS total_payments
FROM payments
WHERE status = 'success';
"""

SQL_ORPHAN_CHECK = """
SELECT COUNT(*) AS orphan_payments
FROM payments p
LEFT JOIN users u ON p.user_id = u.user_id
WHERE u.user_id IS NULL;
"""


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class Validator:
    """
    Run all data quality and KPI validation checks against live SQLite.
    Returns ValidationResult objects — structured, loggable, dashboard-ready.
    """

    def __init__(self, db_url: str = DB_URL):
        self._engine = create_engine(db_url, echo=False)
        log.debug("Validator connected to: %s", db_url)

    def _query(self, sql: str) -> pd.DataFrame:
        try:
            with self._engine.connect() as conn:
                return pd.read_sql(text(sql), conn)
        except Exception as exc:
            log.error("Validator SQL failed: %s", exc)
            raise

    def _scalar(self, sql: str, col: str):
        df  = self._query(sql)
        if df.empty or col not in df.columns:
            return None
        val = df.iloc[0][col]
        return None if pd.isna(val) else val

    # ------------------------------------------------------------------
    # 1. validate_arpu
    # ------------------------------------------------------------------

    def validate_arpu(self) -> ValidationResult:
        """
        PASS  — ARPU >= MIN_ARPU and paying_users > 0
        WARN  — no paying users found
        FAIL  — ARPU < MIN_ARPU (pricing / data quality alert)
        """
        check = "validate_arpu"
        try:
            df           = self._query(SQL_VALIDATE_ARPU)
            row          = df.iloc[0]
            arpu         = float(row["arpu"])         if row["arpu"]         is not None else 0.0
            paying_users = int(row["paying_users"])   if row["paying_users"] is not None else 0

            if paying_users == 0:
                return ValidationResult(check, Status.WARN,
                    "No paying users — ARPU undefined.", arpu, MIN_ARPU)
            if arpu < MIN_ARPU:
                return ValidationResult(check, Status.FAIL,
                    f"ARPU {arpu:.2f} below minimum {MIN_ARPU}.", arpu, MIN_ARPU)
            return ValidationResult(check, Status.PASS,
                f"ARPU={arpu:,.2f}  paying_users={paying_users:,}", arpu, MIN_ARPU)
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 2. validate_revenue
    # ------------------------------------------------------------------

    def validate_revenue(self) -> ValidationResult:
        """
        PASS  — total_revenue > 0, no null/negative amounts
        WARN  — revenue positive but anomalous amounts detected
        FAIL  — total_revenue is 0 or NULL
        """
        check = "validate_revenue"
        try:
            df                   = self._query(SQL_VALIDATE_REVENUE)
            row                  = df.iloc[0]
            total_revenue        = float(row["total_revenue"]        or 0)
            non_positive_amounts = int(row["non_positive_amounts"]   or 0)
            null_amounts         = int(row["null_amounts"]           or 0)

            if total_revenue <= 0:
                return ValidationResult(check, Status.FAIL,
                    f"Total revenue is {total_revenue:.2f} — pipeline may be broken.",
                    total_revenue, "> 0")
            if non_positive_amounts > 0 or null_amounts > 0:
                return ValidationResult(check, Status.WARN,
                    f"Revenue OK ({total_revenue:,.2f}) but "
                    f"{non_positive_amounts} non-positive, {null_amounts} null amounts.",
                    total_revenue, "no nulls/negatives")
            return ValidationResult(check, Status.PASS,
                f"Total revenue = {total_revenue:,.2f} — clean.",
                total_revenue, "> 0")
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 3. validate_cohort
    # ------------------------------------------------------------------

    def validate_cohort(self) -> ValidationResult:
        """
        PASS  — all users have valid cohort_month matching signup_date
        WARN  — mismatched cohort months
        FAIL  — null cohort months or pre-signup payments
        """
        check = "validate_cohort"
        try:
            df             = self._query(SQL_VALIDATE_COHORT)
            row            = df.iloc[0]
            null_cohorts   = int(row["null_cohort_months"])
            mismatched     = int(row["mismatched_cohort_months"])
            n_cohorts      = int(row["distinct_cohorts"])

            df2        = self._query(SQL_VALIDATE_COHORT_PAYMENTS)
            pre_signup = int(df2.iloc[0]["pre_signup_payments"])

            if null_cohorts > 0 or pre_signup > 0:
                return ValidationResult(check, Status.FAIL,
                    f"{null_cohorts} null cohort months; "
                    f"{pre_signup} pre-signup payments.",
                    n_cohorts, "no nulls / no pre-signup")
            if mismatched > 0:
                return ValidationResult(check, Status.WARN,
                    f"{mismatched} cohort_month values don't match signup_date.",
                    n_cohorts, "zero mismatches")
            return ValidationResult(check, Status.PASS,
                f"{n_cohorts} cohorts — all valid, 0 pre-signup anomalies.",
                n_cohorts, "no nulls / no pre-signup")
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 4. validate_failure_rate
    # ------------------------------------------------------------------

    def validate_failure_rate(self) -> ValidationResult:
        """
        PASS  — failure_rate_pct <= MAX_FAILURE_RATE * 100
        WARN  — approaching threshold (>= 75 % of max)
        FAIL  — exceeds MAX_FAILURE_RATE
        """
        check = "validate_failure_rate"
        try:
            df           = self._query(SQL_VALIDATE_FAILURE_RATE)
            row          = df.iloc[0]
            failure_rate = float(row["failure_rate_pct"] or 0)
            total        = int(row["total_payments"]     or 0)
            failed       = int(row["failed_payments"]    or 0)
            max_pct      = MAX_FAILURE_RATE * 100
            warn_pct     = max_pct * 0.75

            if failure_rate > max_pct:
                return ValidationResult(check, Status.FAIL,
                    f"Failure rate {failure_rate:.1f}% exceeds max {max_pct:.0f}% "
                    f"({failed}/{total}).",
                    failure_rate, f"<= {max_pct:.0f}%")
            if failure_rate > warn_pct:
                return ValidationResult(check, Status.WARN,
                    f"Failure rate {failure_rate:.1f}% approaching threshold.",
                    failure_rate, f"<= {max_pct:.0f}%")
            return ValidationResult(check, Status.PASS,
                f"Failure rate {failure_rate:.1f}%  ({failed}/{total}).",
                failure_rate, f"<= {max_pct:.0f}%")
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 5. detect_duplicates
    # ------------------------------------------------------------------

    def detect_duplicates(self) -> ValidationResult:
        """
        PASS  — no duplicates
        WARN  — duplicates found but within MAX_DUPLICATE_RATE threshold
        FAIL  — duplicate_rate > MAX_DUPLICATE_RATE
        """
        check = "detect_duplicates"
        try:
            df          = self._query(SQL_DETECT_DUPLICATES)
            row         = df.iloc[0]
            dupe_groups = int(row["duplicate_groups"] or 0)
            excess_rows = int(row["excess_rows"]      or 0)
            dupe_rate   = float(row["duplicate_rate"] or 0)
            detail      = self._query(SQL_DETECT_DUPLICATES_DETAIL)

            if dupe_rate > MAX_DUPLICATE_RATE:
                return ValidationResult(check, Status.FAIL,
                    f"Duplicate rate {dupe_rate:.2%} exceeds max {MAX_DUPLICATE_RATE:.2%} "
                    f"({dupe_groups} groups, {excess_rows} excess rows).",
                    dupe_rate, MAX_DUPLICATE_RATE, detail_df=detail)
            if dupe_groups > 0:
                return ValidationResult(check, Status.WARN,
                    f"{dupe_groups} duplicate groups ({excess_rows} excess rows) "
                    f"— rate {dupe_rate:.2%} within threshold.",
                    dupe_rate, MAX_DUPLICATE_RATE, detail_df=detail)
            return ValidationResult(check, Status.PASS,
                "No duplicate transactions detected.",
                dupe_rate, MAX_DUPLICATE_RATE)
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 6. detect_nulls
    # ------------------------------------------------------------------

    def detect_nulls(self) -> ValidationResult:
        """
        PASS  — all columns within MAX_NULL_RATE
        WARN  — non-critical columns exceed threshold
        FAIL  — primary-key or payment-amount columns contain nulls
        """
        check = "detect_nulls"
        try:
            df = self._query(SQL_DETECT_NULLS)
            df["null_rate"] = df["null_count"] / df["total_rows"].replace(0, 1)

            critical = {
                "users.user_id", "payments.payment_id",
                "payments.amount", "payments.status",
            }
            critical_nulls = df[
                df["column_ref"].isin(critical) & (df["null_count"] > 0)
            ]
            high_null = df[df["null_rate"] > MAX_NULL_RATE]

            if not critical_nulls.empty:
                return ValidationResult(check, Status.FAIL,
                    f"Critical columns have NULLs: "
                    f"{critical_nulls['column_ref'].tolist()}",
                    None, f"0 nulls in critical columns", detail_df=df)
            if not high_null.empty:
                cols = high_null["column_ref"].tolist()
                return ValidationResult(check, Status.WARN,
                    f"{len(cols)} column(s) exceed null threshold: {cols}",
                    None, f"null_rate <= {MAX_NULL_RATE:.0%}", detail_df=df)
            return ValidationResult(check, Status.PASS,
                f"All {len(df)} scanned columns within null threshold.",
                None, f"null_rate <= {MAX_NULL_RATE:.0%}", detail_df=df)
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 7. freshness_check
    # ------------------------------------------------------------------

    def freshness_check(self) -> ValidationResult:
        """
        PASS  — latest payment within FRESHNESS_SLA_HOURS
        WARN  — latest payment older than SLA (expected in dev/test)
        FAIL  — no payments found at all
        """
        check = "freshness_check"
        try:
            df         = self._query(SQL_FRESHNESS_CHECK)
            row        = df.iloc[0]
            latest_str = row["latest_payment_date"]
            total      = int(row["total_payments"] or 0)

            if not latest_str or total == 0:
                return ValidationResult(check, Status.FAIL,
                    "No successful payments found in database.",
                    None, f"within {FRESHNESS_SLA_HOURS}h")

            latest    = datetime.strptime(str(latest_str), "%Y-%m-%d")
            age_hours = (datetime.now() - latest).total_seconds() / 3600
            label     = f"within {FRESHNESS_SLA_HOURS}h of now"

            if age_hours > FRESHNESS_SLA_HOURS:
                return ValidationResult(check, Status.WARN,
                    f"Latest payment {latest_str} is {age_hours/24:.1f} days old "
                    f"(SLA={FRESHNESS_SLA_HOURS}h). Normal in dev — data ends 2024-12-31.",
                    latest_str, label)
            return ValidationResult(check, Status.PASS,
                f"Latest payment {latest_str} — {age_hours:.1f}h ago.",
                latest_str, label)
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # 8. orphan_check
    # ------------------------------------------------------------------

    def orphan_check(self) -> ValidationResult:
        """
        PASS  — all payments have a matching user row
        FAIL  — orphan payments found (referential integrity broken)
        """
        check = "orphan_check"
        try:
            orphans = self._scalar(SQL_ORPHAN_CHECK, "orphan_payments") or 0
            if orphans > 0:
                return ValidationResult(check, Status.FAIL,
                    f"{orphans} payments reference non-existent users.",
                    orphans, 0)
            return ValidationResult(check, Status.PASS,
                "No orphan payments — FK integrity intact.",
                orphans, 0)
        except Exception as exc:
            return ValidationResult(check, Status.ERROR, str(exc))

    # ------------------------------------------------------------------
    # run_all
    # ------------------------------------------------------------------

    def run_all(self) -> list:
        """
        Execute all validation checks in sequence.
        Returns list[ValidationResult].
        """
        checks = [
            self.validate_arpu,
            self.validate_revenue,
            self.validate_cohort,
            self.validate_failure_rate,
            self.detect_duplicates,
            self.detect_nulls,
            self.freshness_check,
            self.orphan_check,
        ]
        results = []
        for fn in checks:
            try:
                r = fn()
                results.append(r)
                log.info(str(r))
            except Exception as exc:
                e = ValidationResult(fn.__name__, Status.ERROR, str(exc))
                results.append(e)
                log.error(str(e))

        counts = {s: sum(1 for r in results if r.status == s) for s in Status}
        log.info(
            "Validation complete — PASS:%d  WARN:%d  FAIL:%d  ERROR:%d",
            counts[Status.PASS], counts[Status.WARN],
            counts[Status.FAIL], counts[Status.ERROR],
        )
        return results

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_quality_report(self, out_path=None) -> None:
        """Write validation summary to CSV."""
        import pathlib
        path    = pathlib.Path(out_path or REPORTS_DIR / "quality_report.csv")
        results = self.run_all()
        pd.DataFrame([r.to_dict() for r in results]).to_csv(path, index=False)
        log.info("Quality report exported: %s  (%d checks)", path, len(results))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    v       = Validator()
    results = v.run_all()

    print("\n" + "=" * 70)
    for r in results:
        print(r)
    print("=" * 70)

    for r in results:
        if r.check_name == "detect_duplicates" and r.detail_df is not None:
            print("\n  Duplicate sample (top 5):")
            print(r.detail_df.head(5).to_string(index=False))

    for r in results:
        if r.check_name == "detect_nulls" and r.detail_df is not None:
            print("\n  Null scan detail:")
            print(r.detail_df.to_string(index=False))

    v.export_quality_report()
    print("\n=== Quality report exported ===")
