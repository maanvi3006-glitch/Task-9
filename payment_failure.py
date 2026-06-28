"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
payment_failure.py

Responsibility:
    All failure monitoring, retry analysis, and reconciliation logic.
    Every function queries the live SQLite database via SQL.

Functions:
    failure_monitor()       — current failure landscape by gateway/reason
    retry_analysis()        — retry success rates and recovery timing
    reconciliation_check()  — revenue leakage, duplicates, stale records
    revenue_leakage()       — unrecovered revenue by cohort + gateway
    failure_trend()         — daily failure counts for trend monitoring
    top_failure_reasons()   — ranked failure reasons with revenue impact
    gateway_health()        — composite gateway health score

Usage:
    from payment_failure import FailureMonitor
    fm = FailureMonitor()
    report = fm.failure_monitor()
    retry  = fm.retry_analysis()
"""

import logging

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL, LOG_LEVEL, REPORTS_DIR

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("payment_failure")


# ---------------------------------------------------------------------------
# SQL library
# ---------------------------------------------------------------------------

SQL_FAILURE_MONITOR = """
-- Full failure landscape: count, revenue at risk, recovery status per
-- gateway × failure_reason combination.
-- Decision: rows with high revenue_at_risk and low recovery_rate
--           need immediate retry or fraud-rule attention.
SELECT
    pf.gateway,
    pf.failure_reason,
    COUNT(*)                                                      AS failure_count,
    ROUND(SUM(pf.amount), 2)                                      AS revenue_at_risk,
    SUM(pf.recovered)                                             AS recovered_count,
    ROUND(
        CAST(SUM(pf.recovered) AS REAL) / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                             AS recovery_rate_pct,
    ROUND(
        SUM(pf.amount) - SUM(COALESCE(pf.recovery_amount, 0)), 2
    )                                                             AS net_revenue_lost
FROM payment_failures pf
GROUP BY pf.gateway, pf.failure_reason
ORDER BY net_revenue_lost DESC;
"""

SQL_RETRY_ANALYSIS = """
-- For recovered failures: how many days did recovery take?
-- Groups recoveries by days-to-recovery and gateway.
-- Decision: if most recoveries take 6-7 days, shorten retry window.
SELECT
    pf.gateway,
    CAST(
        JULIANDAY(pf.recovery_date) - JULIANDAY(pf.failure_date)
        AS INTEGER
    )                                          AS days_to_recovery,
    COUNT(*)                                   AS recovered_count,
    ROUND(SUM(pf.recovery_amount), 2)          AS recovered_revenue,
    ROUND(AVG(pf.recovery_amount), 2)          AS avg_recovery_amount
FROM payment_failures pf
WHERE pf.recovered = 1
  AND pf.recovery_date IS NOT NULL
GROUP BY pf.gateway, days_to_recovery
ORDER BY pf.gateway ASC, days_to_recovery ASC;
"""

SQL_RETRY_SUMMARY = """
-- Summary of retry outcomes per gateway.
SELECT
    pf.gateway,
    COUNT(*)                                                            AS total_failures,
    SUM(pf.recovered)                                                   AS recovered,
    COUNT(*) - SUM(pf.recovered)                                        AS not_recovered,
    ROUND(CAST(SUM(pf.recovered) AS REAL) / NULLIF(COUNT(*), 0) * 100, 2)
                                                                        AS recovery_pct,
    ROUND(SUM(CASE WHEN recovered = 1 THEN pf.recovery_amount ELSE 0 END), 2)
                                                                        AS recovered_revenue,
    ROUND(SUM(CASE WHEN recovered = 0 THEN pf.amount ELSE 0 END), 2)
                                                                        AS lost_revenue,
    ROUND(AVG(
        CASE WHEN pf.recovered = 1
        THEN JULIANDAY(pf.recovery_date) - JULIANDAY(pf.failure_date)
        END
    ), 2)                                                               AS avg_days_to_recovery
FROM payment_failures pf
GROUP BY pf.gateway
ORDER BY recovery_pct DESC;
"""

SQL_DUPLICATE_TRANSACTIONS = """
-- Detect likely duplicate payments:
-- same user_id + same amount + same payment_date = suspected duplicate.
-- Counts distinct payment_ids per fingerprint group.
-- Decision: duplicates > DUPLICATE_RATE % → investigate ingestion pipeline.
SELECT
    user_id,
    payment_date,
    amount,
    COUNT(payment_id)          AS duplicate_count,
    GROUP_CONCAT(payment_id)   AS payment_ids,
    gateway
FROM payments
GROUP BY user_id, payment_date, amount
HAVING COUNT(payment_id) > 1
ORDER BY duplicate_count DESC, amount DESC;
"""

SQL_STALE_PENDING = """
-- Stale pending payments: status = 'pending' with no revenue event
-- for more than 3 days after payment_date.
-- Decision: stale pending = possible gateway timeout needing manual resolution.
SELECT
    p.payment_id,
    p.user_id,
    p.payment_date,
    p.amount,
    p.gateway,
    p.status,
    -- Days since payment attempt
    CAST(
        JULIANDAY(DATE('now')) - JULIANDAY(p.payment_date)
        AS INTEGER
    )                           AS days_stale
FROM payments p
LEFT JOIN revenue_events re
       ON p.payment_id = re.payment_id
WHERE p.status = 'pending'
  AND re.event_id IS NULL   -- no corresponding revenue event
ORDER BY days_stale DESC, p.amount DESC;
"""

SQL_REVENUE_LEAKAGE = """
-- Revenue leakage: total unrecovered failure amount per cohort.
-- Joins payment_failures to users to surface which cohorts are bleeding most.
SELECT
    u.cohort_month,
    pf.gateway,
    COUNT(pf.failure_id)                     AS failure_count,
    ROUND(SUM(pf.amount), 2)                 AS total_at_risk,
    ROUND(SUM(
        CASE WHEN pf.recovered = 0 THEN pf.amount ELSE 0 END
    ), 2)                                    AS leaked_revenue,
    ROUND(
        SUM(CASE WHEN pf.recovered = 0 THEN pf.amount ELSE 0 END)
        / NULLIF(SUM(pf.amount), 0) * 100.0, 2
    )                                        AS leakage_pct
FROM payment_failures pf
JOIN users u ON pf.user_id = u.user_id
GROUP BY u.cohort_month, pf.gateway
ORDER BY leaked_revenue DESC;
"""

SQL_FAILURE_TREND = """
-- Daily failure counts and revenue at risk — for trend / spike detection.
SELECT
    failure_date,
    COUNT(*)                 AS failures,
    SUM(recovered)           AS recovered,
    COUNT(*) - SUM(recovered) AS not_recovered,
    ROUND(SUM(amount), 2)    AS revenue_at_risk
FROM payment_failures
GROUP BY failure_date
ORDER BY failure_date ASC;
"""

SQL_TOP_FAILURE_REASONS = """
-- Top failure reasons ranked by revenue lost (unrecovered only).
SELECT
    failure_reason,
    COUNT(*)                                                     AS occurrences,
    ROUND(SUM(amount), 2)                                        AS total_at_risk,
    ROUND(SUM(CASE WHEN recovered = 0 THEN amount ELSE 0 END), 2) AS revenue_lost,
    ROUND(
        CAST(SUM(recovered) AS REAL) / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                            AS recovery_pct
FROM payment_failures
GROUP BY failure_reason
ORDER BY revenue_lost DESC;
"""

SQL_GATEWAY_HEALTH = """
-- Composite gateway health view combining:
--   success rate, failure rate, recovery rate, revenue share.
-- Used for the gateway health bar chart on the dashboard.
WITH pay_stats AS (
    SELECT
        gateway,
        COUNT(*)                                                      AS total_txns,
        SUM(CASE WHEN status = 'success'  THEN 1 ELSE 0 END)         AS success_txns,
        SUM(CASE WHEN status = 'failed'   THEN 1 ELSE 0 END)         AS failed_txns,
        ROUND(SUM(CASE WHEN status = 'success' THEN amount ELSE 0 END), 2)
                                                                      AS success_revenue
    FROM payments
    GROUP BY gateway
),
fail_stats AS (
    SELECT
        gateway,
        COUNT(*)                 AS total_failures,
        SUM(recovered)           AS recovered_count,
        ROUND(SUM(CASE WHEN recovered = 0 THEN amount ELSE 0 END), 2)
                                 AS unrecovered_revenue
    FROM payment_failures
    GROUP BY gateway
)
SELECT
    p.gateway,
    p.total_txns,
    p.success_txns,
    p.failed_txns,
    ROUND(
        CAST(p.success_txns AS REAL) / NULLIF(p.total_txns, 0) * 100.0, 2
    )                                                                AS success_rate_pct,
    ROUND(
        CAST(p.failed_txns AS REAL) / NULLIF(p.total_txns, 0) * 100.0, 2
    )                                                                AS failure_rate_pct,
    COALESCE(f.total_failures, 0)                                    AS total_failures,
    COALESCE(f.recovered_count, 0)                                   AS recovered_count,
    ROUND(
        CAST(COALESCE(f.recovered_count, 0) AS REAL)
        / NULLIF(COALESCE(f.total_failures, 0), 0) * 100.0, 2
    )                                                                AS recovery_rate_pct,
    p.success_revenue,
    COALESCE(f.unrecovered_revenue, 0)                               AS unrecovered_revenue
FROM pay_stats p
LEFT JOIN fail_stats f USING (gateway)
ORDER BY success_rate_pct DESC;
"""

SQL_NULL_MONITORING = """
-- Count NULLs in critical columns across key tables.
-- Surfaced on the Data Quality dashboard page.
SELECT 'payments.failure_reason (non-failed rows with value)'  AS check_name,
       COUNT(*) AS issue_count
FROM payments
WHERE status NOT IN ('failed','refunded') AND failure_reason IS NOT NULL
UNION ALL
SELECT 'payments.failure_reason (failed rows missing value)',
       COUNT(*)
FROM payments
WHERE status = 'failed' AND failure_reason IS NULL
UNION ALL
SELECT 'users.active (NULL active flag)',
       COUNT(*)
FROM users
WHERE active IS NULL
UNION ALL
SELECT 'payment_failures.recovery_date (recovered=1 but no date)',
       COUNT(*)
FROM payment_failures
WHERE recovered = 1 AND recovery_date IS NULL
UNION ALL
SELECT 'payments.user_id (NULL user)',
       COUNT(*)
FROM payments
WHERE user_id IS NULL
ORDER BY issue_count DESC;
"""


# ---------------------------------------------------------------------------
# FailureMonitor
# ---------------------------------------------------------------------------

class FailureMonitor:
    """
    Monitor payment failures, retry outcomes, and revenue leakage.
    All methods return DataFrames or dicts sourced directly from SQLite.
    """

    def __init__(self, db_url: str = DB_URL):
        self._engine = create_engine(db_url, echo=False)
        log.debug("FailureMonitor connected to: %s", db_url)

    def _query(self, sql: str) -> pd.DataFrame:
        """Execute SQL and return DataFrame with centralised error handling."""
        try:
            with self._engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            log.debug("Query returned %d rows.", len(df))
            return df
        except Exception as exc:
            log.error("FailureMonitor SQL failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Failure Monitor
    # ------------------------------------------------------------------

    def failure_monitor(self) -> pd.DataFrame:
        """
        Full failure landscape: gateway × reason with revenue at risk,
        recovery rate, and net revenue lost.

        Returns DataFrame: gateway | failure_reason | failure_count |
                           revenue_at_risk | recovered_count |
                           recovery_rate_pct | net_revenue_lost
        """
        df = self._query(SQL_FAILURE_MONITOR)
        total_lost = df["net_revenue_lost"].sum() if not df.empty else 0
        log.info(
            "Failure monitor: %d gateway×reason combos | Net lost: %.2f",
            len(df), total_lost,
        )
        return df

    # ------------------------------------------------------------------
    # Retry Analysis
    # ------------------------------------------------------------------

    def retry_analysis(self) -> dict[str, pd.DataFrame]:
        """
        Analyse retry/recovery timing and success rates.

        Returns a dict with:
            'detail'  — DataFrame: gateway | days_to_recovery |
                        recovered_count | recovered_revenue | avg_recovery_amount
            'summary' — DataFrame: gateway | total_failures | recovered |
                        not_recovered | recovery_pct | recovered_revenue |
                        lost_revenue | avg_days_to_recovery
        """
        detail  = self._query(SQL_RETRY_ANALYSIS)
        summary = self._query(SQL_RETRY_SUMMARY)
        log.info(
            "Retry analysis: %d detail rows | %d gateways summarised.",
            len(detail), len(summary),
        )
        return {"detail": detail, "summary": summary}

    # ------------------------------------------------------------------
    # Reconciliation Check
    # ------------------------------------------------------------------

    def reconciliation_check(self) -> dict[str, pd.DataFrame]:
        """
        Check for data integrity issues:
            - duplicate transactions
            - stale pending payments
            - null monitoring alerts

        Returns a dict with keys: 'duplicates', 'stale_pending', 'nulls'
        Each value is a DataFrame of offending records.
        """
        duplicates    = self._query(SQL_DUPLICATE_TRANSACTIONS)
        stale_pending = self._query(SQL_STALE_PENDING)
        nulls         = self._query(SQL_NULL_MONITORING)

        log.info(
            "Reconciliation: %d duplicate groups | %d stale pending | "
            "%d null issues",
            len(duplicates), len(stale_pending), len(nulls),
        )

        # Log any critical null issues
        if not nulls.empty:
            for _, row in nulls.iterrows():
                if row["issue_count"] > 0:
                    log.warning(
                        "NULL check: '%s' → %d issues",
                        row["check_name"], row["issue_count"],
                    )

        return {
            "duplicates":    duplicates,
            "stale_pending": stale_pending,
            "nulls":         nulls,
        }

    # ------------------------------------------------------------------
    # Revenue Leakage
    # ------------------------------------------------------------------

    def revenue_leakage(self) -> pd.DataFrame:
        """
        Revenue leakage by cohort and gateway.
        Identifies which user cohorts and gateways are losing the most revenue
        to unrecovered failures.

        Returns DataFrame: cohort_month | gateway | failure_count |
                           total_at_risk | leaked_revenue | leakage_pct
        """
        df = self._query(SQL_REVENUE_LEAKAGE)
        total_leaked = df["leaked_revenue"].sum() if not df.empty else 0
        log.info(
            "Revenue leakage: %.2f across %d cohort×gateway buckets.",
            total_leaked, len(df),
        )
        return df

    # ------------------------------------------------------------------
    # Failure Trend
    # ------------------------------------------------------------------

    def failure_trend(self) -> pd.DataFrame:
        """
        Daily failure counts and revenue at risk for trend monitoring.

        Returns DataFrame: failure_date | failures | recovered |
                           not_recovered | revenue_at_risk
        """
        df = self._query(SQL_FAILURE_TREND)
        log.info("Failure trend: %d days of data.", len(df))
        return df

    # ------------------------------------------------------------------
    # Top Failure Reasons
    # ------------------------------------------------------------------

    def top_failure_reasons(self) -> pd.DataFrame:
        """
        Failure reasons ranked by unrecovered revenue lost.

        Returns DataFrame: failure_reason | occurrences | total_at_risk |
                           revenue_lost | recovery_pct
        """
        df = self._query(SQL_TOP_FAILURE_REASONS)
        log.info("Top failure reasons: %d distinct reasons.", len(df))
        return df

    # ------------------------------------------------------------------
    # Gateway Health
    # ------------------------------------------------------------------

    def gateway_health(self) -> pd.DataFrame:
        """
        Composite gateway health combining success rate, failure rate,
        recovery rate, and revenue metrics in one view.

        Returns DataFrame: gateway | total_txns | success_txns | failed_txns |
                           success_rate_pct | failure_rate_pct | total_failures |
                           recovered_count | recovery_rate_pct |
                           success_revenue | unrecovered_revenue
        """
        df = self._query(SQL_GATEWAY_HEALTH)
        log.info("Gateway health: %d gateways.", len(df))
        return df

    # ------------------------------------------------------------------
    # Null Monitoring
    # ------------------------------------------------------------------

    def null_monitoring(self) -> pd.DataFrame:
        """
        Scan critical columns for unexpected NULLs and misplaced values.

        Returns DataFrame: check_name | issue_count
        """
        df = self._query(SQL_NULL_MONITORING)
        log.info("Null monitoring: %d checks run.", len(df))
        return df

    # ------------------------------------------------------------------
    # Full report export
    # ------------------------------------------------------------------

    def export_full_report(self, out_dir=None) -> None:
        """Export failure monitor summary to CSV."""
        import pathlib
        out = pathlib.Path(out_dir or REPORTS_DIR)

        fm = self.failure_monitor()
        fm.to_csv(out / "failure_monitor.csv", index=False)
        log.info("Failure monitor exported: %d rows", len(fm))

        retry = self.retry_analysis()
        retry["summary"].to_csv(out / "retry_summary.csv", index=False)
        log.info("Retry summary exported: %d rows", len(retry["summary"]))


# ---------------------------------------------------------------------------
# Entry point — smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fm = FailureMonitor()

    print("\n=== Failure Monitor (top 8) ===")
    monitor = fm.failure_monitor()
    print(monitor.head(8).to_string(index=False))

    print("\n=== Retry Summary ===")
    retry = fm.retry_analysis()
    print(retry["summary"].to_string(index=False))

    print("\n=== Retry Detail — days to recovery (first 10) ===")
    print(retry["detail"].head(10).to_string(index=False))

    print("\n=== Reconciliation Check ===")
    recon = fm.reconciliation_check()
    print(f"  Duplicate groups : {len(recon['duplicates'])}")
    print(f"  Stale pending    : {len(recon['stale_pending'])}")
    print("\n  Null issues:")
    print(recon["nulls"].to_string(index=False))

    print("\n=== Top Failure Reasons ===")
    reasons = fm.top_failure_reasons()
    print(reasons.to_string(index=False))

    print("\n=== Gateway Health ===")
    health = fm.gateway_health()
    print(health.to_string(index=False))

    print("\n=== Revenue Leakage (top 10) ===")
    leakage = fm.revenue_leakage()
    print(leakage.head(10).to_string(index=False))

    fm.export_full_report()
    print("\n=== Reports exported ===")
