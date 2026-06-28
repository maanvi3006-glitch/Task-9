"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
metrics_engine.py

Responsibility:
    Compute all core revenue KPIs entirely via SQL.
    Every metric function returns a pandas DataFrame or scalar.
    No hardcoded values — all numbers flow from the live SQLite database.

Metrics implemented:
    - ARPU  (Average Revenue Per User)
    - Monthly Revenue
    - Revenue Growth %  (month-over-month)
    - Revenue by Gateway
    - Failed Payment %
    - Gateway Success %
    - Payment Recovery %
    - Revenue Lost  (from unrecovered failures)
    - Revenue Trend  (daily)

Usage:
    from metrics_engine import MetricsEngine
    engine = MetricsEngine()
    arpu = engine.arpu()
    monthly = engine.monthly_revenue()
"""

import logging
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL, LOG_LEVEL

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
)
log = logging.getLogger("metrics_engine")


# ---------------------------------------------------------------------------
# SQL library
# All SQL lives here — never inline in Python logic.
# ---------------------------------------------------------------------------

SQL_ARPU = """
-- ARPU: total successful revenue divided by distinct active users
-- who have made at least one successful payment.
-- Decision: if ARPU drops below threshold, review pricing or churn.
SELECT
    ROUND(
        CAST(SUM(p.amount) AS REAL) /
        NULLIF(COUNT(DISTINCT p.user_id), 0),
        2
    ) AS arpu,
    SUM(p.amount)            AS total_revenue,
    COUNT(DISTINCT p.user_id) AS paying_users
FROM payments p
WHERE p.status = 'success';
"""

SQL_MONTHLY_REVENUE = """
-- Monthly revenue: sum of successful payments grouped by YYYY-MM.
-- Includes transaction count and unique payer count per month.
SELECT
    STRFTIME('%Y-%m', p.payment_date)  AS month,
    ROUND(SUM(p.amount), 2)            AS revenue,
    COUNT(*)                           AS transactions,
    COUNT(DISTINCT p.user_id)          AS unique_payers
FROM payments p
WHERE p.status = 'success'
GROUP BY STRFTIME('%Y-%m', p.payment_date)
ORDER BY month ASC;
"""

SQL_REVENUE_GROWTH = """
-- Month-over-month revenue growth %.
-- Uses LAG window function on monthly revenue.
-- Decision: negative growth 2+ months in a row = pricing/retention alert.
WITH monthly AS (
    SELECT
        STRFTIME('%Y-%m', payment_date) AS month,
        ROUND(SUM(amount), 2)           AS revenue
    FROM payments
    WHERE status = 'success'
    GROUP BY STRFTIME('%Y-%m', payment_date)
),
with_lag AS (
    SELECT
        month,
        revenue,
        LAG(revenue) OVER (ORDER BY month) AS prev_revenue
    FROM monthly
)
SELECT
    month,
    revenue,
    prev_revenue,
    CASE
        WHEN prev_revenue IS NULL OR prev_revenue = 0 THEN NULL
        ELSE ROUND(((revenue - prev_revenue) / prev_revenue) * 100.0, 2)
    END AS growth_pct
FROM with_lag
ORDER BY month ASC;
"""

SQL_REVENUE_BY_GATEWAY = """
-- Revenue split by payment gateway for successful transactions.
-- Decision: low-performing gateways should be reviewed or replaced.
SELECT
    p.gateway,
    ROUND(SUM(p.amount), 2)            AS revenue,
    COUNT(*)                           AS transactions,
    COUNT(DISTINCT p.user_id)          AS unique_payers,
    ROUND(AVG(p.amount), 2)            AS avg_transaction
FROM payments p
WHERE p.status = 'success'
GROUP BY p.gateway
ORDER BY revenue DESC;
"""

SQL_FAILED_PAYMENT_PCT = """
-- Payment failure rate: failed / total payments.
-- Broken down by gateway and overall.
-- Decision: gateway failure rate > 20 % warrants SLA review.
WITH totals AS (
    SELECT
        gateway,
        COUNT(*)                                        AS total,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
    FROM payments
    GROUP BY gateway
)
SELECT
    gateway,
    total,
    failed,
    ROUND(CAST(failed AS REAL) / NULLIF(total, 0) * 100.0, 2) AS failure_pct
FROM totals
UNION ALL
SELECT
    'ALL GATEWAYS'                                                AS gateway,
    SUM(total)                                                    AS total,
    SUM(failed)                                                   AS failed,
    ROUND(CAST(SUM(failed) AS REAL) / NULLIF(SUM(total), 0) * 100.0, 2) AS failure_pct
FROM totals
ORDER BY failure_pct DESC;
"""

SQL_GATEWAY_SUCCESS_PCT = """
-- Gateway-level success rate (inverse of failure rate).
-- Includes refunds and pending in denominator for full picture.
SELECT
    gateway,
    COUNT(*)                                                         AS total,
    SUM(CASE WHEN status = 'success'  THEN 1 ELSE 0 END)            AS success_count,
    SUM(CASE WHEN status = 'failed'   THEN 1 ELSE 0 END)            AS failed_count,
    SUM(CASE WHEN status = 'refunded' THEN 1 ELSE 0 END)            AS refunded_count,
    ROUND(
        CAST(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                                AS success_pct
FROM payments
GROUP BY gateway
ORDER BY success_pct DESC;
"""

SQL_PAYMENT_RECOVERY_PCT = """
-- Recovery rate: what fraction of failed payments were later retried successfully.
-- Decision: low recovery rate suggests retry logic needs improvement.
SELECT
    COUNT(*)                                                           AS total_failures,
    SUM(recovered)                                                     AS recovered_count,
    ROUND(
        CAST(SUM(recovered) AS REAL) / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                                  AS recovery_pct,
    ROUND(SUM(CASE WHEN recovered = 1 THEN recovery_amount ELSE 0 END), 2)
                                                                       AS recovered_revenue,
    ROUND(SUM(CASE WHEN recovered = 0 THEN amount ELSE 0 END), 2)
                                                                       AS unrecovered_revenue
FROM payment_failures;
"""

SQL_REVENUE_LOST = """
-- Revenue lost to unrecovered payment failures, grouped by gateway and reason.
-- Decision: largest loss buckets should be prioritised for retry / fraud rules.
SELECT
    gateway,
    failure_reason,
    COUNT(*)                  AS failure_count,
    ROUND(SUM(amount), 2)     AS revenue_lost
FROM payment_failures
WHERE recovered = 0
GROUP BY gateway, failure_reason
ORDER BY revenue_lost DESC;
"""

SQL_DAILY_REVENUE_TREND = """
-- Daily successful revenue for trend / anomaly detection.
-- Spike days can be correlated with campaigns.
SELECT
    payment_date,
    ROUND(SUM(amount), 2) AS daily_revenue,
    COUNT(*)              AS transactions
FROM payments
WHERE status = 'success'
GROUP BY payment_date
ORDER BY payment_date ASC;
"""

SQL_TOP_SUMMARY = """
-- Single-row executive summary for the dashboard header.
SELECT
    ROUND(SUM(CASE WHEN status = 'success' THEN amount ELSE 0 END), 2)
                                                    AS total_revenue,
    COUNT(DISTINCT CASE WHEN status = 'success'
          THEN user_id END)                         AS active_paying_users,
    ROUND(
        SUM(CASE WHEN status = 'success' THEN amount ELSE 0 END)
        / NULLIF(COUNT(DISTINCT CASE WHEN status = 'success'
                THEN user_id END), 0), 2
    )                                               AS arpu,
    COUNT(*)                                        AS total_transactions,
    SUM(CASE WHEN status = 'failed'  THEN 1 ELSE 0 END) AS failed_transactions,
    SUM(CASE WHEN status = 'refunded' THEN 1 ELSE 0 END) AS refunded_transactions,
    ROUND(
        CAST(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                               AS overall_failure_pct
FROM payments;
"""


# ---------------------------------------------------------------------------
# MetricsEngine
# ---------------------------------------------------------------------------

class MetricsEngine:
    """
    Execute all revenue metrics via SQL against the live SQLite database.

    All public methods return either:
        - pd.DataFrame  (for tabular metrics)
        - dict          (for scalar KPI summaries)

    Methods are intentionally thin wrappers: SQL does the work.
    """

    def __init__(self, db_url: str = DB_URL):
        self._engine = create_engine(db_url, echo=False)
        log.debug("MetricsEngine connected to: %s", db_url)

    def _query(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        """
        Execute a SQL string and return a DataFrame.
        Centralises error handling and logging for all queries.
        """
        try:
            with self._engine.connect() as conn:
                df = pd.read_sql(text(sql), conn, params=params)
            log.debug("Query returned %d rows.", len(df))
            return df
        except Exception as exc:
            log.error("SQL query failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Core KPI: ARPU
    # ------------------------------------------------------------------

    def arpu(self) -> dict:
        """
        ARPU = total_revenue / distinct_paying_users (successful payments only).

        Returns a dict with keys: arpu, total_revenue, paying_users.
        """
        df = self._query(SQL_ARPU)
        if df.empty or df["arpu"].isna().all():
            log.warning("ARPU query returned no data.")
            return {"arpu": 0.0, "total_revenue": 0.0, "paying_users": 0}
        row = df.iloc[0]
        result = {
            "arpu":          float(row["arpu"] or 0),
            "total_revenue": float(row["total_revenue"] or 0),
            "paying_users":  int(row["paying_users"] or 0),
        }
        log.info(
            "ARPU=%.2f  |  Total Revenue=%.2f  |  Paying Users=%d",
            result["arpu"], result["total_revenue"], result["paying_users"],
        )
        return result

    # ------------------------------------------------------------------
    # Monthly Revenue
    # ------------------------------------------------------------------

    def monthly_revenue(self) -> pd.DataFrame:
        """
        Monthly successful revenue with transaction count and unique payers.
        Returns DataFrame: month | revenue | transactions | unique_payers
        """
        df = self._query(SQL_MONTHLY_REVENUE)
        log.info("Monthly revenue: %d months retrieved.", len(df))
        return df

    # ------------------------------------------------------------------
    # Revenue Growth %
    # ------------------------------------------------------------------

    def revenue_growth(self) -> pd.DataFrame:
        """
        Month-over-month revenue growth %.
        Returns DataFrame: month | revenue | prev_revenue | growth_pct
        """
        df = self._query(SQL_REVENUE_GROWTH)
        log.info("Revenue growth: %d months retrieved.", len(df))
        return df

    # ------------------------------------------------------------------
    # Revenue by Gateway
    # ------------------------------------------------------------------

    def revenue_by_gateway(self) -> pd.DataFrame:
        """
        Successful revenue split by payment gateway.
        Returns DataFrame: gateway | revenue | transactions | unique_payers | avg_transaction
        """
        df = self._query(SQL_REVENUE_BY_GATEWAY)
        log.info("Revenue by gateway: %d gateways.", len(df))
        return df

    # ------------------------------------------------------------------
    # Failed Payment %
    # ------------------------------------------------------------------

    def failed_payment_pct(self) -> pd.DataFrame:
        """
        Failure rate per gateway + overall.
        Returns DataFrame: gateway | total | failed | failure_pct
        """
        df = self._query(SQL_FAILED_PAYMENT_PCT)
        log.info("Failed payment pct: %d rows.", len(df))
        return df

    # ------------------------------------------------------------------
    # Gateway Success %
    # ------------------------------------------------------------------

    def gateway_success_pct(self) -> pd.DataFrame:
        """
        Success rate per gateway with full status breakdown.
        Returns DataFrame: gateway | total | success_count | failed_count |
                           refunded_count | success_pct
        """
        df = self._query(SQL_GATEWAY_SUCCESS_PCT)
        log.info("Gateway success pct: %d gateways.", len(df))
        return df

    # ------------------------------------------------------------------
    # Payment Recovery %
    # ------------------------------------------------------------------

    def payment_recovery(self) -> dict:
        """
        Recovery stats for failed payments.
        Returns dict: total_failures | recovered_count | recovery_pct |
                      recovered_revenue | unrecovered_revenue
        """
        df = self._query(SQL_PAYMENT_RECOVERY_PCT)
        if df.empty:
            return {
                "total_failures":      0,
                "recovered_count":     0,
                "recovery_pct":        0.0,
                "recovered_revenue":   0.0,
                "unrecovered_revenue": 0.0,
            }
        row = df.iloc[0]
        result = {
            "total_failures":      int(row["total_failures"] or 0),
            "recovered_count":     int(row["recovered_count"] or 0),
            "recovery_pct":        float(row["recovery_pct"] or 0),
            "recovered_revenue":   float(row["recovered_revenue"] or 0),
            "unrecovered_revenue": float(row["unrecovered_revenue"] or 0),
        }
        log.info(
            "Recovery: %d / %d  (%.1f %%)  |  Recovered revenue: %.2f",
            result["recovered_count"], result["total_failures"],
            result["recovery_pct"], result["recovered_revenue"],
        )
        return result

    # ------------------------------------------------------------------
    # Revenue Lost
    # ------------------------------------------------------------------

    def revenue_lost(self) -> pd.DataFrame:
        """
        Revenue lost to unrecovered failures, by gateway and reason.
        Returns DataFrame: gateway | failure_reason | failure_count | revenue_lost
        """
        df = self._query(SQL_REVENUE_LOST)
        total_lost = df["revenue_lost"].sum() if not df.empty else 0
        log.info("Revenue lost: %.2f across %d failure buckets.", total_lost, len(df))
        return df

    # ------------------------------------------------------------------
    # Daily Revenue Trend
    # ------------------------------------------------------------------

    def daily_revenue_trend(self) -> pd.DataFrame:
        """
        Day-by-day successful revenue for trend line and spike detection.
        Returns DataFrame: payment_date | daily_revenue | transactions
        """
        df = self._query(SQL_DAILY_REVENUE_TREND)
        log.info("Daily trend: %d days of data.", len(df))
        return df

    # ------------------------------------------------------------------
    # Executive Summary (single-row KPI header)
    # ------------------------------------------------------------------

    def top_summary(self) -> dict:
        """
        All headline KPIs in one query — used for the dashboard header row.
        Returns dict with: total_revenue, active_paying_users, arpu,
                           total_transactions, failed_transactions,
                           refunded_transactions, overall_failure_pct
        """
        df = self._query(SQL_TOP_SUMMARY)
        if df.empty:
            log.warning("top_summary returned no rows.")
            return {}
        row = df.iloc[0]
        result = {k: (float(v) if v is not None else 0.0) for k, v in row.items()}
        result["total_transactions"]    = int(result["total_transactions"])
        result["active_paying_users"]   = int(result["active_paying_users"])
        result["failed_transactions"]   = int(result["failed_transactions"])
        result["refunded_transactions"] = int(result["refunded_transactions"])
        log.info(
            "Summary: Revenue=%.2f | ARPU=%.2f | Failure=%.1f%%",
            result["total_revenue"], result["arpu"], result["overall_failure_pct"],
        )
        return result

    # ------------------------------------------------------------------
    # Export helper
    # ------------------------------------------------------------------

    def export_revenue_report(self, out_path) -> None:
        """
        Write monthly revenue report to CSV for stakeholder sharing.
        """
        import pathlib
        df = self.monthly_revenue()
        path = pathlib.Path(out_path)
        df.to_csv(path, index=False)
        log.info("Revenue report exported: %s  (%d rows)", path, len(df))


# ---------------------------------------------------------------------------
# Entry point — quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from config import REPORTS_DIR

    me = MetricsEngine()

    print("\n=== ARPU ===")
    arpu = me.arpu()
    for k, v in arpu.items():
        print(f"  {k}: {v}")

    print("\n=== Top Summary ===")
    summary = me.top_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n=== Monthly Revenue (last 5 months) ===")
    monthly = me.monthly_revenue()
    print(monthly.tail(5).to_string(index=False))

    print("\n=== Revenue Growth (last 5 months) ===")
    growth = me.revenue_growth()
    print(growth.tail(5).to_string(index=False))

    print("\n=== Gateway Success % ===")
    gw = me.gateway_success_pct()
    print(gw.to_string(index=False))

    print("\n=== Payment Recovery ===")
    rec = me.payment_recovery()
    for k, v in rec.items():
        print(f"  {k}: {v}")

    print("\n=== Revenue Lost (top 5 buckets) ===")
    lost = me.revenue_lost()
    print(lost.head(5).to_string(index=False))

    me.export_revenue_report(REPORTS_DIR / "revenue_report.csv")
    print("\n=== Revenue report exported ===")
