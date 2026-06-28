"""
PlaceMux · Phase 2 · Task 9 — Failure Handling & Resilience
cohort_engine.py

Responsibility:
    All cohort-based revenue analytics, computed entirely via SQL.
    Cohorts are defined by the month a user signed up (cohort_month).

Metrics implemented:
    - Cohort Revenue Matrix   (cohort × months-since-signup)
    - Cohort Revenue Retention %  (relative to cohort's Month-0 revenue)
    - Cohort ARPU             (revenue per user per cohort)
    - Cohort Size             (number of users per signup cohort)
    - Lifetime Revenue        (total revenue per cohort to date)

Usage:
    from cohort_engine import CohortEngine
    ce = CohortEngine()
    matrix = ce.cohort_revenue_matrix()
    retention = ce.cohort_retention()
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
log = logging.getLogger("cohort_engine")


# ---------------------------------------------------------------------------
# SQL library
# ---------------------------------------------------------------------------

SQL_COHORT_SIZE = """
-- Number of users in each signup cohort.
SELECT
    cohort_month,
    COUNT(user_id) AS cohort_size
FROM users
GROUP BY cohort_month
ORDER BY cohort_month ASC;
"""

SQL_COHORT_REVENUE_RAW = """
-- Revenue earned from each cohort in each calendar month.
-- months_since_signup = 0 means the same month the cohort signed up.
-- This raw table is the base for both the matrix and retention views.
SELECT
    u.cohort_month,
    STRFTIME('%Y-%m', p.payment_date)                     AS revenue_month,
    -- Integer offset: how many months after signup did this revenue land?
    (
        (CAST(STRFTIME('%Y', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 1, 4) AS INTEGER)) * 12
        +
        (CAST(STRFTIME('%m', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 6, 2) AS INTEGER))
    )                                                      AS months_since_signup,
    COUNT(DISTINCT p.user_id)                              AS paying_users,
    ROUND(SUM(p.amount), 2)                                AS revenue
FROM payments p
JOIN users u ON p.user_id = u.user_id
WHERE p.status = 'success'
  AND (
        (CAST(STRFTIME('%Y', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 1, 4) AS INTEGER)) * 12
        +
        (CAST(STRFTIME('%m', p.payment_date) AS INTEGER) -
         CAST(SUBSTR(u.cohort_month, 6, 2) AS INTEGER))
      ) >= 0   -- exclude payments before signup (data anomaly guard)
GROUP BY u.cohort_month, revenue_month
ORDER BY u.cohort_month ASC, months_since_signup ASC;
"""

SQL_COHORT_ARPU = """
-- ARPU per cohort: lifetime revenue / cohort size.
-- Useful for comparing monetisation across acquisition periods.
WITH cohort_revenue AS (
    SELECT
        u.cohort_month,
        ROUND(SUM(p.amount), 2) AS lifetime_revenue,
        COUNT(DISTINCT p.user_id) AS paying_users
    FROM payments p
    JOIN users u ON p.user_id = u.user_id
    WHERE p.status = 'success'
    GROUP BY u.cohort_month
),
cohort_sizes AS (
    SELECT cohort_month, COUNT(user_id) AS cohort_size
    FROM users
    GROUP BY cohort_month
)
SELECT
    cr.cohort_month,
    cs.cohort_size,
    cr.paying_users,
    cr.lifetime_revenue,
    ROUND(cr.lifetime_revenue / NULLIF(cs.cohort_size, 0), 2) AS arpu_per_user,
    ROUND(
        CAST(cr.paying_users AS REAL) / NULLIF(cs.cohort_size, 0) * 100.0, 2
    )                                                          AS conversion_pct
FROM cohort_revenue cr
JOIN cohort_sizes cs USING (cohort_month)
ORDER BY cr.cohort_month ASC;
"""

SQL_COHORT_LIFETIME = """
-- Total lifetime revenue per cohort (flat, non-pivoted).
-- Used for the cohort lifetime bar chart.
SELECT
    u.cohort_month,
    COUNT(DISTINCT u.user_id)   AS cohort_size,
    COUNT(DISTINCT p.user_id)   AS paying_users,
    ROUND(SUM(p.amount), 2)     AS lifetime_revenue,
    COUNT(p.payment_id)         AS total_transactions
FROM users u
LEFT JOIN payments p
       ON u.user_id = p.user_id
      AND p.status  = 'success'
GROUP BY u.cohort_month
ORDER BY u.cohort_month ASC;
"""


# ---------------------------------------------------------------------------
# CohortEngine
# ---------------------------------------------------------------------------

class CohortEngine:
    """
    Compute all cohort-based revenue analytics via SQL.

    The cohort_revenue_matrix() and cohort_retention() methods return
    pivoted DataFrames ready for heatmap rendering in Plotly.
    """

    def __init__(self, db_url: str = DB_URL):
        self._engine = create_engine(db_url, echo=False)
        log.debug("CohortEngine connected to: %s", db_url)

    def _query(self, sql: str) -> pd.DataFrame:
        """Execute SQL and return DataFrame with error handling."""
        try:
            with self._engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            log.debug("Query returned %d rows.", len(df))
            return df
        except Exception as exc:
            log.error("CohortEngine SQL failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Cohort Size
    # ------------------------------------------------------------------

    def cohort_size(self) -> pd.DataFrame:
        """
        Number of users in each monthly signup cohort.
        Returns DataFrame: cohort_month | cohort_size
        """
        df = self._query(SQL_COHORT_SIZE)
        log.info("Cohort sizes: %d cohorts.", len(df))
        return df

    # ------------------------------------------------------------------
    # Cohort Revenue Raw
    # ------------------------------------------------------------------

    def cohort_revenue_raw(self) -> pd.DataFrame:
        """
        Raw cohort revenue by calendar month and months-since-signup.
        Base table used by matrix and retention calculations.
        Returns DataFrame: cohort_month | revenue_month |
                           months_since_signup | paying_users | revenue
        """
        df = self._query(SQL_COHORT_REVENUE_RAW)
        log.info(
            "Cohort revenue raw: %d rows across %d cohorts.",
            len(df),
            df["cohort_month"].nunique() if not df.empty else 0,
        )
        return df

    # ------------------------------------------------------------------
    # Cohort Revenue Matrix  (pivot: cohorts × months-since-signup)
    # ------------------------------------------------------------------

    def cohort_revenue_matrix(self, max_months: int = 12) -> pd.DataFrame:
        """
        Pivot cohort revenue into a matrix:
            rows    = cohort_month  (signup month)
            columns = months_since_signup  (0, 1, 2, … max_months)
            values  = revenue (INR)

        Cells with no revenue are filled with 0.
        Cohorts that have fewer than max_months of history have NaN
        in future columns (they haven't reached those months yet).

        Args:
            max_months: How many months of post-signup history to show.

        Returns:
            pd.DataFrame  (index = cohort_month, columns = 0..max_months)
        """
        raw = self.cohort_revenue_raw()
        if raw.empty:
            log.warning("No cohort revenue data — returning empty matrix.")
            return pd.DataFrame()

        # Keep only columns within the requested window
        raw = raw[raw["months_since_signup"] <= max_months].copy()

        matrix = raw.pivot_table(
            index="cohort_month",
            columns="months_since_signup",
            values="revenue",
            aggfunc="sum",
            fill_value=0,
        )

        # Ensure column range is always 0..max_months (some may be missing)
        all_cols = list(range(max_months + 1))
        matrix = matrix.reindex(columns=all_cols, fill_value=0)
        matrix.columns.name = "months_since_signup"

        log.info(
            "Cohort revenue matrix: %d cohorts × %d months.",
            len(matrix), len(matrix.columns),
        )
        return matrix

    # ------------------------------------------------------------------
    # Cohort Revenue Retention %
    # ------------------------------------------------------------------

    def cohort_retention(self, max_months: int = 12) -> pd.DataFrame:
        """
        Revenue retention matrix: each cell is the revenue in that month
        expressed as a % of the cohort's Month-0 (signup month) revenue.

        A value of 100 % means the cohort spent the same as their first month.
        Values > 100 % mean expansion revenue (upsell / more applications).

        Args:
            max_months: How many months of retention to track.

        Returns:
            pd.DataFrame  (index = cohort_month, columns = 0..max_months)
                          values are floats representing % of Month-0 revenue.
        """
        matrix = self.cohort_revenue_matrix(max_months=max_months)
        if matrix.empty:
            return pd.DataFrame()

        # Month-0 revenue per cohort (baseline)
        month_0 = matrix[0].replace(0, float("nan"))  # avoid division by zero

        retention = matrix.div(month_0, axis=0) * 100.0
        retention = retention.round(2)

        log.info(
            "Cohort retention matrix: %d cohorts × %d months.",
            len(retention), len(retention.columns),
        )
        return retention

    # ------------------------------------------------------------------
    # Cohort ARPU
    # ------------------------------------------------------------------

    def cohort_arpu(self) -> pd.DataFrame:
        """
        Lifetime ARPU, conversion rate, and paying users per cohort.
        Returns DataFrame: cohort_month | cohort_size | paying_users |
                           lifetime_revenue | arpu_per_user | conversion_pct
        """
        df = self._query(SQL_COHORT_ARPU)
        log.info("Cohort ARPU: %d cohorts.", len(df))
        return df

    # ------------------------------------------------------------------
    # Cohort Lifetime Revenue
    # ------------------------------------------------------------------

    def cohort_lifetime(self) -> pd.DataFrame:
        """
        Total lifetime revenue and transaction count per cohort.
        Includes cohorts with zero revenue (LEFT JOIN).
        Returns DataFrame: cohort_month | cohort_size | paying_users |
                           lifetime_revenue | total_transactions
        """
        df = self._query(SQL_COHORT_LIFETIME)
        log.info("Cohort lifetime: %d cohorts.", len(df))
        return df

    # ------------------------------------------------------------------
    # Export helper
    # ------------------------------------------------------------------

    def export_cohort_report(self, out_path=None) -> None:
        """
        Write cohort ARPU report to CSV for stakeholder sharing.
        """
        import pathlib
        path = pathlib.Path(out_path or REPORTS_DIR / "cohort_report.csv")
        df = self.cohort_arpu()
        df.to_csv(path, index=False)
        log.info("Cohort report exported: %s  (%d rows)", path, len(df))


# ---------------------------------------------------------------------------
# Entry point — smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ce = CohortEngine()

    print("\n=== Cohort Sizes ===")
    sizes = ce.cohort_size()
    print(sizes.to_string(index=False))

    print("\n=== Cohort ARPU ===")
    arpu = ce.cohort_arpu()
    print(arpu.to_string(index=False))

    print("\n=== Cohort Revenue Matrix (Month 0–5) ===")
    matrix = ce.cohort_revenue_matrix(max_months=5)
    print(matrix.to_string())

    print("\n=== Cohort Revenue Retention % (Month 0–5) ===")
    retention = ce.cohort_retention(max_months=5)
    # Format as percentages for readability
    fmt = retention.map(lambda x: f"{x:.1f}%" if pd.notna(x) else "—")
    print(fmt.to_string())

    print("\n=== Cohort Lifetime Revenue ===")
    lifetime = ce.cohort_lifetime()
    print(lifetime.to_string(index=False))

    ce.export_cohort_report()
    print("\n=== Cohort report exported ===")
