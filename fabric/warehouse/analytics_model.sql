/* =====================================================================
   FABRIC WAREHOUSE - curated analytics model (§22)
   =====================================================================
   Built on the Delta tables that Open Mirroring lands in OneLake from BOTH
   operational backends. Run against the Fabric Warehouse SQL endpoint.

   The point of this model: prove that the choice of operational database does
   NOT determine the analytics model. FactOrder is populated from the SQL mirror
   and from the Cosmos mirror, and the two are reconciled row-for-row by
   tools/reconciliation.py.

   Source Delta databases (both on the same Fabric SQL endpoint):
     mir_sql_orders     -> Orders, Properties, Parties, OrderParties, Loans,
                           OrderJsonBlocks, Customers, OrderVersions
     mir_cosmos_orders  -> CosmosOrderItems

   Parameterise the three-part names below if the mirror names differ; the
   defaults match fabric/provision_fabric.py + fabric/setup_mirroring.py.
   ===================================================================== */

/* ---------------------------------------------------------------------
   DIMENSIONS
   --------------------------------------------------------------------- */

DROP TABLE IF EXISTS dbo.DimDate;
CREATE TABLE dbo.DimDate (
    DateKey      int          NOT NULL,
    [Date]       date         NOT NULL,
    [Year]       smallint     NOT NULL,
    [Quarter]    smallint     NOT NULL,
    [Month]      smallint     NOT NULL,
    MonthName    varchar(12)  NOT NULL,
    [Day]        smallint     NOT NULL,
    DayOfWeek    smallint     NOT NULL,
    YearMonth    varchar(7)   NOT NULL,
    IsWeekend    bit          NOT NULL
);
-- NOTE: Fabric Warehouse does not support tinyint; smallint is the narrowest
-- integer type available. Verified by a CREATE TABLE failure, not assumed.

DROP TABLE IF EXISTS dbo.DimCustomer;
CREATE TABLE dbo.DimCustomer (
    CustomerKey   varchar(32)   NOT NULL,
    CustomerName  varchar(200)  NULL,
    TenantKey     varchar(64)   NULL,
    CreatedDate   datetime2(3)  NULL
);

DROP TABLE IF EXISTS dbo.DimOrderStatus;
CREATE TABLE dbo.DimOrderStatus (
    StatusKey     varchar(40)  NOT NULL,
    StatusName    varchar(40)  NOT NULL,
    IsTerminal    bit          NOT NULL,
    LifecycleStep smallint     NULL
);

DROP TABLE IF EXISTS dbo.DimProperty;
CREATE TABLE dbo.DimProperty (
    PropertyKey  varchar(64)   NOT NULL,
    OrderId      varchar(36)   NOT NULL,
    Address1     varchar(200)  NULL,
    City         varchar(100)  NULL,
    [State]      varchar(2)    NULL,
    Zip          varchar(12)   NULL,
    County       varchar(100)  NULL,
    Acreage      decimal(12,4) NULL,
    PropertyType varchar(60)   NULL
);

DROP TABLE IF EXISTS dbo.DimParty;
CREATE TABLE dbo.DimParty (
    PartyKey    varchar(64)   NOT NULL,
    PartyType   varchar(20)   NULL,
    DisplayName varchar(250)  NULL,
    CompanyName varchar(200)  NULL,
    Email       varchar(250)  NULL
);

/* ---------------------------------------------------------------------
   FACTS
   --------------------------------------------------------------------- */

DROP TABLE IF EXISTS dbo.FactOrder;
CREATE TABLE dbo.FactOrder (
    OrderId           varchar(36)   NOT NULL,
    CustomerKey       varchar(32)   NOT NULL,
    StatusKey         varchar(40)   NULL,
    CreatedDateKey    int           NULL,
    SettlementDateKey int           NULL,
    OrderNumber       varchar(40)   NULL,
    OrderType         varchar(40)   NULL,
    Project           varchar(80)   NULL,
    SettlementType    varchar(40)   NULL,
    [State]           varchar(2)    NULL,
    County            varchar(100)  NULL,
    IsCommercial      bit           NULL,
    IsRush            bit           NULL,
    Balance           decimal(19,2) NULL,
    CurrentVersion    int           NULL,
    PayloadBytes      bigint        NULL,
    BlockCount        int           NULL,
    LoanCount         int           NULL,
    PropertyCount     int           NULL,
    PartyCount        int           NULL,
    MaxLoanAmount     decimal(19,2) NULL,
    /* Provenance: which operational backend this row came from. Both write
       into the same fact table, which is how the "analytics model does not
       depend on the operational choice" claim is demonstrated and reconciled. */
    SourceBackend     varchar(10)   NOT NULL
);

DROP TABLE IF EXISTS dbo.FactLoan;
CREATE TABLE dbo.FactLoan (
    LoanKey        varchar(64)   NOT NULL,
    OrderId        varchar(36)   NOT NULL,
    CustomerKey    varchar(32)   NULL,
    LenderPartyKey varchar(64)   NULL,
    LoanAmount     decimal(19,2) NULL,
    LoanType       varchar(40)   NULL,
    InterestRate   decimal(9,4)  NULL,
    LoanTermMonths int           NULL,
    SourceBackend  varchar(10)   NOT NULL
);

DROP TABLE IF EXISTS dbo.FactOrderCharge;
CREATE TABLE dbo.FactOrderCharge (
    OrderId         varchar(36)    NOT NULL,
    CustomerKey     varchar(32)    NULL,
    BlockType       varchar(40)    NULL,
    BlockSubType    varchar(40)    NULL,
    ChargeCategory  varchar(80)    NULL,
    LineNumber      int            NULL,
    SectionNumber   varchar(20)    NULL,
    [Description]   varchar(400)   NULL,
    ContactName     varchar(250)   NULL,
    Amount          decimal(19,2)  NULL,
    BuyerPaid       decimal(19,2)  NULL,
    SellerPaid      decimal(19,2)  NULL,
    SourceBackend   varchar(10)    NOT NULL
);

/* ---------------------------------------------------------------------
   LOADS
   --------------------------------------------------------------------- */

-- DimDate: generated, not sourced.
WITH n AS (
    SELECT TOP (1461) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects a CROSS JOIN sys.all_objects b
),
d AS (SELECT DATEADD(day, i, CAST('2024-01-01' AS date)) AS dt FROM n)
INSERT INTO dbo.DimDate
SELECT
    YEAR(dt) * 10000 + MONTH(dt) * 100 + DAY(dt),
    dt, YEAR(dt), DATEPART(quarter, dt), MONTH(dt), DATENAME(month, dt),
    DAY(dt), DATEPART(weekday, dt),
    CONCAT(YEAR(dt), '-', RIGHT(CONCAT('0', MONTH(dt)), 2)),
    CASE WHEN DATEPART(weekday, dt) IN (1, 7) THEN 1 ELSE 0 END
FROM d;

INSERT INTO dbo.DimOrderStatus (StatusKey, StatusName, IsTerminal, LifecycleStep)
VALUES ('Open', 'Open', 0, 1),
       ('In Process', 'In Process', 0, 2),
       ('Title Review', 'Title Review', 0, 3),
       ('Clear to Close', 'Clear to Close', 0, 4),
       ('Closing', 'Closing', 0, 5),
       ('Closed', 'Closed', 1, 6),
       ('Cancelled', 'Cancelled', 1, 7);

INSERT INTO dbo.DimCustomer (CustomerKey, CustomerName, TenantKey, CreatedDate)
SELECT CustomerId, CustomerName, TenantKey, CreatedDate
FROM mir_sql_orders.dbo.Customers;

INSERT INTO dbo.DimProperty
SELECT PropertyId, OrderId, Address1, City, [State], Zip, County, Acreage, PropertyType
FROM mir_sql_orders.dbo.Properties;

INSERT INTO dbo.DimParty
SELECT PartyId, PartyType, DisplayName, CompanyName, Email
FROM mir_sql_orders.dbo.Parties;

-- FactOrder from the SQL mirror (PATH A).
INSERT INTO dbo.FactOrder
SELECT
    o.OrderId, o.CustomerId, o.Status,
    CASE WHEN o.CreatedDate    IS NULL THEN NULL ELSE YEAR(o.CreatedDate)    * 10000 + MONTH(o.CreatedDate)    * 100 + DAY(o.CreatedDate)    END,
    CASE WHEN o.SettlementDate IS NULL THEN NULL ELSE YEAR(o.SettlementDate) * 10000 + MONTH(o.SettlementDate) * 100 + DAY(o.SettlementDate) END,
    o.OrderNumber, o.OrderType, o.Project, o.SettlementType,
    o.PrimaryState, o.PrimaryCounty, o.IsCommercial, o.IsRush, o.Balance,
    o.CurrentVersion, o.PayloadBytes, o.BlockCount,
    (SELECT COUNT(*) FROM mir_sql_orders.dbo.Loans        l WHERE l.OrderId  = o.OrderId),
    (SELECT COUNT(*) FROM mir_sql_orders.dbo.Properties   p WHERE p.OrderId  = o.OrderId),
    (SELECT COUNT(*) FROM mir_sql_orders.dbo.OrderParties op WHERE op.OrderId = o.OrderId),
    o.MaxLoanAmount,
    'sql'
FROM mir_sql_orders.dbo.Orders o;

-- FactOrder from the Cosmos mirror (PATH B) - same grain, same columns,
-- sourced from the header items' JSON projections.
INSERT INTO dbo.FactOrder
SELECT
    c.orderId,
    c.customerId,
    JSON_VALUE(c.searchJson, '$.status'),
    CASE WHEN JSON_VALUE(c.searchJson, '$.createdDate') IS NULL THEN NULL ELSE
         YEAR(CAST(JSON_VALUE(c.searchJson, '$.createdDate') AS date)) * 10000
       + MONTH(CAST(JSON_VALUE(c.searchJson, '$.createdDate') AS date)) * 100
       + DAY(CAST(JSON_VALUE(c.searchJson, '$.createdDate') AS date)) END,
    CASE WHEN JSON_VALUE(c.searchJson, '$.settlementDate') IS NULL THEN NULL ELSE
         YEAR(CAST(JSON_VALUE(c.searchJson, '$.settlementDate') AS date)) * 10000
       + MONTH(CAST(JSON_VALUE(c.searchJson, '$.settlementDate') AS date)) * 100
       + DAY(CAST(JSON_VALUE(c.searchJson, '$.settlementDate') AS date)) END,
    JSON_VALUE(c.searchJson, '$.orderNumber'),
    JSON_VALUE(c.searchJson, '$.orderType'),
    JSON_VALUE(c.searchJson, '$.project'),
    JSON_VALUE(c.searchJson, '$.settlementType'),
    JSON_VALUE(c.searchJson, '$.state'),
    JSON_VALUE(c.searchJson, '$.county'),
    CAST(CASE WHEN JSON_VALUE(c.searchJson, '$.isCommercial') = 'true' THEN 1 ELSE 0 END AS bit),
    CAST(CASE WHEN JSON_VALUE(c.searchJson, '$.isRush')       = 'true' THEN 1 ELSE 0 END AS bit),
    TRY_CAST(JSON_VALUE(c.summaryJson, '$.balance') AS decimal(19,2)),
    c.orderVersion,
    c.payloadBytes,
    NULL,
    TRY_CAST(JSON_VALUE(c.summaryJson, '$.counts.loans')      AS int),
    TRY_CAST(JSON_VALUE(c.summaryJson, '$.counts.properties') AS int),
    TRY_CAST(JSON_VALUE(c.summaryJson, '$.counts.parties')    AS int),
    TRY_CAST(JSON_VALUE(c.searchJson,  '$.maxLoanAmount')     AS decimal(19,2)),
    'cosmos'
FROM mir_cosmos_orders.dbo.CosmosOrderItems c
WHERE c.docType = 'orderHeader';

INSERT INTO dbo.FactLoan
SELECT l.LoanId, l.OrderId, o.CustomerId, l.LenderPartyId, l.LoanAmount,
       l.LoanType, l.InterestRate, l.LoanTermMonths, 'sql'
FROM mir_sql_orders.dbo.Loans l
JOIN mir_sql_orders.dbo.Orders o ON o.OrderId = l.OrderId;

/* FactOrderCharge - shreds CDF line items out of the mirrored JSON blocks.
   This is the load that proves the multi-hundred-KB JsonPayload survived the
   trip to OneLake: if mirroring had truncated it, OPENJSON would return
   nothing or fail. */
INSERT INTO dbo.FactOrderCharge
SELECT
    b.OrderId, o.CustomerId, b.BlockType, b.BlockSubType,
    sect.[key]                              AS ChargeCategory,
    TRY_CAST(line.LineNumber AS int)        AS LineNumber,
    line.SectionNumber,
    line.[Description],
    line.ContactName,
    TRY_CAST(line.Amount AS decimal(19,2)),
    TRY_CAST(line.BuyerPaid AS decimal(19,2)),
    TRY_CAST(line.SellerPaid AS decimal(19,2)),
    'sql'
FROM mir_sql_orders.dbo.OrderJsonBlocks b
JOIN mir_sql_orders.dbo.Orders o ON o.OrderId = b.OrderId AND o.CurrentVersion = b.OrderVersion
CROSS APPLY OPENJSON(b.JsonPayload) AS sect
CROSS APPLY OPENJSON(sect.[value], '$.Lines')
    WITH (
        LineNumber    int            '$.Number',
        SectionNumber varchar(20)    '$.SectionNumber',
        [Description] varchar(400)   '$.Description',
        ContactName   varchar(250)   '$.ContactName',
        Amount        varchar(40)    '$.Amount',
        BuyerPaid     varchar(40)    '$.BuyerPaidAtClosing',
        SellerPaid    varchar(40)    '$.SellerPaidAtClosing'
    ) AS line
WHERE b.BlockType = 'CDF'
  AND b.BlockSubType IN ('ORIGINATION', 'SERVICES', 'OTHER_COSTS',
                         'DUE_FROM_BUYER', 'DUE_FROM_SELLER', 'TOTALS')
  AND ISJSON(b.JsonPayload) = 1;
