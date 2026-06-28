-- PlaceMux · sql/revenue_growth.sql
-- Month-over-month revenue growth %
-- Run directly:  sqlite3 placemux.db < sql/revenue_growth.sql

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
ORDER BY month;
