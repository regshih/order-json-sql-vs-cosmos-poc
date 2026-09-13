/* =====================================================================
   PATH A - Azure SQL Database hybrid relational + JSON model
   =====================================================================
   8 business tables + 2 operational tables.

   Design rule (see docs/SQL_DESIGN.md):
     searched / joined / filtered / sorted / secured / reported  -> columns
     deep, sparse, variable, pass-through                        -> JSON blocks

   Measured basis: docs/DATA_PROFILE.md. The source order has 106 top-level
   sections, max nesting depth 16, and 33% empty-string scalars. Fully
   normalising it would exceed 100 tables; leaving it as one blob would make
   every search a full scan + JSON parse. This is the middle path.
   ===================================================================== */

IF SCHEMA_ID('ord') IS NULL EXEC('CREATE SCHEMA ord');
GO

/* ---------------------------------------------------------------------
   1. Customers  (tenant boundary)
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.Customers') IS NULL
CREATE TABLE ord.Customers
(
    CustomerId      varchar(32)    NOT NULL CONSTRAINT PK_Customers PRIMARY KEY,
    CustomerName    nvarchar(200)  NOT NULL,
    TenantKey       varchar(64)    NOT NULL,
    CreatedDate     datetime2(3)   NOT NULL CONSTRAINT DF_Customers_Created DEFAULT SYSUTCDATETIME()
);
GO

/* ---------------------------------------------------------------------
   2. Orders  (the operational search surface)
   Only fields that appear in an API filter, sort or report are columns.
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.Orders') IS NULL
CREATE TABLE ord.Orders
(
    OrderId             uniqueidentifier NOT NULL CONSTRAINT PK_Orders PRIMARY KEY,
    CustomerId          varchar(32)      NOT NULL,
    CurrentVersion      int              NOT NULL,
    OrderNumber         varchar(40)      NULL,
    OrderType           varchar(40)      NULL,   -- TransactionType
    Status              varchar(40)      NULL,
    Project             varchar(80)      NULL,
    SettlementType      varchar(40)      NULL,
    IsCommercial        bit              NOT NULL CONSTRAINT DF_Orders_Comm DEFAULT 0,
    IsRush              bit              NOT NULL CONSTRAINT DF_Orders_Rush DEFAULT 0,
    Balance             decimal(19,2)    NULL,
    CreatedDate         datetime2(3)     NULL,
    SettlementDate      datetime2(3)     NULL,
    DisbursementDate    datetime2(3)     NULL,
    CompletedDate       datetime2(3)     NULL,
    ExtractDate         datetime2(3)     NULL,
    /* Denormalised search accelerators. Maintained by ingestion, not by
       trigger: the API filters on these and the alternative is a join to
       Properties/Loans on every list query. */
    PrimaryState        char(2)          NULL,
    PrimaryCounty       nvarchar(100)    NULL,
    MaxLoanAmount       decimal(19,2)    NULL,
    PayloadBytes        int              NULL,   -- compact size of the full order
    BlockCount          smallint         NULL,
    ModifiedDate        datetime2(3)     NOT NULL CONSTRAINT DF_Orders_Mod DEFAULT SYSUTCDATETIME(),
    CONSTRAINT FK_Orders_Customers FOREIGN KEY (CustomerId) REFERENCES ord.Customers(CustomerId)
);
GO

/* ---------------------------------------------------------------------
   3. OrderVersions  (source-version lineage -> archive replay)
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.OrderVersions') IS NULL
CREATE TABLE ord.OrderVersions
(
    OrderId             uniqueidentifier NOT NULL,
    Version             int              NOT NULL,
    ExtractTimestamp    datetime2(3)     NULL,
    SourceArchiveUri    nvarchar(1000)   NULL,   -- raw/{customer}/{order}/{version}/order.json
    PayloadHash         char(64)         NULL,   -- sha256 of compact source bytes
    PayloadBytes        int              NULL,
    BlockCount          smallint         NULL,
    CreatedDate         datetime2(3)     NOT NULL CONSTRAINT DF_OV_Created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_OrderVersions PRIMARY KEY (OrderId, Version),
    CONSTRAINT FK_OV_Orders FOREIGN KEY (OrderId) REFERENCES ord.Orders(OrderId)
);
GO

/* ---------------------------------------------------------------------
   4. Properties
   Address components are relational because state/county drive reporting
   and city/zip drive search. Legal descriptions, HOA detail, parcels and
   deeds stay in the PROPERTIES JSON block.
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.Properties') IS NULL
CREATE TABLE ord.Properties
(
    PropertyId      varchar(64)      NOT NULL CONSTRAINT PK_Properties PRIMARY KEY,
    OrderId         uniqueidentifier NOT NULL,
    Sequence        smallint         NOT NULL CONSTRAINT DF_Prop_Seq DEFAULT 0,
    Address1        nvarchar(200)    NULL,
    Address2        nvarchar(100)    NULL,
    City            nvarchar(100)    NULL,
    [State]         char(2)          NULL,
    Zip             varchar(12)      NULL,
    County          nvarchar(100)    NULL,
    Acreage         decimal(12,4)    NULL,
    PropertyType    nvarchar(60)     NULL,
    CONSTRAINT FK_Prop_Orders FOREIGN KEY (OrderId) REFERENCES ord.Orders(OrderId)
);
GO

/* ---------------------------------------------------------------------
   5. Parties  (ONE party table, not one per role)
   The source carries Buyers / Sellers / Lenders / TitleCompanies / Others
   with near-identical shapes. A role discriminator in OrderParties beats
   six structurally identical tables.
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.Parties') IS NULL
CREATE TABLE ord.Parties
(
    PartyId         varchar(64)     NOT NULL CONSTRAINT PK_Parties PRIMARY KEY,
    PartyType       varchar(20)     NOT NULL,   -- PERSON | COMPANY
    FirstName       nvarchar(100)   NULL,
    LastName        nvarchar(100)   NULL,
    CompanyName     nvarchar(200)   NULL,
    DisplayName     nvarchar(250)   NULL,
    Email           nvarchar(250)   NULL,
    Phone           varchar(40)     NULL
);
GO

/* ---------------------------------------------------------------------
   6. OrderParties  (role assignment)
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.OrderParties') IS NULL
CREATE TABLE ord.OrderParties
(
    OrderId     uniqueidentifier NOT NULL,
    PartyId     varchar(64)      NOT NULL,
    Role        varchar(40)      NOT NULL,   -- BUYER|SELLER|LENDER|ATTORNEY|BUILDER|APPRAISER|...
    Sequence    smallint         NOT NULL CONSTRAINT DF_OP_Seq DEFAULT 0,
    CONSTRAINT PK_OrderParties PRIMARY KEY (OrderId, PartyId, Role, Sequence),
    CONSTRAINT FK_OP_Orders  FOREIGN KEY (OrderId) REFERENCES ord.Orders(OrderId),
    CONSTRAINT FK_OP_Parties FOREIGN KEY (PartyId) REFERENCES ord.Parties(PartyId)
);
GO

/* ---------------------------------------------------------------------
   7. Loans
   Amount / type / number are searched and reported. The full Terms tree
   (ARM detail, buydown, payment schedules) stays in the LOANS JSON block.
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.Loans') IS NULL
CREATE TABLE ord.Loans
(
    LoanId          varchar(64)      NOT NULL CONSTRAINT PK_Loans PRIMARY KEY,
    OrderId         uniqueidentifier NOT NULL,
    Sequence        smallint         NOT NULL CONSTRAINT DF_Loan_Seq DEFAULT 0,
    LenderPartyId   varchar(64)      NULL,
    LoanAmount      decimal(19,2)    NULL,
    LoanType        varchar(40)      NULL,
    LoanNumber      varchar(60)      NULL,
    InterestRate    decimal(9,4)     NULL,
    LoanTermMonths  int              NULL,
    CONSTRAINT FK_Loans_Orders FOREIGN KEY (OrderId) REFERENCES ord.Orders(OrderId)
);
GO

/* ---------------------------------------------------------------------
   8. OrderJsonBlocks  -- the heart of the hybrid model
   One row per logical business block. Business boundaries only; never
   arbitrary byte slices. See ingestion/parser/block_splitter.py.
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.OrderJsonBlocks') IS NULL
CREATE TABLE ord.OrderJsonBlocks
(
    JsonBlockId     bigint           IDENTITY(1,1) NOT NULL CONSTRAINT PK_OrderJsonBlocks PRIMARY KEY,
    OrderId         uniqueidentifier NOT NULL,
    OrderVersion    int              NOT NULL,
    BlockType       varchar(40)      NOT NULL,   -- CDF | TITLE | PARTIES | NOTES | ...
    BlockSubType    varchar(40)      NOT NULL,   -- COMMITMENTS | POLICIES | DISBURSEMENTS | ...
    Sequence        smallint         NOT NULL CONSTRAINT DF_Block_Seq DEFAULT 0,
    JsonPayload     nvarchar(max)    NOT NULL,
    PayloadBytes    int              NOT NULL,
    PayloadHash     char(64)         NOT NULL,
    LastModified    datetime2(3)     NOT NULL CONSTRAINT DF_Block_Mod DEFAULT SYSUTCDATETIME(),
    /* Validation: a malformed block would break reconstruction silently.
       ISJSON is cheap at write time and we only write via ingestion. */
    CONSTRAINT CK_Blocks_IsJson CHECK (ISJSON(JsonPayload) = 1),
    CONSTRAINT UQ_Blocks UNIQUE (OrderId, OrderVersion, BlockType, BlockSubType, Sequence),
    CONSTRAINT FK_Blocks_Orders FOREIGN KEY (OrderId) REFERENCES ord.Orders(OrderId)
);
GO

/* ---------------------------------------------------------------------
   9. IngestionRuns  (operational)
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.IngestionRuns') IS NULL
CREATE TABLE ord.IngestionRuns
(
    RunId           uniqueidentifier NOT NULL CONSTRAINT PK_IngestionRuns PRIMARY KEY,
    StartedUtc      datetime2(3)     NOT NULL CONSTRAINT DF_IR_Start DEFAULT SYSUTCDATETIME(),
    CompletedUtc    datetime2(3)     NULL,
    Backend         varchar(20)      NOT NULL,   -- sql | cosmos
    SourceKind      varchar(40)      NULL,       -- synthetic | sample
    OrdersAttempted int              NULL,
    OrdersSucceeded int              NULL,
    OrdersFailed    int              NULL,
    BlocksWritten   int              NULL,
    BytesWritten    bigint           NULL,
    Notes           nvarchar(1000)   NULL
);
GO

/* ---------------------------------------------------------------------
   10. AuditEvents  (operational)
   --------------------------------------------------------------------- */
IF OBJECT_ID('ord.AuditEvents') IS NULL
CREATE TABLE ord.AuditEvents
(
    AuditEventId    bigint           IDENTITY(1,1) NOT NULL CONSTRAINT PK_AuditEvents PRIMARY KEY,
    OccurredUtc     datetime2(3)     NOT NULL CONSTRAINT DF_AE_When DEFAULT SYSUTCDATETIME(),
    OrderId         uniqueidentifier NULL,
    OrderVersion    int              NULL,
    EventType       varchar(40)      NOT NULL,   -- INGEST | UPDATE | READ_DENIED | ...
    Backend         varchar(20)      NULL,
    RunId           uniqueidentifier NULL,
    Detail          nvarchar(2000)   NULL
);
GO

/* =====================================================================
   INDEXES - each one is tied to a real API query pattern.
   ===================================================================== */

/* GET /orders?customerId=&status=  -> the primary list query.
   Included columns make it a covering index for the list projection. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Orders_Customer_Status')
CREATE NONCLUSTERED INDEX IX_Orders_Customer_Status
    ON ord.Orders (CustomerId, Status)
    INCLUDE (OrderNumber, OrderType, Project, CurrentVersion, SettlementDate,
             PrimaryState, MaxLoanAmount, PayloadBytes, CreatedDate);
GO

/* GET /orders?state=  (+ customer scoping) */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Orders_State_Customer')
CREATE NONCLUSTERED INDEX IX_Orders_State_Customer
    ON ord.Orders (PrimaryState, CustomerId)
    INCLUDE (Status, OrderNumber, MaxLoanAmount, SettlementDate);
GO

/* GET /orders?minLoanAmount=  -> range seek instead of scanning Loans */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Orders_MaxLoanAmount')
CREATE NONCLUSTERED INDEX IX_Orders_MaxLoanAmount
    ON ord.Orders (MaxLoanAmount)
    INCLUDE (CustomerId, Status, PrimaryState, OrderNumber);
GO

/* Incremental extraction into Fabric (change-based ingestion watermark) */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Orders_ModifiedDate')
CREATE NONCLUSTERED INDEX IX_Orders_ModifiedDate
    ON ord.Orders (ModifiedDate) INCLUDE (CustomerId, CurrentVersion);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Properties_Order')
CREATE NONCLUSTERED INDEX IX_Properties_Order
    ON ord.Properties (OrderId) INCLUDE ([State], County, City, Address1, Zip, Acreage, PropertyType);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Properties_State')
CREATE NONCLUSTERED INDEX IX_Properties_State
    ON ord.Properties ([State]) INCLUDE (OrderId, County);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_OrderParties_Order_Role')
CREATE NONCLUSTERED INDEX IX_OrderParties_Order_Role
    ON ord.OrderParties (OrderId, Role) INCLUDE (PartyId);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_OrderParties_Party')
CREATE NONCLUSTERED INDEX IX_OrderParties_Party
    ON ord.OrderParties (PartyId) INCLUDE (OrderId, Role);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Loans_Order')
CREATE NONCLUSTERED INDEX IX_Loans_Order
    ON ord.Loans (OrderId) INCLUDE (LoanAmount, LoanType, LoanNumber, LenderPartyId, InterestRate);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Loans_Amount')
CREATE NONCLUSTERED INDEX IX_Loans_Amount
    ON ord.Loans (LoanAmount) INCLUDE (OrderId, LoanType);
GO

/* Block reads.
   GET /orders/{id}          -> all blocks for a version
   GET /orders/{id}/title    -> BlockType filter
   The key order (OrderId, OrderVersion, BlockType, BlockSubType, Sequence)
   serves both with one index. JsonPayload is deliberately NOT included:
   at 15-750 KB per row it would duplicate the whole dataset in the index. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Blocks_Order_Version_Type')
CREATE NONCLUSTERED INDEX IX_Blocks_Order_Version_Type
    ON ord.OrderJsonBlocks (OrderId, OrderVersion, BlockType, BlockSubType, Sequence)
    INCLUDE (PayloadBytes, PayloadHash, LastModified);
GO

/* Incremental block extraction into Fabric */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Blocks_LastModified')
CREATE NONCLUSTERED INDEX IX_Blocks_LastModified
    ON ord.OrderJsonBlocks (LastModified) INCLUDE (OrderId, OrderVersion, BlockType, PayloadBytes);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_AuditEvents_Order')
CREATE NONCLUSTERED INDEX IX_AuditEvents_Order
    ON ord.AuditEvents (OrderId, OccurredUtc);
GO
