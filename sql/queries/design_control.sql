/* =====================================================================
   SQL DESIGN CONTROL (§19)
   =====================================================================
   Demonstrates, with runnable SQL, why the POC recommends NEITHER extreme.

   Control A : fully normalise every JSON node
   Control B : store everything in one giant JSON column
   Chosen    : the hybrid model in sql/schema/01_schema.sql

   Run with tools/design_control.py, which executes each query, captures the
   actual plan statistics, and writes docs/SQL_DESIGN.md tables from the results.
   ===================================================================== */

-- ---------------------------------------------------------------------
-- CONTROL A - fully normalised
-- ---------------------------------------------------------------------
-- A faithful relational decomposition of the measured source (106 top-level
-- sections, max nesting depth 16, 4,600+ objects) needs a table per repeating
-- object type. The demonstrator below builds just the CDF slice - ONE of 106
-- sections - and already needs 5 tables and a 5-way join to answer a question
-- the hybrid model answers with one indexed row plus one JSON block.

IF SCHEMA_ID('ctrl') IS NULL EXEC('CREATE SCHEMA ctrl');
GO

/* Just the CDF subtree, normalised. Note this is a DEMONSTRATOR: it is not
   part of the recommended model and carries no production indexes. */
IF OBJECT_ID('ctrl.CdfDocument') IS NULL
CREATE TABLE ctrl.CdfDocument (
    CdfDocumentId  uniqueidentifier NOT NULL PRIMARY KEY,
    OrderId        uniqueidentifier NOT NULL,
    Sequence       smallint         NOT NULL,
    CdfNumber      varchar(40)      NULL,
    CdfType        varchar(40)      NULL,
    IssuedDate     datetime2(3)     NULL,
    RevisionNumber varchar(10)      NULL
);
GO
IF OBJECT_ID('ctrl.CdfSection') IS NULL
CREATE TABLE ctrl.CdfSection (
    CdfSectionId   uniqueidentifier NOT NULL PRIMARY KEY,
    CdfDocumentId  uniqueidentifier NOT NULL,
    SectionKey     varchar(60)      NOT NULL,
    SectionType    nvarchar(120)    NULL,
    SectionTotal   decimal(19,2)    NULL
);
GO
IF OBJECT_ID('ctrl.CdfLine') IS NULL
CREATE TABLE ctrl.CdfLine (
    CdfLineId              uniqueidentifier NOT NULL PRIMARY KEY,
    CdfSectionId           uniqueidentifier NOT NULL,
    LineNumber             int              NULL,
    SectionNumber          varchar(20)      NULL,
    Description            nvarchar(400)    NULL,
    DescriptionExtended    nvarchar(1000)   NULL,
    ContactName            nvarchar(250)    NULL,
    Amount                 decimal(19,2)    NULL,
    BuyerPaidAtClosing     decimal(19,2)    NULL,
    BuyerPaidBeforeClosing decimal(19,2)    NULL,
    SellerPaidAtClosing    decimal(19,2)    NULL,
    SellerPaidBeforeClosing decimal(19,2)   NULL,
    PaidByOthers           decimal(19,2)    NULL,
    Reference              nvarchar(120)    NULL,
    IsRestricted           bit              NULL
);
GO
IF OBJECT_ID('ctrl.CdfLineCharge') IS NULL
CREATE TABLE ctrl.CdfLineCharge (
    CdfLineChargeId uniqueidentifier NOT NULL PRIMARY KEY,
    CdfLineId       uniqueidentifier NOT NULL,
    Description     nvarchar(400)    NULL,
    PayeeName       nvarchar(250)    NULL,
    Amount          decimal(19,2)    NULL,
    Reference       nvarchar(120)    NULL,
    IsOptional      bit              NULL
);
GO
IF OBJECT_ID('ctrl.CdfDisbursement') IS NULL
CREATE TABLE ctrl.CdfDisbursement (
    CdfDisbursementId uniqueidentifier NOT NULL PRIMARY KEY,
    CdfDocumentId     uniqueidentifier NOT NULL,
    PayeeName         nvarchar(250)    NULL,
    Amount            decimal(19,2)    NULL,
    DisbursementDate  datetime2(3)     NULL,
    CheckNumber       varchar(30)      NULL,
    Status            varchar(30)      NULL,
    Type              varchar(30)      NULL
);
GO

-- QUERY A1: reconstruct the CDF section of one order from the normalised model.
-- Five joins, and the result still has to be re-shaped into nested JSON by hand.
-- name: control_a_reconstruct_cdf
SELECT d.CdfNumber, d.CdfType, s.SectionKey, s.SectionType, s.SectionTotal,
       l.LineNumber, l.SectionNumber, l.Description, l.Amount,
       l.BuyerPaidAtClosing, l.SellerPaidAtClosing,
       c.Description AS ChargeDescription, c.PayeeName, c.Amount AS ChargeAmount
FROM ctrl.CdfDocument d
JOIN ctrl.CdfSection  s ON s.CdfDocumentId = d.CdfDocumentId
JOIN ctrl.CdfLine     l ON l.CdfSectionId  = s.CdfSectionId
LEFT JOIN ctrl.CdfLineCharge c ON c.CdfLineId = l.CdfLineId
WHERE d.OrderId = @OrderId
ORDER BY s.SectionKey, l.LineNumber, c.Description;
GO

-- QUERY A2: the same answer from the HYBRID model - one indexed row.
-- name: hybrid_reconstruct_cdf
SELECT BlockSubType, Sequence, PayloadBytes, JsonPayload
FROM ord.OrderJsonBlocks
WHERE OrderId = @OrderId AND BlockType = 'CDF'
ORDER BY BlockSubType, Sequence;
GO

-- ---------------------------------------------------------------------
-- CONTROL B - everything in one JSON column, nothing extracted
-- ---------------------------------------------------------------------
IF OBJECT_ID('ctrl.OrderBlob') IS NULL
CREATE TABLE ctrl.OrderBlob (
    OrderId     uniqueidentifier NOT NULL PRIMARY KEY,
    OrderJson   nvarchar(max)    NOT NULL,
    PayloadBytes int             NOT NULL,
    CONSTRAINT CK_OrderBlob_IsJson CHECK (ISJSON(OrderJson) = 1)
);
GO

-- QUERY B1: the customer's own list query - orders for a customer, status
-- Closed, loan amount above a threshold - against the blob-only model.
-- Every row must be read off disk and JSON-parsed. There is no index that
-- can help, because the predicate values live inside the blob.
-- name: control_b_search
SELECT TOP (50)
       b.OrderId,
       JSON_VALUE(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Number')  AS OrderNumber,
       JSON_VALUE(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Status')  AS Status,
       JSON_VALUE(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Properties[0].Address.State.Code') AS [State],
       (SELECT MAX(TRY_CONVERT(decimal(19,2), l.Amount))
          FROM OPENJSON(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Loans')
               WITH (Amount nvarchar(40) '$.Amount') AS l)                            AS MaxLoanAmount
FROM ctrl.OrderBlob b
WHERE JSON_VALUE(b.OrderJson, '$.ExtractDetails.CustomerSerialNumber') = @CustomerId
  AND JSON_VALUE(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Status') = @Status
  AND (SELECT MAX(TRY_CONVERT(decimal(19,2), l.Amount))
         FROM OPENJSON(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Loans')
              WITH (Amount nvarchar(40) '$.Amount') AS l) >= @MinLoanAmount;
GO

-- QUERY B2: the same answer from the HYBRID model - a covering index seek.
-- name: hybrid_search
SELECT TOP (50) o.OrderId, o.OrderNumber, o.Status, o.PrimaryState, o.MaxLoanAmount
FROM ord.Orders o
WHERE o.CustomerId = @CustomerId
  AND o.Status = @Status
  AND o.MaxLoanAmount >= @MinLoanAmount
ORDER BY o.ModifiedDate DESC;
GO

-- QUERY B3: a single block read from the blob-only model. The whole
-- multi-megabyte document is read to return one section.
-- name: control_b_block_read
SELECT JSON_QUERY(b.OrderJson, '$.ExtractData.ExtractObjects[0].ObjectData.Title') AS TitleJson,
       b.PayloadBytes
FROM ctrl.OrderBlob b
WHERE b.OrderId = @OrderId;
GO

-- QUERY B4: the same block read from the HYBRID model - only TITLE rows.
-- name: hybrid_block_read
SELECT BlockSubType, Sequence, PayloadBytes, JsonPayload
FROM ord.OrderJsonBlocks
WHERE OrderId = @OrderId AND BlockType = 'TITLE'
ORDER BY BlockSubType, Sequence;
GO

-- ---------------------------------------------------------------------
-- Schema-evolution burden (documented, not benchmarked)
-- ---------------------------------------------------------------------
-- name: schema_evolution_counts
SELECT
    (SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
      WHERE s.name = 'ord')  AS HybridTables,
    (SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
      WHERE s.name = 'ctrl' AND t.name LIKE 'Cdf%') AS NormalisedTablesForOneSection,
    (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id
      JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE s.name = 'ord') AS HybridColumns,
    (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id
      JOIN sys.schemas s ON s.schema_id = t.schema_id
      WHERE s.name = 'ctrl' AND t.name LIKE 'Cdf%') AS NormalisedColumnsForOneSection;
GO
