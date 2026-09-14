/* =====================================================================
   Representative analytics over the curated model (§22).
   Each query is named so tools/run_analytics.py can execute it, time it,
   and write the results into docs/FABRIC_ANALYTICS.md.
   ===================================================================== */

-- name: order_volume_over_time
-- Order volume by month, split by which operational backend fed the fact.
SELECT d.YearMonth, f.SourceBackend, COUNT(*) AS Orders
FROM dbo.FactOrder f
JOIN dbo.DimDate d ON d.DateKey = f.CreatedDateKey
GROUP BY d.YearMonth, f.SourceBackend
ORDER BY d.YearMonth, f.SourceBackend;

-- name: orders_by_state
SELECT f.SourceBackend, f.[State], COUNT(*) AS Orders,
       SUM(f.MaxLoanAmount) AS TotalMaxLoan
FROM dbo.FactOrder f
WHERE f.[State] IS NOT NULL
GROUP BY f.SourceBackend, f.[State]
ORDER BY f.SourceBackend, Orders DESC;

-- name: orders_by_status
SELECT s.StatusName, s.LifecycleStep, f.SourceBackend, COUNT(*) AS Orders
FROM dbo.FactOrder f
JOIN dbo.DimOrderStatus s ON s.StatusKey = f.StatusKey
GROUP BY s.StatusName, s.LifecycleStep, f.SourceBackend
ORDER BY s.LifecycleStep, f.SourceBackend;

-- name: average_loan_amount
SELECT f.SourceBackend, l.LoanType,
       COUNT(*) AS Loans,
       AVG(l.LoanAmount) AS AvgLoanAmount,
       SUM(l.LoanAmount) AS TotalLoanVolume,
       MIN(l.LoanAmount) AS MinLoanAmount,
       MAX(l.LoanAmount) AS MaxLoanAmount
FROM dbo.FactLoan l
JOIN dbo.FactOrder f ON f.OrderId = l.OrderId AND f.SourceBackend = l.SourceBackend
GROUP BY f.SourceBackend, l.LoanType
ORDER BY TotalLoanVolume DESC;

-- name: loan_volume_by_month
SELECT d.YearMonth, SUM(l.LoanAmount) AS LoanVolume, COUNT(*) AS Loans
FROM dbo.FactLoan l
JOIN dbo.FactOrder f ON f.OrderId = l.OrderId AND f.SourceBackend = 'sql'
JOIN dbo.DimDate d   ON d.DateKey = f.SettlementDateKey
GROUP BY d.YearMonth
ORDER BY d.YearMonth;

-- name: cdf_charges_by_category
-- The load-bearing analytics query: it only works if the multi-hundred-KB
-- JsonPayload survived mirroring into OneLake intact.
SELECT c.ChargeCategory,
       COUNT(*)          AS ChargeLines,
       SUM(c.Amount)     AS TotalAmount,
       AVG(c.Amount)     AS AvgAmount,
       SUM(c.BuyerPaid)  AS BuyerPaidTotal,
       SUM(c.SellerPaid) AS SellerPaidTotal
FROM dbo.FactOrderCharge c
GROUP BY c.ChargeCategory
ORDER BY TotalAmount DESC;

-- name: title_fees_by_description
SELECT TOP 25 c.[Description],
       COUNT(*)      AS Occurrences,
       SUM(c.Amount) AS TotalAmount,
       AVG(c.Amount) AS AvgAmount
FROM dbo.FactOrderCharge c
WHERE c.[Description] LIKE 'Title%' OR c.[Description] LIKE '%Endorsement%'
GROUP BY c.[Description]
ORDER BY TotalAmount DESC;

-- name: closing_cost_totals_by_order
SELECT TOP 25 c.OrderId,
       SUM(c.Amount)     AS TotalClosingCost,
       SUM(c.BuyerPaid)  AS BuyerPaid,
       SUM(c.SellerPaid) AS SellerPaid,
       COUNT(*)          AS ChargeLines
FROM dbo.FactOrderCharge c
GROUP BY c.OrderId
ORDER BY TotalClosingCost DESC;

-- name: order_version_and_size_distribution
SELECT f.SourceBackend,
       COUNT(*)                AS Orders,
       AVG(CAST(f.PayloadBytes AS bigint)) AS AvgPayloadBytes,
       MIN(f.PayloadBytes)     AS MinPayloadBytes,
       MAX(f.PayloadBytes)     AS MaxPayloadBytes,
       SUM(CAST(f.PayloadBytes AS bigint)) AS TotalPayloadBytes,
       AVG(CAST(f.CurrentVersion AS float)) AS AvgVersion
FROM dbo.FactOrder f
GROUP BY f.SourceBackend;

-- name: property_geography
SELECT p.[State], p.County, COUNT(DISTINCT p.OrderId) AS Orders,
       AVG(p.Acreage) AS AvgAcreage
FROM dbo.DimProperty p
GROUP BY p.[State], p.County
ORDER BY Orders DESC;

/* =====================================================================
   RECONCILIATION: the SQL-fed and Cosmos-fed facts must agree.
   ===================================================================== */

-- name: reconcile_row_counts
SELECT SourceBackend, COUNT(*) AS FactOrderRows, COUNT(DISTINCT OrderId) AS DistinctOrders
FROM dbo.FactOrder GROUP BY SourceBackend;

-- name: reconcile_business_keys
-- Orders present in one backend's facts but not the other.
SELECT
    (SELECT COUNT(*) FROM (SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend = 'sql'
       EXCEPT SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend = 'cosmos') x) AS OnlyInSql,
    (SELECT COUNT(*) FROM (SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend = 'cosmos'
       EXCEPT SELECT OrderId FROM dbo.FactOrder WHERE SourceBackend = 'sql') y) AS OnlyInCosmos;

-- name: reconcile_aggregates
SELECT
    s.SourceBackend        AS SqlBackend,
    c.SourceBackend        AS CosmosBackend,
    s.Orders               AS SqlOrders,
    c.Orders               AS CosmosOrders,
    s.TotalMaxLoan         AS SqlTotalMaxLoan,
    c.TotalMaxLoan         AS CosmosTotalMaxLoan,
    ABS(s.TotalMaxLoan - c.TotalMaxLoan) AS AbsDifference
FROM (SELECT SourceBackend, COUNT(*) AS Orders, SUM(MaxLoanAmount) AS TotalMaxLoan
      FROM dbo.FactOrder WHERE SourceBackend = 'sql'    GROUP BY SourceBackend) s
CROSS JOIN
     (SELECT SourceBackend, COUNT(*) AS Orders, SUM(MaxLoanAmount) AS TotalMaxLoan
      FROM dbo.FactOrder WHERE SourceBackend = 'cosmos' GROUP BY SourceBackend) c;

-- name: reconcile_per_order_status
-- Any order where the two backends disagree on status is a real defect.
SELECT TOP 50 s.OrderId, s.StatusKey AS SqlStatus, c.StatusKey AS CosmosStatus,
       s.MaxLoanAmount AS SqlMaxLoan, c.MaxLoanAmount AS CosmosMaxLoan
FROM dbo.FactOrder s
JOIN dbo.FactOrder c ON c.OrderId = s.OrderId AND c.SourceBackend = 'cosmos'
WHERE s.SourceBackend = 'sql'
  AND (s.StatusKey <> c.StatusKey
       OR ABS(ISNULL(s.MaxLoanAmount, 0) - ISNULL(c.MaxLoanAmount, 0)) > 0.01);

-- name: json_payload_integrity
-- Direct evidence on whether mirroring truncated the nvarchar(max) blocks.
SELECT
    COUNT(*)                                   AS BlockRows,
    MAX(LEN(JsonPayload))                      AS MaxPayloadChars,
    AVG(CAST(LEN(JsonPayload) AS bigint))      AS AvgPayloadChars,
    SUM(CASE WHEN LEN(JsonPayload) >= 1048576 THEN 1 ELSE 0 END) AS RowsAtOrAbove1MiB,
    SUM(CASE WHEN ISJSON(JsonPayload) = 1 THEN 1 ELSE 0 END)     AS ValidJsonRows,
    SUM(CASE WHEN ISJSON(JsonPayload) = 0 THEN 1 ELSE 0 END)     AS InvalidJsonRows,
    SUM(CASE WHEN LEN(JsonPayload) <> PayloadBytes
                  AND ABS(LEN(JsonPayload) - PayloadBytes) > 64 THEN 1 ELSE 0 END) AS LengthMismatches
FROM mir_sql_orders.dbo.OrderJsonBlocks;
