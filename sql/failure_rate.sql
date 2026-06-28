-- PlaceMux · sql/failure_rate.sql
-- Payment failure rate by gateway + overall
-- Run directly:  sqlite3 placemux.db < sql/failure_rate.sql

WITH totals AS (
    SELECT
        gateway,
        COUNT(*)                                          AS total,
        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
        SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success
    FROM payments
    GROUP BY gateway
)
SELECT
    gateway,
    total,
    success,
    failed,
    ROUND(CAST(failed  AS REAL) / NULLIF(total, 0) * 100.0, 2) AS failure_pct,
    ROUND(CAST(success AS REAL) / NULLIF(total, 0) * 100.0, 2) AS success_pct
FROM totals
UNION ALL
SELECT
    'ALL GATEWAYS',
    SUM(total), SUM(success), SUM(failed),
    ROUND(CAST(SUM(failed)  AS REAL) / NULLIF(SUM(total), 0) * 100.0, 2),
    ROUND(CAST(SUM(success) AS REAL) / NULLIF(SUM(total), 0) * 100.0, 2)
FROM totals
ORDER BY failure_pct DESC;
