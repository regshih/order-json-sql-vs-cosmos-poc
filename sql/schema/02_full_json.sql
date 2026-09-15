-- ===========================================================================
-- SCENARIO A - Azure SQL Database, ONE complete order JSON per row.
--
-- Deliberately NOT the hybrid model in 01_schema.sql. There is one row per
-- LOGICAL ORDER and the whole payload lives in one column. Nothing is split.
--
-- Two tables, same shape, different payload column type. Both are needed
-- because the choice is forced rather than stylistic (docs/SOURCES.md N.1/N.2):
--
--   ord.OrderDocuments        JsonPayload nvarchar(max) + ISJSON check
--                             -> CAN be mirrored to Fabric. The deployable one.
--
--   ord.OrderDocumentsNative  JsonPayload json (native binary type)
--                             -> CANNOT be mirrored: "A table can't be mirrored
--                                if it has the json or vector data type."
--                                Exists to measure what that constraint costs.
--
-- The scalar columns are ROUTING AND INDEX METADATA, not decomposition. The
-- complete order remains in JsonPayload byte-for-byte; these are duplicated
-- scalars so that the summary and search endpoints never touch a 5 MB payload.
-- The brief explicitly permits this ("retain useful routing/index metadata
-- outside the JSON when that materially improves point lookup").
-- ===========================================================================

IF SCHEMA_ID('ord') IS NULL EXEC('CREATE SCHEMA ord');
GO

-- ---------------------------------------------------------------------------
-- A1. nvarchar(max) variant - mirrorable
-- ---------------------------------------------------------------------------
IF OBJECT_ID('ord.OrderDocuments', 'U') IS NULL
BEGIN
    CREATE TABLE ord.OrderDocuments
    (
        OrderId         uniqueidentifier NOT NULL,
        CustomerId      nvarchar(64)     NOT NULL,
        OrderVersion    int              NOT NULL,
        OrderNumber     nvarchar(64)     NULL,
        Status          nvarchar(64)     NULL,
        PrimaryState    nvarchar(8)      NULL,
        MaxLoanAmount   decimal(19,2)    NULL,
        TotalLoanAmount decimal(19,2)    NULL,
        PropertyCount   int              NOT NULL CONSTRAINT DF_OrdDoc_Prop  DEFAULT(0),
        LoanCount       int              NOT NULL CONSTRAINT DF_OrdDoc_Loan  DEFAULT(0),
        PartyCount      int              NOT NULL CONSTRAINT DF_OrdDoc_Party DEFAULT(0),
        PayloadBytes    int              NOT NULL,
        PayloadHash     char(64)         NULL,
        -- The canonical summary body, precomputed at ingest. Keeps the hot
        -- endpoint off the payload entirely.
        SummaryJson     nvarchar(max)    NULL,
        UpdatedAt       datetime2(3)     NOT NULL CONSTRAINT DF_OrdDoc_Upd DEFAULT(SYSUTCDATETIME()),
        JsonPayload     nvarchar(max)    NOT NULL,
        CONSTRAINT PK_OrderDocuments PRIMARY KEY CLUSTERED (OrderId),
        -- Validity is enforced in the database, not merely trusted from the
        -- application. This is the nvarchar(max) variant's substitute for the
        -- native type's parse-on-write guarantee, and it is not free - the
        -- write cost of this check is part of what the comparison measures.
        CONSTRAINT CK_OrderDocuments_IsJson CHECK (ISJSON(JsonPayload) = 1)
    );
END
GO

-- Search/filter paths. Every one of these serves a projection column, never the
-- payload: a filtered scan must never have to parse JSON.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_OrderDocuments_Customer')
    CREATE NONCLUSTERED INDEX IX_OrderDocuments_Customer
        ON ord.OrderDocuments (CustomerId, UpdatedAt DESC)
        INCLUDE (OrderVersion, Status, PayloadBytes);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_OrderDocuments_Status')
    CREATE NONCLUSTERED INDEX IX_OrderDocuments_Status
        ON ord.OrderDocuments (Status, PrimaryState)
        INCLUDE (CustomerId, MaxLoanAmount, TotalLoanAmount, PayloadBytes);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_OrderDocuments_Loan')
    CREATE NONCLUSTERED INDEX IX_OrderDocuments_Loan
        ON ord.OrderDocuments (MaxLoanAmount DESC)
        INCLUDE (CustomerId, Status, PrimaryState);
GO

-- ---------------------------------------------------------------------------
-- A2. native json variant - NOT mirrorable, measured for the ceiling
-- ---------------------------------------------------------------------------
-- Created only where the type exists. Guarded rather than assumed: the type is
-- GA on Azure SQL Database under the SQL Server 2025 / Always-up-to-date update
-- policy, and a database on an older policy would fail this batch outright.
IF OBJECT_ID('ord.OrderDocumentsNative', 'U') IS NULL
   AND EXISTS (SELECT 1 FROM sys.types WHERE name = 'json')
BEGIN
    EXEC('
    CREATE TABLE ord.OrderDocumentsNative
    (
        OrderId         uniqueidentifier NOT NULL,
        CustomerId      nvarchar(64)     NOT NULL,
        OrderVersion    int              NOT NULL,
        OrderNumber     nvarchar(64)     NULL,
        Status          nvarchar(64)     NULL,
        PrimaryState    nvarchar(8)      NULL,
        MaxLoanAmount   decimal(19,2)    NULL,
        TotalLoanAmount decimal(19,2)    NULL,
        PropertyCount   int              NOT NULL CONSTRAINT DF_OrdDocN_Prop  DEFAULT(0),
        LoanCount       int              NOT NULL CONSTRAINT DF_OrdDocN_Loan  DEFAULT(0),
        PartyCount      int              NOT NULL CONSTRAINT DF_OrdDocN_Party DEFAULT(0),
        PayloadBytes    int              NOT NULL,
        PayloadHash     char(64)         NULL,
        SummaryJson     nvarchar(max)    NULL,
        UpdatedAt       datetime2(3)     NOT NULL CONSTRAINT DF_OrdDocN_Upd DEFAULT(SYSUTCDATETIME()),
        -- No ISJSON check here: the native type validates on assignment by
        -- construction. That difference is itself part of the measurement.
        JsonPayload     json             NOT NULL,
        CONSTRAINT PK_OrderDocumentsNative PRIMARY KEY CLUSTERED (OrderId)
    );');
END
GO

IF EXISTS (SELECT 1 FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
           WHERE s.name = 'ord' AND t.name = 'OrderDocumentsNative')
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_OrderDocumentsNative_Customer')
BEGIN
    EXEC('CREATE NONCLUSTERED INDEX IX_OrderDocumentsNative_Customer
              ON ord.OrderDocumentsNative (CustomerId, UpdatedAt DESC)
              INCLUDE (OrderVersion, Status, PayloadBytes);');
END
GO

IF EXISTS (SELECT 1 FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
           WHERE s.name = 'ord' AND t.name = 'OrderDocumentsNative')
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_OrderDocumentsNative_Status')
BEGIN
    EXEC('CREATE NONCLUSTERED INDEX IX_OrderDocumentsNative_Status
              ON ord.OrderDocumentsNative (Status, PrimaryState)
              INCLUDE (CustomerId, MaxLoanAmount, TotalLoanAmount, PayloadBytes);');
END
GO
