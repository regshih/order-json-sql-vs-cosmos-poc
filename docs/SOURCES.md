# SOURCES — Verified Microsoft Documentation for the Order-JSON POC

**Verified on 2026-09-13.**

Every fact below was fetched from a live Microsoft page on 2026-09-13 (learn.microsoft.com, azure.microsoft.com, devblogs.microsoft.com, pypi.org, or the official Azure Retail Prices API). Nothing here is from memory. Where a page contradicts itself or another page, the contradiction is called out explicitly. Where a number could not be retrieved, it is listed under **Unverified / could not confirm** rather than guessed.

Accessed date for every link in this document: **2026-09-13**.

---

## A. Azure Cosmos DB for NoSQL

### A.1 Maximum item size — still 2 MB

| Resource | Limit |
| --- | --- |
| Maximum size of an item | **2 MB** (UTF-8 length of JSON representation) |
| Maximum length of partition key value | 2,048 bytes (101 bytes if large partition-key isn't enabled) |
| Maximum length of ID value | 1,023 bytes |
| Maximum level of nesting for embedded objects / arrays | 128 |
| Maximum number of properties per item | No practical limit |
| Maximum length of property value / string property value | No practical limit |

Source: [Service quotas and default limits — Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits) (page `ms.date` 2025-09-03, `updated_at` 2026-08-25).

The 2 MB figure has **not** changed. The only documented exception is the API for MongoDB, where 16 MB documents are supported after feature enablement in the portal — this does **not** apply to the API for NoSQL ([same page, footnote 1](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits); [MongoDB 4.2 data types](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/feature-support-42)).

Related per-request limits that matter for multi-MB payloads:

| Resource | Limit |
| --- | --- |
| Maximum request size (stored procedure, CRUD) | **2 MB** |
| Maximum response size (e.g. paginated query) | **4 MB** |
| Maximum execution time for a single operation / query page | 5 sec |
| Maximum number of operations in a transactional batch | 100 |

Source: [Service quotas and default limits — Per-request limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits).

The docs explicitly call large items an anti-pattern: *"Storing very large items in Azure Cosmos DB results in high RU charges and can be considered as an anti-pattern. In particular, don't store binary content or large chunks of text that you don't need to query on. A best practice is to put this kind of data in Azure Blob Storage and store a reference (or link) to the blob in the item."* — [Optimize request cost](https://learn.microsoft.com/en-us/azure/cosmos-db/optimize-cost-reads-writes).

### A.2 Logical partition key size limit — still 20 GB

| Resource | Limit |
| --- | --- |
| Maximum storage across all items per (logical) partition | **20 GB** |
| Maximum RUs per partition (logical & physical) | **10,000** |
| Maximum number of distinct (logical) partition keys | Unlimited |
| Maximum storage per container | Unlimited |
| Minimum RU/s required per 1 GB | 1 RU/s |

Source: [Service quotas and default limits — Provisioned throughput](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits).

The same 20 GB logical-partition limit applies to **serverless** accounts ([Service quotas — Serverless](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)).

A **temporary** increase can be requested via a support ticket (quota type *"Temporary increase in container's logical partition key size"*), but the docs state: *"**SLA guarantees are not honored when the limit is increased**"* and it is *"intended as a temporary mitigation and not recommended as a long-term solution"* ([Service quotas — footnote 2](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)).

Physical partitions split automatically at **50 GB** ([Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys)).

### A.3 Hierarchical partition keys (subpartitioning)

**Status: generally available.** The current doc carries no preview banner ([Hierarchical partition keys — Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys), `ms.date` 2026-02-02, `updated_at` 2026-04-27).

**Maximum depth: three levels.** *"you can configure up to a three-level hierarchy for your partition keys"* and *"The overall depth can't exceed three levels."* ARM/Bicep requires `kind: 'MultiHash'` and `version: 2`.

Documented limitations (verbatim from the **Limitations and known issues** section):

- *"Working with containers that use hierarchical partition keys is supported only in the .NET v3 SDK, in the Java v4 SDK, in the Python SDK, and in the preview version of the JavaScript SDK. ... Support for other SDKs, including Python, isn't available currently."* — **note this sentence is self-contradictory on the live page** (it lists Python as supported and then as unsupported). The supported-SDK table on the same page lists **Python SDK >= 4.6.0** as supported, and the page includes working Python code samples for create-container, create-item, point-read, and single-partition query. Treat Python as supported, and verify empirically in the POC.
- *"There are limitations with various Azure Cosmos DB connectors (for example, with Azure Data Factory)."*
- *"You can specify hierarchical partition keys only up to three layers in depth."*
- *"Hierarchical partition keys can currently be enabled only on new containers. You must set partition key paths at the time of container creation, and you can't change them later."*
- *"Hierarchical partition keys are currently supported only for the API for NoSQL accounts. The APIs for MongoDB and Cassandra aren't currently supported."*
- *"Hierarchical partition keys aren't currently supported with the users and permissions feature. You can't assign a permission to a partial prefix of the hierarchical partition key path."*

**Container recreation is required to adopt or change HPK.** The migration path documented is: create a new container with the desired HPK config → copy data with [container copy jobs](https://learn.microsoft.com/en-us/azure/cosmos-db/container-copy) (offline) or the [change feed](https://learn.microsoft.com/en-us/azure/cosmos-db/change-feed) (live) → repoint the app → validate.

Supported SDK minimums:

| SDK | Supported versions |
| --- | --- |
| .NET SDK v3 | >= 3.33.0 |
| Java SDK v4 | >= 4.42.0 |
| JavaScript SDK v4 | 4.0.0 |
| Python SDK | >= 4.6.0 |

**Query routing behavior** (important for design): only a *prefix* of the hierarchy routes efficiently, and the values must be in the `WHERE` clause. *"For queries that target a prefix of the hierarchical partition key path, you should include those leading partition key values in the WHERE clause so the query can be routed efficiently. Supplying values only via `PartitionKeyBuilder` doesn't by itself guarantee efficient routing."*

| Filter | Routing |
| --- | --- |
| All three levels | Single logical + physical partition |
| Level 1 + level 2 | Targeted subset of physical partitions |
| Level 1 only | Targeted subset of physical partitions |
| Level 2 only, or level 3 only | **Full fan-out across all physical partitions** |

**Cardinality warning for the first level:** *"Having low cardinality at the first level of the hierarchical partition key limits all of your write operations at the time of ingestion to just one physical partition until it reaches 50 GB and splits ... the maximum throughput your workload can theoretically achieve to ingest data is number of physical partitions * 10k."* Partition splits *"can take between 4-6 hours to complete."*

**Using `id` as the last level is explicitly recommended:** *"When using hierarchical partition keys, you can add the item ID as the last level in your hierarchy to guarantee that you can scale beyond the logical partition key limit of 20 GB."*

**Indexing interaction:** *"The partition key (unless it is also `/id`) is not indexed and should be included in the index. ... In the case of hierarchical partition keys, you want to include the individual levels of the partition key hierarchy in your indexing policy to ensure efficient query performance."* — [Indexing policies](https://learn.microsoft.com/en-us/azure/cosmos-db/index-policy).

### A.4 Point read vs query RU characteristics

*"reading a single item by its ID and partition key uses one request unit. The item should be about 1 KB in size."* — [Request Units in Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/request-units).

The documented scaling table:

| Item size | Cost of one point read |
| --- | --- |
| 1 KB | **1 RU** |
| 100 KB | **10 RUs** |

Source: [Optimize request cost — Point reads](https://learn.microsoft.com/en-us/azure/cosmos-db/optimize-cost-reads-writes).

Key statements from the same page:

- *"The only factor affecting the RU charge of a point read (besides the consistency level used) is the size of the item retrieved."*
- *"When using either the strong or bounded staleness consistency levels, the RU cost of any read operation (point read or query) is doubled."*
- Read-operation efficiency ordering: point read → single-partition filtered query → query without equality/range filter → query without filters.
- *"In the API for NoSQL, point reads can only be made using the REST API or SDKs. Queries that filter on one item's ID and partition key aren't considered a point read."*
- Writes: *"Inserting a 1-KB item without indexing costs around ~5.5 RUs. Replacing an item costs two times the charge required to insert the same item."*
- Query costing example: *"if a query returns a thousand 1-KB items, the cost of the operation is 1000."*

**Implication for multi-MB items:** the documented curve is sub-linear (100x size → 10x RU between 1 KB and 100 KB), but Microsoft publishes **no** point-read RU figure above 100 KB. Extrapolating to 1–5 MB is not documented — measure it. See *Unverified* section.

Factors affecting RU charge generally ([Request Units](https://learn.microsoft.com/en-us/azure/cosmos-db/request-units)): item size; item indexing; item property count; indexed properties; data consistency (*"strong and bounded staleness consistency levels consume approximately two times more RUs while performing read operations"*); type of reads (*"Point reads cost fewer RUs than queries"*); query patterns; script usage.

### A.5 Indexing policy

Source: [Indexing policies in Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/index-policy).

- **Default:** *"The default indexing policy for newly created containers indexes every property of every item and enforces range indexes for any string or number."*
- Indexing modes: **Consistent** (synchronous) and **None**. Lazy indexing is legacy — *"New containers cannot select lazy indexing."*
- **Write RU impact:** *"By optimizing the number of paths that are indexed, you can substantially reduce the latency and RU charge of write operations."* And from [Optimize request cost](https://learn.microsoft.com/en-us/azure/cosmos-db/optimize-cost-reads-writes): *"Optimizing your indexing policy to only index the properties that your queries filter on can make a huge difference in the RUs consumed by your write operations. ... it's highly recommended to reevaluate and customize your indexing policy when going to production."*
- **Index size:** *"If all the properties are indexed, then the index size can be larger than the data size."* Total consumed storage = data size + index size.
- **Excluding large subtrees** is the documented mechanism for large-property containers: *"You might need to configure the indexing policy for containers with large or complex item structures to reduce RU consumption"* ([Service quotas](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)).
- Path syntax: scalar = `/path/?`; array elements = `/[]`; wildcard subtree = `/*`. Any policy must include root `/*` as either an included or an excluded path.
- Recommended strategy: *"Include the root path to selectively exclude paths that don't need to be indexed. This approach is recommended as it lets Azure Cosmos DB proactively index any new property."*
- Precedence: more precise path wins; `/a/b/?` beats `/a/?`; `/a/?` beats `/a/*`.
- `id` and `_ts` are always indexed under Consistent mode and cannot be disabled. `_etag` is excluded by default.
- **All explicitly included paths add an index entry for every item, even where the path is undefined.**
- Index transformation is online, in-place, asynchronous, and **consumes RUs**. Removing an index takes effect immediately; adding one requires a transformation. Group index removals into a single policy change.
- TTL requires indexing: you cannot set `indexingMode: none` on a TTL-enabled container. For TTL-only with no indexed paths, use `consistent` mode with no included paths and `/*` as the only excluded path.

Documented index-related limits ([Service quotas — SQL query limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)):

| Resource | Limit |
| --- | --- |
| Maximum explicitly included paths per container | 1500 (increasable via support) |
| Maximum explicitly excluded paths per container | 1500 (increasable via support) |
| Maximum properties in a composite index | 8 |
| Maximum number of paths in a composite index | 100 |
| Maximum number of unique keys per container | 10 |
| Maximum length of SQL query | 512 KB |

### A.6 Autoscale vs provisioned vs serverless

Sources: [Service quotas and default limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits) and [Serverless in Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/serverless).

| Provisioning type | Resource | Limit |
| --- | --- | --- |
| Manual throughput | Minimum RU/s per container | **400** |
| Manual throughput | Minimum RU/s per database (shared) | 400 for the first 25 containers |
| Autoscale | Minimum max RU/s per container | **1000** |
| Autoscale | Minimum max RU/s per database (shared) | 1000 for the first 25 containers |
| Both | Maximum RUs per container/database | 1,000,000 (increasable via support) |
| Both | Maximum RUs per physical/logical partition | 10,000 |

Minimum-RU formulas:

- Manual, per container: `MAX(400, current storage GB * 1 RU/s, highest RU/s ever provisioned / 100)`
- Autoscale, per container: `MAX(1000, current storage GB * 10 RU/s, highest RU/s ever provisioned / 10)`, rounded up to nearest 1000

Autoscale behavior ([Limits for autoscale provisioned throughput](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)):

- Scales between `0.1 * Tmax` and `Tmax`.
- *"Minimum billable RU/s per hour: `0.1 * Tmax`. Billing is per hour, based on the highest RU/s the system scaled to during the hour, or `0.1*Tmax`, whichever is higher."*

Serverless ([Serverless consumption-based account type](https://learn.microsoft.com/en-us/azure/cosmos-db/serverless)):

- *"A serverless account can run only in a single Azure region. It isn't possible to add more Azure regions to a serverless account after you create the account."*
- Cannot pass, read, or update throughput on a serverless container; cannot create a shared-throughput database.
- *"A serverless container begins with a throughput of 5,000 RU/s. Each physical partition within a serverless container can handle up to 5,000 RU/s, meaning the maximum throughput of the container depends on the total number of physical partitions."*
- Per-logical-partition storage limit is the same 20 GB; max storage per container unlimited; max databases+containers per account 500; regions: 1.
- Best fit: *"intermittent and unpredictable traffic and long idle times"*, *"Low (less than 10 percent) average-to-peak traffic ratio."*
- Serverless does **not** support lazy indexing.
- Availability zones are supported in designated regions.

Account-level limits ([Service quotas — Per-account](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)): max 500 databases+containers per account (**cannot be increased**); max 25 containers per shared-throughput database; max account metadata throughput 240 RU/s; max custom data-plane RBAC role definitions 100; max role assignments 2,000.

### A.7 Consistency levels and RU implications for reads

Source: [Consistency levels in Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/consistency-levels).

Five levels, strongest to weakest: **Strong, Bounded staleness, Session, Consistent prefix, Eventual.**

| Consistency level | Quorum reads | Quorum writes |
| --- | --- | --- |
| Strong | Local Minority | Global Majority |
| Bounded Staleness | Local Minority | Local Majority |
| Session | Single Replica (using session token) | Local Majority |
| Consistent Prefix | Single Replica | Local Majority |
| Eventual | Single Replica | Local Majority |

*"For strong and bounded staleness, reads are done against two replicas in a four-replica set (minority quorum) to ensure consistency guarantees. Session, consistent prefix, and eventual consistency use single-replica reads. As a result, for the same number of request units, read throughput for strong and bounded staleness is half that of the other consistency levels."* And: *"The RU cost of reads for local minority reads is twice that of weaker consistency levels."*

Write throughput per RU is **identical across all consistency levels**.

Latency SLOs: *"Read latency for all consistency levels is guaranteed to be less than 10 milliseconds at the 99th percentile. Average read latency, at the 50th percentile, is typically 4 milliseconds or less."* Write latency likewise <10 ms P99 / ~5 ms P50, except multi-region strong-consistency accounts.

Strong consistency is unavailable with multiple write regions, and is blocked by default for accounts spanning regions more than 5,000 miles / 8,000 km apart.

### A.8 Microsoft Entra ID / managed identity data-plane RBAC, and disabling key auth

Source: [Connect using role-based access control and Microsoft Entra ID — Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-connect-role-based-access-control) (`ms.date` 2025-09-10, `updated_at` 2026-04-29).

- Built-in data-plane roles: **`Cosmos DB Built-in Data Reader`** and **`Cosmos DB Built-in Data Contributor`**. The Data Contributor definition ID is the fixed GUID **`00000000-0000-0000-0000-000000000002`**, scoped as `/subscriptions/.../providers/Microsoft.DocumentDB/databaseAccounts/<account>/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002` — *"the identifier (`00000000-0000-0000-0000-000000000002`) is unique across all role definitions in your account."*
- Full list: [Data plane security reference — built-in roles](https://learn.microsoft.com/en-us/azure/cosmos-db/reference-data-plane-security#built-in-roles).
- **Key-based (local) auth can be disabled** via the account property `properties.disableLocalAuth`. CLI:
  ```
  az resource update --set properties.disableLocalAuth=true ...
  ```
  Bicep: `disableLocalAuth: true`. PowerShell: `$resource.Properties.DisableLocalAuth = $true`.
  *"Disable key-based authentication to your existing account so that applications are required to use Microsoft Entra ID authentication."*
- Limits: 100 custom role definitions and 2,000 role assignments per account ([Service quotas — Role-based access control](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)).

Relevant to Fabric mirroring (see §C.2): mirroring requires the data actions `Microsoft.DocumentDB/databaseAccounts/readMetadata` and `Microsoft.DocumentDB/databaseAccounts/readAnalytics`, and **managed identities and read-only account keys are *not* supported** for the mirroring connection ([Cosmos DB mirroring limits](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-limitations)).

### A.9 Python SDK

Source: [azure-cosmos on PyPI](https://pypi.org/project/azure-cosmos/).

- Package name: **`azure-cosmos`**.
- Latest released version: **4.17.0**, published **2026-09-09**.
- Python support: 3.8+ per the prerequisites section; **4.14.0 and later require Python 3.9+**.
- 4.17.0 added the `enable_compact_utf8_item_writes` client option, *"to reduce request sizes for item write operations by serializing Unicode as compact UTF-8"* — directly relevant to a 2 MB item ceiling.
- Sync client: `from azure.cosmos import CosmosClient`. Async client: `from azure.cosmos.aio import CosmosClient`.
- Async guidance: *"it is highly recommended to use the `async with` keywords. This creates a context manager that will initialize and later close the async client."* Query results are async iterators (`async for`). The async client attempts cross-partition queries without an explicit flag.
- Hierarchical partition keys require `azure-cosmos >= 4.6.0` ([Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys)). Python HPK usage: `PartitionKey(path=["/tenantId", "/userId", "/sessionId"], kind="MultiHash")`; point reads pass the key as a list: `container.read_item(item=item_id, partition_key=["a","b","c"])`.

---

## B. Azure SQL Database

### B.1 Native `json` data type — GA in Azure SQL Database

**Status: generally available in Azure SQL Database.** The doc states verbatim:

> The JSON data type:
> - is generally available for Azure SQL Database and Azure SQL Managed Instance with the **SQL Server 2025** or **Always-up-to-date** update policy.
> - is in preview for SQL Server 2025 (17.x) and SQL database in Fabric.

Sources: [JSON data type (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/data-types/json-data-type?view=azuresqldb-current) (`ms.date` 2026-01-14) and [Index JSON data](https://learn.microsoft.com/en-us/sql/relational-databases/json/index-json-data?view=azuresqldb-current).

GA was announced **2025-05-19**: [Announcing the General Availability (GA) of JSON data type & JSON aggregates](https://devblogs.microsoft.com/azure-sql/announcing-the-general-availability-ga-of-json-data-type-json-aggregates/) — covering the native `json` type plus `JSON_OBJECTAGG` and `JSON_ARRAYAGG`. Mirror post: [Microsoft Community Hub](https://techcommunity.microsoft.com/blog/azuresqlblog/announcing-the-general-availability-ga-of-json-data-type-and-json-aggregates/4415303).

**Documented size limits for the `json` type:**

| Field | Limitation |
| --- | --- |
| JSON data type size (binary) | **Up to 2 GB** |
| Number of unique keys | Up to 32K |
| Per key string size | 7,998 bytes |
| Per string value size | 536,870,911 bytes (~512 MB) |
| Number of properties in one object | Up to 65,535 |
| Number of elements in one array | Up to 65,535 |
| Number of nested levels in JSON document | 128 |

Source: [JSON data type — Size limitations](https://learn.microsoft.com/en-us/sql/t-sql/data-types/json-data-type?view=azuresqldb-current).

**Behavior vs `nvarchar(max)`** (all from the same page):

- Stored as a **native binary format**, internally UTF-8 with `Latin1_General_100_BIN2_UTF8` collation.
- Documented benefits over `varchar`/`nvarchar`: *"More efficient reads, as the document is already parsed; More efficient writes, as the query can update individual values without accessing the entire document; More efficient storage, optimized for compression; No change in compatibility with existing code."*
- Input **must be a JSON object or array** — scalars, booleans, and `null` are rejected (RFC 4627).
- Available under all database compatibility levels.
- Explicit `CAST`/`CONVERT` to/from `char`, `nchar`, `varchar`, `nvarchar` only; **no implicit conversions** (same as `xml`).
- You **can** `ALTER TABLE` an existing `varchar(max)` column to `json`, but you **cannot** convert a `json` column back to a string/binary type with `ALTER TABLE`.
- Cannot be used with `sql_variant`; cannot create an alias type over it (`CREATE TYPE`).
- Usable as parameter/return type of UDFs and procs; compatible with triggers and views. `SELECT ... INTO` preserves the `json` type.
- **Client-visibility caveat:** *"`sp_describe_first_result_set` ... doesn't correctly return the json data type. Therefore, many data access clients and driver see a varchar or nvarchar data type. Currently, TDS >= 7.4 (with UTF-8) sees varchar(max) with Latin_General_100_bin2_utf8. Currently, TDS < 7.4 sees nvarchar(max) with database collation."* This matters for pyodbc.
- **`OPENJSON` caveat:** *"Currently, the `OPENJSON()` function doesn't accept the json data type in some platforms. Currently, it's an implicit conversion. Explicitly convert to nvarchar(max) first."* (In SQL Server 2025, `OPENJSON` does support `json`.)
- The `.modify()` method for in-place updates is *"currently in preview and only available in SQL Server 2025 (17.x)"* — **not available in Azure SQL Database today.**
- `bcp` native format writes the document as `varchar`/`nvarchar`; a format file is required to designate a `json` column.

### B.2 JSON functions available

Source: [JSON Functions (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/functions/json-functions-transact-sql?view=azuresqldb-current) (`updated_at` 2026-08-24). Applies to SQL Server 2016+, Azure SQL Database, Azure SQL Managed Instance, Azure Synapse Analytics, **SQL analytics endpoint in Microsoft Fabric**, **Warehouse in Microsoft Fabric**, and SQL database in Fabric.

| Function | Description |
| --- | --- |
| `ISJSON` | Tests whether a string contains valid JSON. |
| `JSON_ARRAY` | Constructs JSON array text from zero or more expressions. |
| `JSON_ARRAYAGG` | Constructs a JSON array from an aggregation of SQL data or columns. |
| `JSON_MODIFY` | Updates the value of a property in a JSON string and returns the updated JSON string. |
| `JSON_OBJECT` | Constructs JSON object text from zero or more expressions. |
| `JSON_OBJECTAGG` | Constructs a JSON object from an aggregation of SQL data or columns. |
| `JSON_PATH_EXISTS` | Tests whether a specified SQL/JSON path exists in the input JSON string. |
| `JSON_QUERY` | Extracts an object or an array from a JSON string. |
| `JSON_VALUE` | Extracts a scalar value from a JSON string. |
| `OPENJSON` | Parses JSON text and returns objects and properties as rows and columns. |

`FOR JSON` (PATH / AUTO / `WITHOUT_ARRAY_WRAPPER`) is a query clause, not listed in the function table, but is used throughout the current Azure SQL JSON docs ([Index JSON data](https://learn.microsoft.com/en-us/sql/relational-databases/json/index-json-data?view=azuresqldb-current)).

**`JSON_CONTAINS` is NOT in the Azure SQL Database function list.** It is documented only alongside `CREATE JSON INDEX` for SQL Server 2025 ([CREATE JSON INDEX](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-json-index-transact-sql?view=sql-server-ver17)). The GA blog author (Umachandar Jayachandran, Microsoft) stated in the comments that `JSON_CONTAINS` is *"available in SQL Server 2025 preview, with rollout to Azure planned"* ([GA blog](https://devblogs.microsoft.com/azure-sql/announcing-the-general-availability-ga-of-json-data-type-json-aggregates/)). Do not design against `JSON_CONTAINS` in Azure SQL Database today.

`JSON_OBJECTAGG` and `JSON_ARRAYAGG` **are GA** in Azure SQL Database as of 2025-05-19 (same blog).

### B.3 JSON indexing

**There is no `CREATE JSON INDEX` in Azure SQL Database today.**

The `CREATE JSON INDEX` reference page's **Applies to** line reads only: *"SQL Server 2025 (17.x)"*, and the page states *"Creating JSON indexes is currently in preview and only available in SQL Server 2025 (17.x)."* — [CREATE JSON INDEX (Transact-SQL)](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-json-index-transact-sql?view=sql-server-ver17) (`ms.date` 2025-10-27, `updated_at` 2026-07-21; requesting the `azuresqldb-current` moniker redirects to the SQL Server 2025 view).

The [Index JSON data](https://learn.microsoft.com/en-us/sql/relational-databases/json/index-json-data?view=azuresqldb-current) page (which *does* apply to Azure SQL Database) says only: *"In SQL Server 2025 (17.x), you can use the CREATE JSON INDEX feature"* and otherwise directs you to standard indexes: *"Indexes work the same way on JSON data in varchar/nvarchar or the native json data type."*

The GA blog comments state JSON INDEX *"is in the process of being rolled out to Azure SQL Database and Azure SQL Managed Instance with version-less policy"* with a separate announcement promised at worldwide availability — as of 2026-09-13 that announcement is not reflected in the reference docs. **Treat native JSON indexes as unavailable in Azure SQL Database until the `CREATE JSON INDEX` Applies-to line lists it.**

#### Supported approach today: computed column + standard index

From [Index JSON data](https://learn.microsoft.com/en-us/sql/relational-databases/json/index-json-data?view=azuresqldb-current):

```sql
ALTER TABLE Sales.SalesOrderHeader
    ADD vCustomerName AS JSON_VALUE(Info, '$.Customer.Name');

CREATE INDEX idx_soh_json_CustomerName
    ON Sales.SalesOrderHeader(vCustomerName)
    INCLUDE (SalesOrderNumber, OrderDate);
```

Documented caveats:

- The engine matches the query expression to the computed-column expression automatically — *"You don't have to rewrite your queries."*
- Key-length ceiling: *"The maximum key length for a nonclustered index is 1700 bytes."* `JSON_VALUE` can return up to 8000 bytes; *"the values that are longer than 1700 bytes can't be indexed. If you try to enter the value in the indexed computed column that is longer than 1700 bytes, the DML operation fails."*
- *"For better performance, try to cast the value that you expose using a computed column into the smallest applicable data type. Use int and datetime2 types instead of string types."*
- *"A computed column isn't persisted. A computed column is only computed when the index needs to be rebuilt. It doesn't occupy additional space in the table."*
- Indexes over JSON are **collation-aware** — `JSON_VALUE` inherits the collation of the source column, so an `ORDER BY` with a different `COLLATE` cannot use the index.
- Full-text search is listed as supported in Azure SQL Database ([Fabric SQL database limitations — feature comparison table](https://learn.microsoft.com/en-us/fabric/database/sql/limitations)): *"Yes, but third-party filters and word breakers aren't supported."*

For reference, the SQL Server 2025 `CREATE JSON INDEX` requirements (relevant only if the workload later moves to SQL Server 2025 or the feature lands in Azure SQL): the column must be the native `json` type (not `varchar(max)`/`nvarchar(max)`); the table **requires a clustered primary key**; *"The clustering key is limited to 31 columns and the maximum size of the index key should be less than 128 bytes"*; only one JSON index per `json` column, up to 249 per table; offline builds only (`ONLINE = OFF`); no computed `json` columns, views, table-valued variables, or memory-optimized tables; overlapping JSON paths are rejected; path changes require recreating the index; no index hints; `DATA_COMPRESSION` unsupported. Options include `OPTIMIZE_FOR_ARRAY_SEARCH`, `FILLFACTOR`, `DROP_EXISTING`, `MAXDOP` (*"currently always uses only a single processor"*).

### B.4 `nvarchar(max)` / LOB behavior

Source: [Maximum capacity specifications for SQL Server](https://learn.microsoft.com/en-us/sql/sql-server/maximum-capacity-specifications-for-sql-server?view=azuresqldb-current) (`ms.date` 2025-08-21).

| Specification | Value |
| --- | --- |
| Bytes per row | **8,060** |
| Bytes per short string column | 8,000 |
| Bytes per `varchar(max)`, `varbinary(max)`, `xml`, `text`, or `image` column | **2^31-1** (~2 GB) |
| Characters per `ntext` or `nvarchar(max)` column | **2^30-1** (~1 G characters, ~2 GB) |
| Bytes per index key | 900 (clustered), **1,700 (nonclustered)** |
| Bytes per `GROUP BY`, `ORDER BY` | 8,060 |
| Columns per table | 1,024 (30,000 with sparse column sets) |
| Columns per `SELECT` / `INSERT` / `UPDATE` statement | 4,096 |
| Batch size | 65,536 * network packet size (default packet 4 KB) |

Row-overflow: *"SQL Server supports row-overflow storage, which enables variable length columns to be pushed off-row. Only a 24-byte root is stored in the main record for variable length columns pushed out of row."* See [Large row support](https://learn.microsoft.com/en-us/sql/relational-databases/pages-and-extents-architecture-guide#large-row-support).

**Practical read on this for 1–5 MB order JSON:** a 1–5 MB payload always lives off-row as LOB whether stored as `nvarchar(max)` or `json`. The 8,060-byte row limit is not a blocker; the cost is LOB read I/O per row. The native `json` type's documented advantages (pre-parsed reads, compression-optimized storage, targeted writes) apply exactly to this pattern. Note the client-side TDS caveat in §B.1 — drivers see `varchar(max)`/`nvarchar(max)`.

### B.5 Service tiers for a read-heavy ~50 RPS workload with multi-MB payloads

Source: [Resource limits for single databases using the vCore purchasing model](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-vcore-single-databases?view=azuresql) (`ms.date` 2026-03-09).

**General Purpose, provisioned, standard-series (Gen5)** — `GP_Gen5_N`:

| vCores | 2 | 4 | 6 | 8 | 10 |
| --- | --- | --- | --- | --- | --- |
| Memory (GB) | 10.4 | 20.8 | 31.1 | 41.5 | 51.9 |
| Max data size (GB) | 1024 | 1024 | 1536 | 2048 | 2048 |
| Storage type | Remote SSD | Remote SSD | Remote SSD | Remote SSD | Remote SSD |
| Read IO latency | 5-10 ms | 5-10 ms | 5-10 ms | 5-10 ms | 5-10 ms |
| Write IO latency | 5-7 ms | 5-7 ms | 5-7 ms | 5-7 ms | 5-7 ms |
| Max data IOPS | 640 | 1280 | 1920 | 2560 | 3200 |
| Max log rate (MiB/s) | 9 | 18 | 27 | 36 | 45 |
| Max concurrent workers | 200 | 400 | 600 | 800 | 1000 |
| Max concurrent sessions | 30,000 | 30,000 | 30,000 | 30,000 | 30,000 |
| Number of replicas | 1 | 1 | 1 | 1 | 1 |
| **Read Scale-out** | **N/A** | **N/A** | **N/A** | **N/A** | **N/A** |

**Business Critical, provisioned, standard-series (Gen5)** — `BC_Gen5_N`:

| vCores | 2 | 4 | 6 | 8 | 10 |
| --- | --- | --- | --- | --- | --- |
| Memory (GB) | 10.4 | 20.8 | 31.1 | 41.5 | 51.9 |
| Max data size (GB) | 1024 | 1024 | 1536 | 2048 | 2048 |
| Max local storage (GB) | 4829 | 4829 | 4829 | 4829 | 4829 |
| Storage type | Local SSD | Local SSD | Local SSD | Local SSD | Local SSD |
| Read / Write IO latency | 1-2 ms | 1-2 ms | 1-2 ms | 1-2 ms | 1-2 ms |
| Max data IOPS | 8000 | 16,000 | 24,000 | 32,000 | 40,000 |
| Max log rate (MiB/s) | 24 | 48 | 72 | 96 | 96 |
| Max concurrent workers | 200 | 400 | 600 | 800 | 1000 |
| Number of replicas | 4 | 4 | 4 | 4 | 4 |
| **Read Scale-out** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** |
| In-memory OLTP storage (GB) | 1.57 | 3.14 | 4.71 | 6.28 | 8.65 |

**Hyperscale, provisioned, standard-series (Gen5)** — `HS_Gen5_N`:

| vCores | 2 | 4 | 6 | 8 | 10 | 12 | 14 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Memory (GB) | 10.4 | 20.8 | 31.1 | 41.5 | 51.9 | 62.3 | 72.7 |
| **Max data size (TB)** | **128** | 128 | 128 | 128 | 128 | 128 | 128 |
| Max log size | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited |
| Max local SSD IOPS | 8000 | 16,000 | 24,000 | 32,000 | 40,000 | 48,000 | 56,000 |
| Max log rate (MiB/s) | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Max concurrent workers | 200 | 400 | 600 | 800 | 1000 | 1200 | 1400 |
| Max concurrent sessions | 30,000 | 30,000 | 30,000 | 30,000 | 30,000 | 30,000 | 30,000 |
| **Secondary replicas** | **0-4** | 0-4 | 0-4 | 0-4 | 0-4 | 0-4 | 0-4 |
| **Read Scale-out** | **Yes** | Yes | Yes | Yes | Yes | Yes | Yes |
| Backup storage retention | 7 days | 7 days | 7 days | 7 days | 7 days | 7 days | 7 days |

Hyperscale IO latency (constant across sizes): local read 1-2 ms; **remote** (page server) read 1-4 ms; write 1-4 ms. Storage is multi-tiered (separate compute and storage components) — see [Hyperscale service tier architecture](https://learn.microsoft.com/en-us/azure/azure-sql/database/hyperscale-architecture?view=azuresql).

**General Purpose serverless, standard-series (Gen5)** — `GP_S_Gen5_N`:

| Min-max vCores | 0.5-1 | 0.5-2 | 0.5-4 | 0.75-6 | 1.0-8 |
| --- | --- | --- | --- | --- | --- |
| Min-max memory (GB) | 2.02-3 | 2.05-6 | 2.10-12 | 2.25-18 | 3.00-24 |
| Min-max auto-pause delay (min) | 15-10,080 | 15-10,080 | 15-10,080 | 15-10,080 | 15-10,080 |
| Max data size (GB) | 512 | 1024 | 1024 | 1024 | 2048 |
| Max data IOPS | 320 | 640 | 1280 | 1920 | 2560 |
| Max log rate (MiB/s) | 4.5 | 9 | 18 | 27 | 36 |
| Max concurrent workers | 75 | 150 | 300 | 450 | 600 |
| Max concurrent sessions | 30,000 | 30,000 | 30,000 | 30,000 | 30,000 |
| **Read Scale-out** | **N/A** | **N/A** | **N/A** | **N/A** | **N/A** |

*"The serverless compute tier is currently available on standard-series (Gen5) hardware only."* Hyperscale serverless is also documented (`HS_S_Gen5_N`).

**Sizing notes for the POC's stated shape (read-heavy, ~50 RPS, 1–5 MB payloads):**

- 50 RPS x 3 MB average ≈ **150 MB/s of read throughput**. On General Purpose the constraint is remote-SSD data IOPS and 5-10 ms read latency: at 64 KB IO size, `GP_Gen5_8` (2,560 IOPS) tops out near 160 MB/s from storage, so cold-cache reads will be the bottleneck; warm buffer-pool hits depend on 41.5 GB of memory. Business Critical's local SSD (1-2 ms, 32,000 IOPS at 8 vCore) is the documented low-latency path, and it is the smallest tier that also gives **read scale-out**. (The MB/s figure is an arithmetic inference from the documented IOPS and the documented IO-size range — Microsoft does not publish MB/s per SLO.)
- **Read scale-out** is available on Business Critical and Hyperscale only; it is **N/A** on General Purpose (provisioned *and* serverless). Hyperscale supports **0-4 secondary replicas**. This is the deciding factor if reads must be offloaded.
- **Max concurrent workers** scales at 100 per vCore (provisioned) / 75 per max-vCore (serverless). 50 RPS with sub-second queries is far inside these, but multi-MB LOB reads lengthen worker hold time — budget workers as `RPS x avg query seconds`.
- Hyperscale is the only tier with a 128 TB data ceiling and unlimited log size; the others cap at 1–4 TB.
- Serverless auto-pause (15 min minimum delay) causes a cold-start on the first request after a pause — unsuitable for a steady 50 RPS serving path, but fine for the POC's idle periods.
- The row-size / LOB limits of §B.4 apply identically to all tiers; there is no per-tier row-size difference.

Recent Hyperscale items ([What's new in Azure SQL Database](https://learn.microsoft.com/en-us/azure/azure-sql/database/doc-changes-updates-release-notes-whats-new?view=azuresql), `updated_at` 2026-09-01): 160 and 192 vCore Hyperscale Premium-series (preview, March 2026); multiple geo-replicas for Hyperscale (up to four, preview); convert geo-replicated non-Hyperscale DB to Hyperscale (GA October 2025); `sys.dm_hs_database_replicas` DMV (GA August 2025).

### B.6 Max row size / LOB limits

Covered in §B.4. Summary: **8,060 bytes per row**; **2^31-1 bytes** per `varchar(max)`/`varbinary(max)`/`xml`; **2^30-1 characters** per `nvarchar(max)`; **2 GB** for the native `json` type; **1,700 bytes** max nonclustered index key; row-overflow pushes variable-length columns off-row with a 24-byte in-row root.

### B.7 Microsoft Entra-only authentication and managed identity

Source: [Microsoft Entra-only authentication with Azure SQL](https://learn.microsoft.com/en-us/azure/azure-sql/database/authentication-azure-ad-only-authentication?view=azuresql) (`ms.date` 2026-09-01).

- *"When you enable Microsoft Entra-only authentication, you disable SQL authentication in the Azure SQL environment, including connections from SQL server administrators, logins, and users."* Existing SQL logins are not removed — they simply cannot connect.
- The Microsoft Entra admin **must be set first**; enabling fails otherwise. Removing the Entra admin while Entra-only is enabled is not supported.
- Enable/disable surfaces:
  - CLI: `az sql server ad-only-auth enable --resource-group <rg> --name <server>` (requires Azure CLI >= 2.14.2); `... disable`; `... get`.
  - PowerShell: `Enable-AzSqlServerActiveDirectoryOnlyAuthentication` (Az.Sql >= 2.10.0).
  - REST/ARM: resource type `Microsoft.Sql/servers/azureADOnlyAuthentications`, name `<server>/Default`, property **`azureADOnlyAuthentication: true`**.
  - T-SQL check: `SELECT SERVERPROPERTY('IsExternalAuthenticationOnly')` → 1 = enabled.
- Permissions: Owner/Contributor, or the **SQL Security Manager** role. *"The SQL Server Contributor and SQL Managed Instance Contributor roles don't have permissions to enable or disable the Microsoft Entra-only authentication feature."* Required actions: `Microsoft.Sql/servers/azureADOnlyAuthentications/*` (plus `Microsoft.Sql/servers/administrators/read` for the portal blade).
- A system-generated `CloudSA...` admin name still appears in the portal after provisioning; the page is explicit that *"Seeing a generated name doesn't mean that SQL authentication is enabled"*, it is *"not a shared account, a fallback, or a break-glass path"*, and *"The service permanently removes the password from the control plane after provisioning."*
- **Limitations under Entra-only in SQL Database:** Elastic jobs, SQL Data Sync, change data capture (mixed-identity cases), transactional replication into SQL Database, SQL Insights (preview), and `EXEC AS` for Entra group-member accounts are not supported.
- Azure Policy can force new servers to be created with Entra-only enabled ([Azure Policy for Microsoft Entra-only authentication](https://learn.microsoft.com/en-us/azure/azure-sql/database/authentication-azure-ad-only-authentication-policy)).
- **Microsoft Entra server principals (logins) reached GA in June 2026** ([What's new in Azure SQL Database](https://learn.microsoft.com/en-us/azure/azure-sql/database/doc-changes-updates-release-notes-whats-new?view=azuresql)).

### B.8 Connection pooling guidance for Python (pyodbc / ODBC Driver 18)

Source: [Driver-Aware Connection Pooling in the ODBC Driver for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/windows/driver-aware-connection-pooling-in-the-odbc-driver-for-sql-server?view=sql-server-ver17) (page applies to Azure SQL Database, Fabric SQL database, and SQL Server; `updated_at` 2026-08-24). Note: the page title and content scope this to **Windows**; see *Unverified* for Linux/unixODBC.

Documented behavior that determines pool membership — directly relevant to a Python service using managed identity:

- *"Whatever the connection properties, connections that use `SQLDriverConnect` go into a separate pool from connections that use `SQLConnect`."*
- *"When using Microsoft Entra ID ... and driver-aware connection pooling, the driver also uses the `Authentication` value to determine the membership in the connection pool."*
- *"Setting the `SQL_COPT_SS_ACCESS_TOKEN` attribute causes a connection to be pooled separately"* — a token-per-connection pattern fragments the pool; reuse one token across connections for its lifetime.
- A connection is **not** reused from the pool if any of these connection-string keywords differ: `Address`, `AnsiNPW`, `App`, `ApplicationIntent`, `Authentication`, `ColumnEncryption`, `Database`, `Encrypt`, `Failover_Partner`, `FailoverPartnerSPN`, `MARS_Connection`, `Network`, `PWD`, `Server`, `ServerSPN`, `TransparentNetworkIPResolution`, `Trusted_Connection`, `TrustServerCertificate`, `UID`, `WSID`.
- Attributes that also split pools: `SQL_ATTR_CURRENT_CATALOG`, `SQL_ATTR_PACKET_SIZE`, `SQL_COPT_SS_ACCESS_TOKEN`, `SQL_COPT_SS_AUTHENTICATION`, `SQL_COPT_SS_APPLICATION_INTENT` (read-only gets its own pool), `SQL_COPT_SS_MARS_ENABLED`, `SQL_COPT_SS_ENCRYPT`, `SQL_COPT_SS_TRUST_SERVER_CERTIFICATE`, and others.
- *"If two or more of the following connection attributes or connection keywords differ, a pooled connection isn't used: `Language`, `QuoteId`, `SQL_ATTR_TXN_ISOLATION`, `SQL_COPT_SS_QUOTED_IDENT`."* A single difference is tolerated but *"performance degrades because resetting the following parameters requires an extra network call."*
- Attributes that are reset client-side with **no** extra round trip (safe to vary): all statement attributes, `SQL_ATTR_AUTOCOMMIT`, `SQL_ATTR_CONNECTION_TIMEOUT`, `SQL_ATTR_LOGIN_TIMEOUT`, `SQL_ATTR_ODBC_CURSORS`, plus keywords `AutoTranslate`, `Description`, `MultisubnetFailover`, `Regional`, and the QueryLog/StatsLog family.
- *"Driver-aware connection pooling prevents a bad connection from being returned from the pool."*

**Practical guidance for the POC:** build one canonical connection string (identical `Server`, `Database`, `Encrypt=yes`, `TrustServerCertificate=no`, `Authentication`, `ApplicationIntent`) and reuse it for every connection so the whole service shares a single pool. `Encrypt=yes;TrustServerCertificate=no` is the documented posture for ODBC Driver 18 against Azure SQL. See *Unverified* for pyodbc-specific pooling and for Linux/unixODBC pooling configuration.

---

## C. Microsoft Fabric integration

### C.1 Fabric Mirroring for Azure SQL Database

**Status: generally available.** The [Azure SQL Database mirroring overview](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database) (`ms.date` 2025-07-03, `updated_at` 2026-07-06) carries **no preview banner**, and the [mirroring overview](https://learn.microsoft.com/en-us/fabric/mirroring/overview) lists Azure SQL Database in the supported-platform table with no "(preview)" suffix (unlike Azure Database for MySQL, Dremio, SharePoint List, and Fabric SQL database entries, which are marked preview).

**How it works** ([Mirroring overview — How does database mirroring work?](https://learn.microsoft.com/en-us/fabric/mirroring/overview)): *"Delta files arrive incrementally in Fabric from the data source. ... the SQL Database Engine scans the source database's transaction log at a high frequency. SQL Server publishes changes for each table to corresponding files in the Fabric landing zone. Inside Fabric, a replicator engine always runs and scans for newly published files at a high frequency. Fabric immediately merges incoming changes into the target delta table. **Changes can be published as fast as every 15 seconds.**"* Backoff logic reduces overhead during low activity.

**Latency caveat:** *"near real-time replication can depend on various factors, including: Location or region of source; Location or region of destination; Volume of changes; Frequency of changes; Network bandwidth and latency from source; Compute resources allocated to the on-premises data gateway."*

**Prerequisites:**

- Writable **primary** database only.
- *"Either the System Assigned Managed Identity (SAMI) or the User Assigned Managed Identity (UAMI) of the Azure SQL logical server needs to be enabled and must be the primary identity."* (UAMI support is *"currently in preview"*.)
- The connecting principal needs **`ALTER ANY EXTERNAL MIRROR`** (included in `CONTROL` or `db_owner`).
- Workspace **Admin** or **Member** role.
- If the database is not publicly accessible and doesn't allow Azure services, a VNet data gateway or on-premises data gateway is required.
- No cross-tenant mirroring (Azure SQL and Fabric workspace must be in the same Entra tenant).
- Tier support: *"All service tiers in the vCore purchasing model are supported."* For DTU, *"databases created in the Free, Basic, or Standard service tiers with fewer than 100 DTUs are not supported."*
- Cannot mirror if CDC is enabled, Azure Synapse Link for SQL is enabled, delayed transaction durability is enabled, or the DB is already mirrored into another Fabric workspace.

**Limitations that directly affect this POC** ([Limitations and behaviors for Fabric mirrored databases from Azure SQL Database](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database-limitations), `ms.date` 2026-02-26, `updated_at` 2026-07-06):

- **`json` and `vector` typed tables cannot be mirrored at all.** *"Currently, a table can't be mirrored if it has the **json** or **vector** data type. Currently, you can't ALTER a column to the vector or json data type when a table is mirrored."*
- **LOB columns are truncated at 1 MB.** *"If one or more columns in the table is of type Large Binary Object (LOB) with a size > 1 MB, the column data is truncated to size of 1 MB in Fabric OneLake."* This applies to `nvarchar(max)` / `varchar(max)` holding 1–5 MB order JSON.
- Unsupported column types (silently not mirrored): `image`, `text`/`ntext`, `xml`, `rowversion`/`timestamp`, `sql_variant`, UDTs, `geometry`, `geography`, and **computed columns**.
- Tables with a PK/clustered index on unsupported types cannot be mirrored: computed columns, UDTs, `geometry`, `geography`, `hierarchyid`, `sql_variant`, `timestamp`, `datetime2(7)`, `datetimeoffset(7)`, `time(7)`.
- *"Delta lake supports only six digits of precision"* — `datetime2(7)` loses the 7th digit; `datetimeoffset(7)` loses time zone and the 7th digit.
- Clustered columnstore indexes not supported.
- Source tables using temporal/ledger history tables, Always Encrypted, in-memory tables, graph, or external tables cannot be mirrored.
- Max **1,000 tables** per mirrored database. With "Mirror all data", it takes the first 1,000 sorted by schema then table name.
- Any DDL change triggers a **full reseed** of that table. Switch partition and alter primary key are blocked on mirrored tables.
- Row-level security, object-level permissions, dynamic data masking, and Purview sensitivity labels **are not propagated** to OneLake.
- `.dacpac` deployments need `/p:DoNotAlterReplicatedObjects=False`.
- Stopping mirroring disables it completely; starting again **reseeds all tables from scratch**.
- Since April 2025, tables without a primary key can be mirrored (with a documented workaround to pick up pre-existing PK-less tables).
- Mirroring is available in **all Microsoft Fabric regions**.

**Cost model** ([Mirroring overview — Cost of mirroring](https://learn.microsoft.com/en-us/fabric/mirroring/overview)):

> - *"Storage for replicas is free up to a limit based on the capacity size. Mirroring offers a free terabyte of mirroring storage for every capacity unit (CU) you purchase. For example, if you purchase an F64 capacity, you get 64 free terabytes worth of storage, exclusively used for mirroring. You pay for OneLake storage if you exceed the free mirroring storage limit or when the capacity is paused."*
> - *"Background Fabric compute used to replicate your data into Fabric OneLake is free and doesn't consume capacity. Requests directly to the OneLake for mirrored data consume capacity as normal OneLake compute consumption. The compute for querying data by using SQL, Power BI, or Spark is charged at regular rates."*
> - *"A running Microsoft Fabric capacity is required for mirroring. A paused or deleted capacity affects mirroring and no data is replicated."*

**Retention:** *"For mirrored databases created from the Fabric portal after mid-June 2025, the default retention is one day. For old mirrored databases, the default is seven days."* Configurable in **Settings → Delta table management** or via the public API `retentionInDays` property.

### C.2 Fabric Mirroring for Azure Cosmos DB for NoSQL

**Status: no preview banner on the current docs** — [Mirroring Azure Cosmos DB in Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db) (`ms.date` 2025-12-03, `updated_at` 2026-06-18) and it is listed without "(preview)" in the [mirroring overview](https://learn.microsoft.com/en-us/fabric/mirroring/overview) supported-platform table. *"Currently, only Azure Cosmos DB for NoSQL accounts are supported. Mirroring isn't available in sovereign clouds (Azure Government and Azure China)."*

**How it works:**

- *"Your Azure Cosmos DB data is continuously replicated directly into Fabric OneLake in near real-time, without any performance impact on your transactional workloads or consuming Request Units (RUs)."*
- *"Mirroring does not use Azure Cosmos DB's analytical store or change feed as a change data capture source."*
- *"The continuous backup feature is a prerequisite for mirroring. You can enable either 7-day or 30-day continuous backup ... 7-day continuous backup is recommended, as it is free of cost."*
- Creates a **mirrored database item** plus an autogenerated **read-only SQL analytics endpoint**.
- *"For an Azure Cosmos DB account with a primary write region and multiple read regions, mirroring chooses the Azure Cosmos DB read region closest to the region where Fabric capacity is configured."*

**Latency:** *"It could take a few minutes to replicate your Azure Cosmos DB Data into Fabric OneLake. Depending on your data's initial snapshot or the frequency of updates/deletes, replication could also take longer in some cases."* (Softer than the 15-second figure documented for SQL-log-based mirroring.)

**How nested JSON lands in Delta — it is stored as a JSON *string*, not flattened:**

> *"Nested data is shown as a JSON string in SQL analytics endpoint tables. You can use `OPENJSON`, `CROSS APPLY`, and `OUTER APPLY` in T-SQL queries or views to expand this data selectively. If you're using Power Query, you can also apply the `ToJson` function to expand this data. Through auto schema inference, nested data can be flattened through `OPENJSON` without having to explicitly define the nested schema."*
> — [Mirroring Azure Cosmos DB — Support for nested data](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db)

*"Mirroring doesn't have schema constraints on the level of nesting."* See [how to query nested data](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-how-to-query-nested).

**Critical column-width limit for multi-MB documents** ([Limits and quotas — SQL analytics endpoint limitations](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-limitations)):

> - *"Existing tables before November 18, 2025 only support **varchar(8000)** and need to be recreated to adopt new data type and support data up to 2 MB which is the maximum Cosmos DB document size."*
> - *"The SQL analytics endpoint supports **varchar(max)** up to 2 MB for tables created after November 18, 2025."*

So a mirrored database created today carries the full 2 MB Cosmos document; one created before 2025-11-18 truncates at 8,000 characters until recreated.

**Other documented limitations** ([Limits and quotas](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-limitations)):

- Supported account types: **API for NoSQL only**. Not MongoDB (RU), Gremlin, Table, Cassandra (RU), Managed Instance for Cassandra, DocumentDB (vCore), or sovereign clouds.
- Requires **7-day or 30-day continuous backup**; all continuous-backup limitations carry over, including *"the inability to disable continuous backup once enabled and lack of support for multi-region write accounts."* **Mirroring does not support accounts with multiple write regions.**
- You can't disable analytical store on an account with continuous backup enabled; you can't enable continuous backup on an account that previously disabled analytical store for a container.
- Auth: **read-write account keys or Microsoft Entra ID + RBAC only.** *"Read-only account keys and managed identities aren't supported."* Required Entra actions: `Microsoft.DocumentDB/databaseAccounts/readMetadata` and `Microsoft.DocumentDB/databaseAccounts/readAnalytics`. Rotating keys breaks mirroring until credentials are updated (stop → update → restart).
- *"Delete operations in the source container are immediately reflected in Fabric OneLake using mirroring. **Soft-delete operations using time-to-live (TTL) values isn't supported.**"*
- *"Mirroring doesn't support custom partitioning."*
- Schema drift: new properties become new columns; missing properties become nulls; renames keep **both** old and new columns; incompatible type changes become nulls. *"Replicating data using mirroring doesn't have a full-fidelity or well-defined schema."*
- Case-insensitive duplicate property names are disambiguated by appending `_n` (e.g. `addressName`, `AddressName_1`).
- OneLake data doesn't support private endpoints, customer-managed keys, or double encryption. CMK on OneLake is unsupported for mirroring.
- VNet/private-endpoint Cosmos accounts are supported via the **Network ACL Bypass** feature — no data gateway required ([Configure private networks for mirrored Cosmos DB](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-private-network)).
- Deleting and re-adding a container of the same name replaces the warehouse table with only the new container's data.
- Workspace **Admin** or **Member** required; stop = full disable; start = full reseed.
- Fabric Data Explorer inside the mirrored item is read-only and **does** consume source RUs.

**Cost:** *"Fabric compute used to replicate your Cosmos DB data into Fabric OneLake is free. Storage in OneLake is free of cost based the capacity size. ... The compute usage for querying data via SQL, Power BI, or Spark is still charged based on the Fabric Capacity. ... The Azure Cosmos DB continuous backup feature is a prerequisite to mirroring: Standard charges for continuous backup apply. There are no additional charges for mirroring on continuous backup billing."* Data Explorer use inside Fabric accrues normal Cosmos RU charges.

### C.3 OneLake shortcuts

Source: [Unify data sources with OneLake shortcuts](https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts) (`ms.date` 2026-07-13).

**External sources supported:**

- Amazon S3
- Amazon S3 compatible
- Azure Data Lake Storage (ADLS) Gen2
- Azure Blob Storage
- Dataverse
- Google Cloud Storage
- Iceberg
- OneDrive and SharePoint
- On-premises / network-restricted locations via the Fabric on-premises data gateway (OPDG)

**Internal OneLake shortcut targets** — *"Use internal OneLake shortcuts to reference data within existing Fabric items, including: KQL databases; Lakehouses; **Mirrored Azure Databricks Catalogs**; **Mirrored Databases**; Semantic models; SQL databases; Warehouses."*

**Yes — you can shortcut to a mirrored database's Delta tables.** "Mirrored Databases" is an explicitly listed internal shortcut source, and shortcuts can cross items and workspaces (*"the item types don't need to match"*). Confirmed from the other direction too: the Cosmos mirroring doc lists *"Fabric Lakehouse using shortcuts for data engineering and data science scenarios"* as a consumption path ([Mirroring Azure Cosmos DB](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db)).

**Where shortcuts can be created:** lakehouses and KQL databases. In a lakehouse `Tables` folder, shortcuts are allowed **only at the top level** and must be Delta-format to auto-register as tables. In `Files`, any level and any format. Schema shortcuts require [schema-enabled lakehouses](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas).

**Documented limits:**

| Limit | Value |
| --- | --- |
| Shortcuts per Fabric item | 100,000 |
| Shortcuts per single OneLake path | 10 |
| Maximum direct shortcut-to-shortcut links | 5 |
| Table API recognition of new shortcuts | *"up to a minute"* |

Other constraints: no `%` or `+` in names/paths; no non-Latin characters; Delta doesn't support spaces in table names, so a shortcut with a space isn't recognized as a Delta table; lineage for shortcuts to warehouses and semantic models isn't available; lineage view is scoped to a single workspace.

**Caching:** available for GCS, S3, S3-compatible, and OPDG shortcuts only; retention 1–28 days; *"Individual files greater than 1 GB in size aren't cached."*

**Identity caveat:** *"When users access shortcuts through Power BI semantic models using **Direct Lake over SQL** or T-SQL engines in **Delegated identity mode**, the calling user's identity isn't passed through to the shortcut target. Instead, the calling item's owner's identity is passed."*

REST API: [OneLake shortcuts](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts).

### C.4 Fabric Lakehouse vs Warehouse — SQL analytics endpoint and T-SQL surface

Source: [What is the SQL analytics endpoint for a lakehouse?](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint) (`ms.date` 2026-05-19, `updated_at` 2026-08-04).

- *"The SQL analytics endpoint gives you a **read-only** T-SQL query surface over the Delta tables in your lakehouse. ... Behind the scenes, the SQL analytics endpoint runs on the same engine as the Fabric Data Warehouse."*
- *"The SQL analytics endpoint isn't unique to lakehouses. Other Fabric items — including warehouses, mirrored databases, SQL databases, and Azure Cosmos DB — also auto-provision a SQL analytics endpoint. The experience and limitations are the same across all of them."*
- *"The SQL analytics endpoint operates in read-only mode over Delta tables — you can't insert, update, or delete data through it. To modify data, switch to the lakehouse and use Apache Spark."*
- Within that read-only boundary you **can**: run SELECTs (including over shortcut tables); create views, functions, and stored procedures; apply row-level and object-level security; serve Power BI over TDS; query across workspaces via shortcuts.
- **Warehouse** is the read-write T-SQL item (full DML/DDL). **Lakehouse / mirrored-database SQL analytics endpoints are read-only.**
- Cross-item three-part-name queries work: `SELECT * FROM ContosoWarehouse.dbo.ContosoSalesTable ...` ([Mirroring overview — Cross-database queries](https://learn.microsoft.com/en-us/fabric/mirroring/overview)).

**T-SQL surface limitations** ([Limitations of Fabric Data Warehouse](https://learn.microsoft.com/en-us/fabric/data-warehouse/limitations), `ms.date` 2026-07-29):

- *"Fabric Data Warehouse doesn't support every Transact-SQL statement available in SQL Server."* Full matrix: [T-SQL surface area](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area).
- *"Fabric Data Warehouse and SQL analytics endpoint connections require both the source and target items to be in the same region. **Cross-region connections ... aren't supported and might fail to authenticate or connect.**"*

**Endpoint limitations relevant to JSON payloads** ([SQL analytics endpoint — Limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint)):

- *"The **varchar(max)** data type is only supported in SQL analytics endpoints of **mirrored items and Fabric databases, and not for lakehouses.** Tables created after November 10, 2025 will automatically be mapped with varchar(max). Tables created before November 10, 2025 need to be recreated ... **Data truncation to 8 KB still applies on the tables in SQL analytics endpoint of the lakehouse, including shortcuts to a mirrored item.**"*
  → **Shortcutting a mirrored item into a lakehouse re-imposes the 8 KB truncation.** Query the mirrored item's own SQL analytics endpoint for full-fidelity JSON.
- Check with: `SELECT o.name, c.name, type_name(user_type_id) AS [type], max_length FROM sys.columns c JOIN sys.objects o ON c.object_id = o.object_id WHERE max_length = -1 AND type_name(user_type_id) IN ('varchar','varbinary');` (`max_length = -1` means `varchar(max)`).
- Only Delta Parquet under `/Tables` is autodiscovered; `/Files` tables are not exposed. External Delta tables created with Spark code aren't visible — use shortcuts.
- Delta column mapping **by name** supported (in preview); **by ID** is not.
- Adding a foreign key constraint blocks further schema changes on those tables.
- Scalar UDFs supported only when inlineable.
- Schemas named like system schemas (`sys`, `information_schema`) or DB roles (`db_owner`, `db_datareader`) fail to sync.
- **A workspace supports up to 150 warehouse + SQL analytics endpoint items combined.**

JSON functions **are** available in the Fabric SQL analytics endpoint and Fabric Warehouse ([JSON functions applies-to list](https://learn.microsoft.com/en-us/sql/t-sql/functions/json-functions-transact-sql?view=azuresqldb-current)).

### C.5 Fabric Data Factory — pipelines, Copy activity, Copy job, incremental copy

Source: [What is Copy job in Data Factory](https://learn.microsoft.com/en-us/fabric/data-factory/what-is-copy-job) (`ms.date` 2026-04-24, `updated_at` 2026-08-04). No preview banner on the item itself; several sub-features are individually marked preview.

- Positioning: *"Copy job is the go-to solution in Microsoft Fabric Data Factory for simplified data movement from many sources to many destinations — no pipelines required."*
- Copy modes: **Full copy** and **Incremental copy**.
- **Incremental copy** supports two mechanisms: **watermark-based** (`ROWVERSION`, datetime, date, string-interpreted-as-datetime, integer columns) and **CDC-based** when CDC is enabled on the source. *"Copy job automatically tracks and manages the state of the last successful run."* On failure, *"the copy job always resumes from the end of the last successful run."* You can reset incremental back to full at any time, per job or per table.
- CDC vs watermark guidance: use CDC when you need **deletes**, continuous sync, SCD Type 2 history, or minimal scan load; use watermark when CDC isn't available and you only need inserts and updates.
- Update methods: **Append** (default), **Merge**, **Overwrite**, **SCD Type 2**.
- **Azure SQL Database is explicitly supported** with automatic table creation and truncate-before-full-load. The same table lists Azure SQL Managed Instance, Azure Synapse SQL Pool, Fabric Lakehouse table, Fabric Warehouse, on-premises SQL Server, Oracle, Snowflake, and **SQL database in Fabric (Preview)**.
- **Auto-partitioning (preview)** for large tables is supported on: Amazon RDS for SQL Server, **Azure SQL Database**, Azure Synapse (SQL Pool), Fabric Data Warehouse, SQL database in Fabric, SQL Server, Azure SQL Managed Instance, Oracle, SAP HANA, and Fabric Lakehouse tables.
- **Azure Cosmos DB is not listed** in the auto-table-creation/truncate table or the auto-partitioning list on this page. The authoritative list is [Copy job sources and destinations](https://learn.microsoft.com/en-us/fabric/data-factory/copy-job-connectors) — verify Cosmos there before designing around it. (Cosmos DB *is* a Fabric Data Factory connector: [Azure Cosmos DB for NoSQL connector](https://learn.microsoft.com/en-us/fabric/data-factory/connector-azure-cosmosdb-for-nosql).)
- Query-based subsets: you can copy a filtered subset via a database query, for both full and incremental copy, including casting an unsupported column type to use it as a watermark.
- Audit columns can be appended per row: extraction time, source file path, workspace ID, Copy job ID / run ID / name, incremental window bounds, and custom values.
- Run options: run once, multiple schedules per job, manual trigger, or as a **copy job activity inside a pipeline** with event triggers.
- Hosting: cloud, on-premises gateway, or VNet data gateway.
- CI/CD: Git integration, deployment pipelines, and Variable library parameterization of connections.
- **Data-type mapping limitation:** *"Currently such data type conversion is supported when copying between tabular data. **Hierarchical sources/destinations are not supported**, which means there is no system-defined data type conversion between source and destination interim types."* Interim types are: Boolean, Byte, Byte array, Datetime, DatetimeOffset, Decimal, Double, GUID, Int16/32/64, SByte, Single, String, Timespan, UInt16/32/64. **There is no interim type that preserves nested JSON structure** — nested documents move as String.
- Regional availability matches Fabric. Pricing: [Copy job pricing](https://learn.microsoft.com/en-us/fabric/data-factory/pricing-copy-job).
- Decision guidance between Copy job, Mirroring, and Copy activity: [Data movement strategy decision guide](https://learn.microsoft.com/en-us/fabric/data-factory/decision-guide-data-movement).

### C.6 Fabric capacity SKUs, throttling, smoothing, and mirroring CU consumption

**SKU table** ([Understand Microsoft Fabric licenses and capacity](https://learn.microsoft.com/en-us/fabric/enterprise/licenses), `ms.date` 2026-06-15):

| SKU | Capacity Units (CUs) | Power BI SKU | Power BI v-cores |
| --- | --- | --- | --- |
| **F2** | **2** | – | 0.25 |
| **F4** | **4** | – | 0.5 |
| **F8** | **8** | EM/A1 | 1 |
| F16 | 16 | EM2/A2 | 2 |
| F32 | 32 | EM3/A3 | 4 |
| **F64** | **64** | P1/A4 | 8 |
| Trial | 64 | – | 8 |
| F128 | 128 | P2/A5 | 16 |
| F256 | 256 | P3/A6 | 32 |
| F512 | 512 | P4/A7 | 64 |
| F1024 | 1024 | P5/A8 | 128 |
| F2048 / F4096 / F8192 | 2048 / 4096 / 8192 | – | 256 / 512 / 1024 |

**F2 is the smallest F SKU.** *"F SKUs are the recommended capacities for Fabric. ... Pricing is regional, and Azure bills per second with a one-minute minimum."*

What F2 supports: **all Fabric experiences** — the capability gate is not the workload, it's Power BI viewing licenses. *"On F SKUs smaller than F64, each user viewing Power BI content must have Pro, PPU, or an individual trial. On F64 or larger, users with only a Free license and a viewer role can view Power BI content."* Non-Power-BI Fabric items (lakehouses, warehouses, notebooks, pipelines, mirrored databases) can be created and shared with a **Free** per-user license on any F SKU. Direct Lake guardrails do tighten at small SKUs — see §C.8.

**Throttling and smoothing** ([Understand capacity throttling and smoothing](https://learn.microsoft.com/en-us/fabric/enterprise/throttling), `ms.date` 2026-08-14):

- **Timepoints are 30 seconds.** 2,880 timepoints in 24 hours.
- *"Fabric smooths **interactive** operations over a minimum of five minutes, and up to 64 minutes depending on how much CU usage they consume. Fabric smooths **background** operations over a 24-hour period."*
- **Bursting:** *"operations can temporarily use more compute than the provisioned compute for the capacity SKU."*
- **Throttling stages:**

| Usage | Policy limit | Experience impact |
| --- | --- | --- |
| Usage <= 10 minutes | Overage protection | Jobs can consume 10 minutes of future capacity use without throttling. |
| 10 minutes < Usage <= 60 minutes | Interactive delay | Fabric delays user-requested interactive jobs by **20 seconds** at submission. |
| 60 minutes < Usage <= 24 hours | Interactive rejection | Fabric **rejects** user-requested interactive jobs. |
| Usage > 24 hours | Background rejection | Fabric rejects **all** requests. |

- **Warehouse and SQL analytics endpoint operations are classified as *background*:** *"Fabric reports almost all operations in the **Warehouse** category as *background* to take advantage of 24-hour smoothing of activity ... Classifying all data warehousing as background prevents peaks of CU utilization from triggering throttling too quickly."* But: *"When an interactive operation starts a chain that includes a background operation, Fabric can throttle the background operation as an interactive operation."*
- Real-Time Intelligence skips the 20-second-delay stage and only throttles at the 60-minute rejection stage.
- Throttled error signals: status `CapacityLimitExceeded`; *"Your organization's Fabric compute capacity has exceeded its limits. Try again later"*; *"Cannot load model due to reaching capacity limits"*.
- *"Throttling only affects operations requested after the capacity starts throttling."* In-flight operations complete.
- Remedies: scale the SKU up; pause and resume (bills accumulated future usage and resets to zero); or enable capacity overage billing, *"however, it costs three times the normal capacity rate"* ([Enable capacity overage](https://learn.microsoft.com/en-us/fabric/enterprise/enable-capacity-overage)).
- *"Throttling calculations include only billable operations."*
- Bursting and smoothing do **not** apply when Autoscale Billing for Spark is enabled.

**Does mirroring consume capacity CU? No — the replication itself doesn't.** *"Background Fabric compute used to replicate your data into Fabric OneLake is free and doesn't consume capacity. Requests directly to the OneLake for mirrored data consume capacity as normal OneLake compute consumption. The compute for querying data by using SQL, Power BI, or Spark is charged at regular rates."* And: *"A running Microsoft Fabric capacity is required for mirroring. A paused or deleted capacity affects mirroring and no data is replicated. However, the background compute used for replication doesn't consume your capacity units."* — [Mirroring overview — Cost of mirroring](https://learn.microsoft.com/en-us/fabric/mirroring/overview).

### C.7 Fabric REST APIs — what can be automated

**Base host:** `https://api.fabric.microsoft.com/v1`

**Item CRUD** ([Item management overview](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview), `ms.date` 2025-03-25):

Generic pattern:
```
POST   https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items
GET    https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items
GET    https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}
PATCH  https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}
DELETE https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}
POST   .../items/{itemId}/getDefinition
POST   .../items/{itemId}/updateDefinition
```

Support matrix for the items relevant here:

| Item type | Create (no definition) | Create with payload/definition | Service principal | Get | Update | Delete | List |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Lakehouse** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **Warehouse** | Yes | **No** | Yes | Yes | Yes | Yes | Yes |
| **Notebook** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **MirroredDatabase** | **No** | Yes (definition required) | **No** | Yes | Yes | Yes | Yes |
| **SemanticModel** | No | Yes | Yes | Yes | Yes | Yes | Yes |
| **Report** | No | Yes | Yes | Yes | Yes | Yes | Yes |
| **DataPipeline** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **CopyJob** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **SparkJobDefinition** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **Environment** | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| **GraphQLApi** | Yes | Yes (definition only) | Yes | Yes | Yes | Yes | Yes |
| SQLEndpoint | No | No | No | No | No | No | Yes (list only) |
| MirroredWarehouse | No | No | No | No | No | No | Yes (list only) |

*"The following item types are not supported for any API: Scorecard, Dataflow."*

Two automation constraints to design around:
1. **MirroredDatabase requires a definition on create and does NOT support service principals.** Automated mirrored-database creation must run as a user identity.
2. **Warehouse cannot be created with a definition** (empty create only); **SQL analytics endpoints cannot be created at all** — they are auto-provisioned.

**Yes, there is a Mirrored Database REST API** ([MirroredDatabase — Items](https://learn.microsoft.com/en-us/rest/api/fabric/mirroreddatabase/items)):

| Operation | Endpoint |
| --- | --- |
| Create Mirrored Database | `POST /v1/workspaces/{workspaceId}/mirroredDatabases` |
| List Mirrored Databases | `GET /v1/workspaces/{workspaceId}/mirroredDatabases` |
| Get Mirrored Database | `GET /v1/workspaces/{workspaceId}/mirroredDatabases/{mirroredDatabaseId}` |
| Update Mirrored Database | `PATCH /v1/workspaces/{workspaceId}/mirroredDatabases/{mirroredDatabaseId}` |
| Delete Mirrored Database | `DELETE /v1/workspaces/{workspaceId}/mirroredDatabases/{mirroredDatabaseId}` |
| Get Mirrored Database Definition | `POST /v1/workspaces/{workspaceId}/mirroredDatabases/{mirroredDatabaseId}/getDefinition` |
| Update Mirrored Database Definition | `POST /v1/workspaces/{workspaceId}/mirroredDatabases/{mirroredDatabaseId}/updateDefinition` |

The operation-group page lists exactly these seven operations by name; the paths follow the standard Fabric item-API shape. Data-retention configuration uses the `retentionInDays` property — [Configure data retention](https://learn.microsoft.com/en-us/fabric/mirroring/mirrored-database-rest-api#configure-data-retention). Start/stop mirroring and mirroring-status operations are documented in the same [Mirrored database REST API](https://learn.microsoft.com/en-us/fabric/mirroring/mirrored-database-rest-api) article — see *Unverified* item 9.

**Running a notebook (or any item job) on demand** ([Job Scheduler — Run On Demand Item Job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job)):

```
POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/instances
```

- Required delegated scope: `Item.Execute.All`, or specific e.g. `Notebook.Execute.All`.
- Supports **user, service principal, and managed identity** identities.
- Body (optional): `{ "executionData": {...}, "parameters": [ { "name": "...", "value": ..., "type": "Text|Number|Integer|Boolean|DateTime|Guid|VariableReference|Automatic" } ] }`.
- Returns **202 Accepted** with a `Location` header pointing at the job instance and a `Retry-After` header (seconds) — *"Clients must use this value to determine when to check the job status."*
- Error codes: `InsufficientPrivileges`, `InvalidJobType`, `TooManyRequestsForJobs`, `ItemNotFound`, `429 Too Many Requests`.
- *"The URL for this API has been updated to include the job type as part of the path, replacing the previous use of a query parameter. For backward compatibility, invocations using the query parameter are still supported."*

**Refreshing SQL analytics endpoint metadata**: [Refresh SQL endpoint metadata REST API](https://learn.microsoft.com/en-us/rest/api/fabric/sqlendpoint/items/refresh-sql-endpoint-metadata).

**Workspace creation** is part of the Core API surface ([Fabric REST API reference](https://learn.microsoft.com/en-us/rest/api/fabric/articles/)) — `POST /v1/workspaces`. Shortcut automation: [OneLake shortcuts API](https://learn.microsoft.com/en-us/rest/api/fabric/core/onelake-shortcuts).

### C.8 Direct Lake semantic models

Source: [Direct Lake overview](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview) (`ms.date` 2026-06-15, `updated_at` 2026-09-09).

**Current guidance:** *"The primary use case for Direct Lake storage mode is typically for IT-driven analytics projects that use lake-centric architectures."* A refresh *"copies only metadata (known as framing) ... which can take a few seconds to complete."*

**Two modes:**

- **Direct Lake on OneLake** — can span *"one or more Fabric data sources with Delta tables"*; **does not fall back to DirectQuery**; can be combined with Import tables (composite models); supports calculated tables (preview) and user-context calculated columns (preview). Cannot be built on non-materialized SQL views.
- **Direct Lake on SQL** — a **single** Fabric data source; uses the SQL analytics endpoint for table/view discovery and permission checks; **falls back to DirectQuery** when it can't read the Delta table directly (SQL views, SQL-based granular access control, RLS, exceeded guardrails). No composite modeling.

**Capacity guardrails:**

| Fabric SKU | Parquet files per table | Row groups per table | Rows per table (millions) | Max model size on disk/OneLake (GB) | Max memory (GB) |
| --- | --- | --- | --- | --- | --- |
| F2 | 1,000 | 1,000 | 300 | 10 | 3 |
| F4 | 1,000 | 1,000 | 300 | 10 | 3 |
| F8 | 1,000 | 1,000 | 300 | 10 | 3 |
| F16 | 1,000 | 1,000 | 300 | 20 | 5 |
| F32 | 1,000 | 1,000 | 300 | 40 | 10 |
| F64/FT1/P1 | 5,000 | 5,000 | 1,500 | Unlimited | 25 |
| F128/P2 | 5,000 | 5,000 | 3,000 | Unlimited | 50 |
| F256/P3 | 5,000 | 5,000 | 6,000 | Unlimited | 100 |
| F512/P4 | 10,000 | 10,000 | 12,000 | Unlimited | 200 |
| F1024/P5 and above | 10,000 | 10,000 | 24,000 | Unlimited | 400 |

*"The Max model size on disk/OneLake guardrail is evaluated at the model level and affects all queries. All other guardrails presented in the table are evaluated per query."* On exceeding guardrails: **Direct Lake on OneLake** — *"refresh fails and the model cannot be queried until the Delta tables are optimized"*; **Direct Lake on SQL** — *"Falls back to DirectQuery mode if fallback is enabled."*

**Limits that matter for JSON payloads:**

- *"The length of string column values is limited to **32,764 Unicode characters**."* A 1–5 MB JSON string will not fit in a Direct Lake column.
- *"Direct Lake storage mode tables don't support complex Delta table column types. Binary and GUID semantic types are also unsupported."*
- Non-numeric floats (`NaN`) unsupported.
- No hybrid tables, no model table partitions, no user-defined aggregations.
- Relationship one-side columns must be unique or queries fail; related-column data types must match.
- *"Creating a Direct Lake semantic model in a workspace that is in a different region of the data source workspace isn't supported."* Workaround for Direct Lake on SQL: create a lakehouse in the other region and shortcut the tables.
- No gateway (on-premises or VNet) support for either mode — *"supports only cloud connections."*
- Not supported in personal workspaces (My Workspace). Service principal profiles unsupported. Embedding requires a V2 embed token.
- XMLA tools must support `compatibilityLevel` 1604 or higher.
- RLS: strongly recommends a [fixed identity](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-fixed-identity) cloud connection.
- Direct Lake works over mirrored databases ([Mirroring overview — Direct Lake](https://learn.microsoft.com/en-us/fabric/mirroring/overview)).

### C.9 Fabric SQL analytics endpoint as an operational API query surface

**Microsoft's own framing is analytics, not operational serving.** The endpoint doc says *"you get high-performance, low-latency SQL queries without managing infrastructure"*, but every surrounding constraint points away from a 50-RPS low-latency serving path:

**1. Metadata/data sync is asynchronous, with a documented lag and an inactivity halt.**

> *"A background process is responsible for scanning the lakehouse for changes and keeping the SQL analytics endpoint up-to-date ... **Under normal operating conditions, the lag between a lakehouse and SQL analytics endpoint is less than one minute. The actual length of time can vary from a few seconds to minutes** depending on many factors ... **The background process runs only when the SQL analytics endpoint is active and it halts after 15 minutes of inactivity.**"*
> — [SQL analytics endpoint performance considerations](https://learn.microsoft.com/en-us/fabric/data-engineering/sql-analytics-endpoint-performance) (`ms.date` 2026-08-07, `updated_at` 2026-09-09)

Factors that make it worse: *"Automatic metadata discovery ... is a single instance per Fabric workspace. If you observe increased latency for changes to sync between lakehouses and the SQL analytics endpoint, it could be due to a large number of lakehouses in one workspace."*; small-file accumulation from frequent updates/deletes; high-cardinality partitioning; *"If there's an extremely large volume of table changes during the ETL processing, an expected delay occurs until all the changes are processed."* Recommended partition size: *"at least (or close to) 1 GB"*.

**2. A faster sync exists but is preview and new-endpoint-only.**

> *"In May 2026, the new metadata sync for the SQL analytics endpoint was announced as a **preview** feature. You can enable the new metadata sync process, which **applies only to new SQL analytics endpoints**. The new metadata sync option works to keep the data available for querying **within seconds** of it landing in the lakehouse."*
> — [SQL analytics endpoint metadata sync](https://learn.microsoft.com/en-us/fabric/data-engineering/sql-analytics-endpoint-metadata-sync) (`ms.date` 2026-05-29, `updated_at` 2026-09-09)

Enabled per-workspace under **Warehouse settings**; existing endpoints stay on the legacy sync. Freshness observable via `sys.dm_db_external_tables_log_status` (`last_update_time_utc`, `latest_log_version`, `latest_checkpoint_version`, `is_blocked`). Its limitations: no multi-part checkpoint support; cannot be enabled with workspace private link. Manual refresh paths: portal Refresh button; [Refresh SQL endpoint metadata REST API](https://learn.microsoft.com/en-us/rest/api/fabric/sqlendpoint/items/refresh-sql-endpoint-metadata); `sys.sp_dw_refresh_ext_table` (new-sync endpoints only). Guidance: *"Use the API only if you have schema changes ... For data-only changes in a SQL analytics endpoint, use the `sys.sp_dw_refresh_ext_table` system stored procedure to update a specific table."*

**3. Capacity throttling applies, with warehouse ops classified as background.** A rejected query returns `CapacityLimitExceeded` rather than degrading gracefully ([Throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)). See §C.6.

**4. Hard functional limits.** Read-only; no cross-region connections; 150 warehouse+endpoint items per workspace; lakehouse endpoints truncate strings at 8 KB (including shortcuts to mirrored items); Fabric T-SQL is a subset of SQL Server T-SQL.

**Conclusion for the design:** Microsoft publishes **no documented per-query latency SLO and no documented concurrent-query limit** for the SQL analytics endpoint (see *Unverified*). Combined with (a) up-to-one-minute-plus data lag on the legacy sync, (b) the 15-minute inactivity halt of the sync process, (c) background-classified capacity throttling with a hard rejection stage, and (d) no cross-region connectivity, the SQL analytics endpoint should be treated as an **analytical serving surface, not an operational API backend** for a 50-RPS low-latency path. Microsoft's own positioning for OLTP inside Fabric is SQL database in Fabric — *"The home in Fabric for OLTP workloads"*.

**Fabric SQL Database (the OLTP offering):**

Source: [SQL database in Microsoft Fabric overview](https://learn.microsoft.com/en-us/fabric/database/sql/overview) (`ms.date` 2026-05-19) and [Limitations for SQL database](https://learn.microsoft.com/en-us/fabric/database/sql/limitations) (`ms.date` 2026-08-24, `updated_at` 2026-09-01).

- *"SQL database in Microsoft Fabric is a developer-friendly transactional database, **based on Azure SQL Database** ... A SQL database in Fabric uses the same SQL Database Engine as Azure SQL Database."*
- *"Set up for analytics by **automatically replicating the data into OneLake near real time**"* — mirroring is automatic for all eligible tables, vs "manually enabled" for Azure SQL Database.
- **GA/preview status is ambiguous in the live docs** — the overview and limitations pages carry **no preview banner**, but other current pages still label it preview: the `json` data type page says the type *"is in preview for SQL Server 2025 (17.x) and SQL database in Fabric"*; the Fabric Data Warehouse limitations page links *"Limitations in SQL database in Microsoft Fabric (preview)"*; the Copy job connector table lists *"SQL database in Fabric (Preview)"*. Treat as a **GA product with preview sub-features** and confirm before committing.

**Documented resource limits:**

| Category | Fabric SQL database limit |
| --- | --- |
| Compute size | Up to **32 vCores** |
| Storage size | Up to **4 TB** |
| Tempdb size | Up to 1,024 GB |
| Log write throughput | Up to **50 MB/s** |
| Backups | Zone-redundant (ZRS), 7 days retention, enabled by default |
| Read-only replicas | *"Use the read-only SQL analytics endpoint for a read-only TDS SQL connection"* |
| Number of SQL databases | **150 per workspace** (3 in a trial capacity) |

**Relevance to this POC — gaps vs Azure SQL Database** (from the feature comparison table on the limitations page): no Always Encrypted, no application roles, **no CDC**, no ledger, no server-level roles, **no `EXECUTE AS`**, no elastic jobs/queries/pools, no active geo-replication, no failover groups, no geo-restore, no long-term retention, no CMK/TDE-BYOK (*"Customer-managed keys are not supported. Transparent Data Encryption (TDE) is not supported"* at the database level), no Azure CLI/PowerShell/Bicep tooling, no VNet service endpoints, no workspace-level private links, no SQL Server Auditing, connection policy locked to **Default** (requires outbound 11000-11999 plus 1433 to Azure SQL gateway IPs). Logins aren't supported — **only Entra-principal users**. Scaling is automatic; pause/resume is automatic.

Fabric SQL database mirroring has its own limitation set: [Limitations for Fabric SQL database mirroring](https://learn.microsoft.com/en-us/fabric/database/sql/mirroring-limitations) — e.g. clustered columnstore indexes must be created at table-creation time or mirroring must be stopped.

---

## D. Pricing

### D.1 Method and caveat

The public pricing pages at `azure.microsoft.com/pricing/details/...` render prices via client-side JavaScript. Fetched as HTML on 2026-09-13 they return **`$-` placeholders only** — no numbers were retrievable from [Azure Cosmos DB autoscale pricing](https://azure.microsoft.com/en-us/pricing/details/cosmos-db/autoscale-provisioned/) or [Microsoft Fabric pricing](https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/).

All figures below were therefore retrieved from the **official [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)** (`https://prices.azure.com/api/retail/prices`) on **2026-09-13**, `BillingCurrency: USD`, `CustomerEntityType: Retail` (pay-as-you-go list price, no negotiated discount). These are the same meters that back the pricing pages.

### D.2 Azure Cosmos DB for NoSQL

Query: `serviceName eq 'Azure Cosmos DB' and armRegionName eq 'westus3'` / `'eastus'`

| Meter | West US 3 | East US | Unit |
| --- | --- | --- | --- |
| **Provisioned throughput** (`Azure Cosmos DB \| RUs \| 100 RU/s`) | **$0.008** | **$0.008** | 1/Hour per 100 RU/s |
| **Autoscale** (`Azure Cosmos DB autoscale \| AP1–AP4 \| 100 RUs`) | **$0.012** | **$0.012** | 1/Hour per 100 RU/s |
| **Transactional storage** (`Azure Cosmos DB \| RUs \| Data Stored`) | **$0.25** | **$0.25** | 1 GB/Month |
| **Serverless** (`Azure Cosmos DB serverless \| RUs \| 1M RUs`) | **$0.25** | **$0.25** | per 1M RUs |
| Analytical storage (`Azure Cosmos DB Analytics Storage \| Standard \| Data Stored`) | $0.02 | $0.03 | 1 GB/Month |
| Backup — Standard snapshot (`Azure Cosmos DB Snapshot \| Standard \| Backup Data Stored`) | $0.12 | $0.12 | 1 GB/Month |
| Backup — Periodic LRS | not returned for westus3 | $0.10 | 1 GB/Month |
| Backup — Periodic ZRS | not returned for westus3 | $0.125 | 1 GB/Month |
| Backup — Periodic RA-GRS | not returned for westus3 | $0.20 | 1 GB/Month |
| Analytics Storage read operations | $0.005 | not re-verified | per 10K ops |

Notes:
- Autoscale is **1.5x** the provisioned rate per 100 RU/s ($0.012 vs $0.008), consistent across AP1/AP2/AP3/AP4 tiers and both regions. Autoscale bills on the hourly maximum scaled-to value, floored at `0.1 * Tmax` ([Autoscale limits](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)).
- "AP1 Entry Price" ($0.032/hr WUS3), AP2 ($0.16), AP3 ($0.40), AP4 ($0.80) are minimum entry meters for the autoscale plan tiers, not per-100-RU rates.
- The **7-day continuous backup** required by Fabric Cosmos mirroring is documented as *"free of cost"* ([Mirroring Azure Cosmos DB](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db)). The Backup meters above apply to periodic/30-day configurations.
- Pricing page for cross-reference: [Azure Cosmos DB pricing](https://azure.microsoft.com/en-us/pricing/details/cosmos-db/).

### D.3 Azure SQL Database — West US 3

Query: `serviceName eq 'SQL Database' and armRegionName eq 'westus3' and type eq 'Consumption'`

**Compute ($/vCore/hour):**

| Tier / hardware | Meter | $/vCore/hour |
| --- | --- | --- |
| **General Purpose, provisioned, Gen5** | `SQL Database Single/Elastic Pool General Purpose - Compute Gen5 \| vCore` | **$0.152217** |
| General Purpose, provisioned, Gen5, **zone redundant** | `... \| Zone Redundancy vCore` | $0.09133 (surcharge meter) |
| General Purpose, provisioned, **Fsv2-series** | `... General Purpose - Compute FSv2 Series \| vCore` | $0.13358 |
| **General Purpose, serverless, Gen5** | `SQL Database General Purpose - Serverless - Compute Gen5 \| vCore` | **$0.521758** |
| **Business Critical, provisioned, Gen5** | `SQL Database Single/Elastic Pool Business Critical - Compute Gen5 \| vCore` | **$0.304435** |
| **Hyperscale, provisioned, Gen5** | `SQL Database SingleDB/Elastic Pool Hyperscale - Compute Gen5 \| vCore` | **$0.18266** |
| Hyperscale, provisioned, **Premium-series** | `SQL Database Single/Elastic Pool Hyperscale - Premium Series Compute \| vCore` | $0.18266 |
| Hyperscale, Premium-series **memory optimized** | `... Premium Series Memory Optimized Compute \| vCore` | $0.255724 |
| Hyperscale, DC-series | `... Hyperscale - Compute DC-Series \| vCore` | $0.365 |
| **Hyperscale, serverless, Gen5** | `SQL Database SingleDB Hyperscale - Serverless - Compute Gen5 \| 1 vCore` | **$0.585** |
| Hyperscale, serverless, read replica | `SQL Database SingleDB Hyperscale - Serverless - Read Repl - Compute Gen5 \| vCore` | $0.378 |
| Hyperscale, serverless (alt meter) | `SQL Database Hyperscale - Serverless - Compute Gen5 \| 1 vCore` | $0.378 |

**Storage ($/GB/month):**

| Meter | $/GB/month |
| --- | --- |
| **General Purpose data stored** (`SQL Database Single/Elastic Pool General Purpose - Storage \| General Purpose`) | **$0.115** |
| General Purpose, zone-redundant data stored | $0.23 |
| **Business Critical data stored** | **$0.25** |
| **Hyperscale data stored** (`SQL Database SingleDB Hyperscale - Storage \| Hyperscale \| Hyperscale Data Stored`) | **$0.10** |
| Hyperscale data stored (older meter: `SQL Database Hyperscale - Storage \| Hyperscale`) | $0.25 — see note |
| PITR backup storage, LRS / ZRS | $0.10 |
| PITR backup storage, RA-GRS / RA-GZRS | $0.20 |
| LTR backup storage, LRS / ZRS | $0.025 |
| LTR backup storage, RA-GRS / RA-GZRS | $0.05 |
| Hyperscale backup — LRS / ZRS / RA-GRS / RA-GZRS | $0.08 / $0.10 / $0.20 / $0.20 |
| IO rate operations (GP, BC, Hyperscale) | $0.20 per 1M operations |

**Two Hyperscale storage meters exist in the API** — `SQL Database SingleDB Hyperscale - Storage | Hyperscale` ($0.10/GB/mo) and a legacy `SQL Database Hyperscale - Storage | Hyperscale` ($0.25/GB/mo). The `SingleDB` meter matches current single-database Hyperscale. Confirm which meter your subscription bills against before modeling; do not assume.

**Free-tier meters** exist at $0.00 for GP serverless compute (1-80 vCore), GP data stored, and PITR backup storage — these back the [Azure SQL Database free offer](https://learn.microsoft.com/en-us/azure/azure-sql/database/free-offer) (*"up to ten free General Purpose databases, each with 100,000 vCore seconds of compute, every month"*).

Pricing page for cross-reference: [Azure SQL Database pricing](https://azure.microsoft.com/en-us/pricing/details/azure-sql-database/single/).

### D.4 Azure Blob Storage / ADLS Gen2 — West US 3

Query: `serviceName eq 'Storage' and armRegionName eq 'westus3' and productName eq 'General Block Blob v2 Hierarchical Namespace'` (this is the ADLS Gen2 / hierarchical-namespace product).

| Tier / redundancy | $/GB/month | Tier minimum (GB) |
| --- | --- | --- |
| **Hot LRS** | **$0.018** | 0 (first 50 TB) |
| Hot LRS | $0.0173 | 51,200 (next 450 TB) |
| Hot LRS | $0.0166 | 512,000 (over 500 TB) |
| Hot ZRS | $0.0236 / $0.022656 / $0.021712 | 0 / 51,200 / 512,000 |
| Hot GRS | $0.037 / $0.0353 / $0.0339 | 0 / 51,200 / 512,000 |
| Hot RA-GRS | $0.046 / $0.0442 / $0.0423 | 0 / 51,200 / 512,000 |
| Hot GZRS | $0.0429 / $0.041163 / $0.039425 | 0 / 51,200 / 512,000 |
| Hot RA-GZRS | $0.0532 / $0.051237 / $0.049109 | 0 / 51,200 / 512,000 |
| **Cool LRS** | **$0.01** | 0 |
| Cool ZRS | $0.013 | 0 |
| Cool GRS | $0.02 | 0 |
| Cool RA-GRS | $0.025 | 0 |
| Cool GZRS / RA-GZRS | $0.022 | 0 |
| **Cold LRS** | **$0.0036** | 0 |
| Cold ZRS | $0.004 | 0 |
| Cold GRS / GZRS | $0.0072 / $0.0076 | 0 |
| Cold RA-GRS / RA-GZRS | $0.009 / $0.0095 | 0 |
| **Archive LRS** | **$0.00099** | 0 |
| Archive GRS / RA-GRS | $0.00299 | 0 |

Storage-only; transaction, data-retrieval, and early-deletion charges are separate meters not listed here. Pricing page for cross-reference: [Azure Blob Storage pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/).

### D.5 Microsoft Fabric — West US 3

Query: `serviceName eq 'Microsoft Fabric' and armRegionName eq 'westus3'`

**Fabric capacity is metered per capacity unit-hour, not per SKU.** The single pay-as-you-go consumption rate returned is:

| Meter | Price | Unit |
| --- | --- | --- |
| `Fabric Capacity \| <workload> Capacity Usage \| ... CU` | **$0.18** | 1 Hour (per CU) |
| `Fabric Capacity \| Capacity Overage Capacity Usage \| CU` | **$0.54** | 1 Hour (per CU) |

$0.18/CU/hour is returned identically for every workload meter (Data Warehouse, Data Warehouse (Accelerated), Compute Pool/Spark, Data Movement, Data Movement - Incremental copy, Data Orchestration, Eventhouse, Eventstream, Power BI, all OneLake read/write/iterative/other operation meters, SQL database in Microsoft Fabric, Cosmos Database in Microsoft Fabric, Copilot and AI, API for GraphQL, user data functions, dbt job, Apache Airflow job, SSIS in Fabric, VNet Data Gateway, and the rest). The $0.54 overage rate is exactly 3x base, matching the documented statement that capacity overage *"costs three times the normal capacity rate"* ([Throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling)).

**Derived SKU rates** (SKU CU counts from [Fabric licenses](https://learn.microsoft.com/en-us/fabric/enterprise/licenses) x $0.18/CU/hour — **derived, not a directly published SKU meter**):

| SKU | CUs | Derived $/hour (West US 3, PAYG) | Derived $/month at 730 h |
| --- | --- | --- | --- |
| **F2** | 2 | **$0.36** | ~$262.80 |
| **F4** | 4 | **$0.72** | ~$525.60 |
| **F8** | 8 | **$1.44** | ~$1,051.20 |
| F16 | 16 | $2.88 | ~$2,102.40 |
| F32 | 32 | $5.76 | ~$4,204.80 |
| **F64** | 64 | **$11.52** | ~$8,409.60 |

Billing is per second with a one-minute minimum, and capacities can be paused ([Fabric licenses](https://learn.microsoft.com/en-us/fabric/enterprise/licenses); [Pause and resume](https://learn.microsoft.com/en-us/fabric/enterprise/pause-resume)).

**Reservation meters** returned for West US 3: `Fabric Capacity Reservation | Fabric Capacity | Fabric Capacity CU` at **$938.00** and **$2,814.00** per "1 Hour" — the unit label on these reservation rows is not self-explanatory and they should **not** be used in a cost model without confirmation. Flagged in *Unverified*.

**OneLake storage ($/GB/month), West US 3:**

| Meter | $/GB/month |
| --- | --- |
| **OneLake Storage Hot** | **$0.023** |
| **Storage Mirroring** (mirroring replica storage beyond the free allowance) | **$0.023** |
| **Storage Mirroring Free** | **$0.00** |
| OneLake Storage Cool | $0.0125 |
| OneLake Storage Cold | $0.004 |
| OneLake Cache | $0.266 |
| OneLake BCDR Storage Hot / Cool / Cold | $0.0416 / $0.028125 / $0.0076 |
| SQL Storage (Fabric SQL database) | $0.22115 |
| Cosmos DB Storage (Cosmos DB in Fabric) | $0.22115 |
| Cosmos DB Backup Storage | $0.197886 |
| Cosmos DB Data Restore | $0.184616 |
| SQL Backup Storage | $0.088462 |

The **`Storage Mirroring Free` = $0.00** meter is the billing realization of the documented free allowance: *"a free terabyte of mirroring storage for every capacity unit (CU) you purchase"* ([Cost of mirroring](https://learn.microsoft.com/en-us/fabric/mirroring/overview)). On an **F2 that is 2 TB free; F4 → 4 TB; F8 → 8 TB; F64 → 64 TB.** Overflow bills at the `Storage Mirroring` rate of $0.023/GB/month.

Pricing page for cross-reference: [Microsoft Fabric pricing](https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/).

---

## Differences from POC prompt assumptions

### (a) "Cosmos max item size is a hard constraint requiring decomposition of 1–5 MB orders"

**Confirmed as a hard constraint — and it is stricter than item size alone.** 2 MB is the documented maximum item size and also the maximum **request** size; the maximum **response** size is 4 MB ([Service quotas](https://learn.microsoft.com/en-us/azure/cosmos-db/concepts-limits)). There is no increase path for the API for NoSQL (the 16 MB figure applies only to the API for MongoDB). So:

- **Orders above 2 MB cannot be stored as a single Cosmos item, period.** Decomposition, compression, or externalization to Blob is required.
- Orders in the 2–4 MB range are also beyond the request limit, not just the item limit — a "store it compressed and decompress client-side" strategy must keep the *serialized* item under 2 MB.
- Microsoft's own documented recommendation for this shape is externalization, not decomposition: *"don't store binary content or large chunks of text that you don't need to query on. A best practice is to put this kind of data in Azure Blob Storage and store a reference (or link) to the blob in the item"* ([Optimize request cost](https://learn.microsoft.com/en-us/azure/cosmos-db/optimize-cost-reads-writes)). The prompt's decomposition assumption is one valid option; blob-offload is the documented one and should be evaluated alongside it.
- **A cost consequence the prompt doesn't state:** decomposing one logical order into N items turns a single ~1 RU-class point read into N point reads or one cross-item query, and queries are documented as strictly more expensive than point reads. A 1,000-item fan-out costs on the order of 1,000 RU ([Optimize request cost](https://learn.microsoft.com/en-us/azure/cosmos-db/optimize-cost-reads-writes)). Model the read amplification, not just the write path.
- **`azure-cosmos` 4.17.0 adds `enable_compact_utf8_item_writes`** specifically *"to reduce request sizes for item write operations"* ([PyPI](https://pypi.org/project/azure-cosmos/)) — worth testing before assuming a payload exceeds 2 MB.
- **No documented RU figure exists above 100 KB**, so the RU cost of a ~2 MB point read is not published. Measure it.

### (b) "Hierarchical partition keys support /customerId, /orderId, /id as 3 levels"

**Structurally correct, but three documented facts change the design:**

1. **Three levels is the hard maximum** — *"The overall depth can't exceed three levels."* So `/customerId, /orderId, /id` uses the entire budget; no fourth level is available later ([Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys)).
2. **Using `/id` as the third level means every point read needs all three values — which is fine — but it makes `/customerId + /orderId` prefix reads *targeted cross-partition* queries, not single-partition point reads.** The routing table is explicit: only the full three-value filter routes to a single logical **and** physical partition. If the POC's decomposition puts an order's fragments under `/customerId, /orderId` and reassembles by querying that prefix, that read is a targeted cross-partition query whose RU cost scales with the number of fragments and physical partitions touched — **not** a ~1 RU point read. And the values must appear in the `WHERE` clause: *"Supplying values only via `PartitionKeyBuilder` doesn't by itself guarantee efficient routing."*
3. **`/customerId` as level 1 must have high cardinality.** *"Having low cardinality at the first level ... limits all of your write operations at the time of ingestion to just one physical partition until it reaches 50 GB"*, and splits *"can take between 4-6 hours to complete."* A B2B order system with a few hundred customers will hot-partition on ingest. The docs say that if the first level lacks high cardinality and you're hitting 20 GB today, *"we suggest using a synthetic partition key instead of a hierarchical partition key."*

Also not in the prompt: **HPK cannot be added or changed after container creation** — migration means create-new-container plus container copy job or change feed plus repointing the app. The docs contain a **self-contradiction about Python SDK support** (the limitations bullet says Python isn't supported while the supported-SDK table lists Python >= 4.6.0 and the page ships Python samples). Validate Python HPK empirically early in the POC. Finally, **include each HPK level explicitly in the indexing policy** or prefix queries will full-scan ([Indexing policies](https://learn.microsoft.com/en-us/azure/cosmos-db/index-policy)).

### (c) "Azure SQL nvarchar(max) is the only JSON option"

**Outdated.** A **native `json` data type is GA in Azure SQL Database** (announced 2025-05-19), on the SQL Server 2025 or Always-up-to-date update policy, with a documented 2 GB ceiling and documented advantages over `nvarchar(max)`: pre-parsed reads, compression-optimized storage, and targeted writes ([JSON data type](https://learn.microsoft.com/en-us/sql/t-sql/data-types/json-data-type?view=azuresqldb-current); [GA announcement](https://devblogs.microsoft.com/azure-sql/announcing-the-general-availability-ga-of-json-data-type-json-aggregates/)). `JSON_OBJECTAGG` and `JSON_ARRAYAGG` are also GA. Existing `varchar(max)` columns can be `ALTER TABLE`'d to `json`.

**But three caveats cut hard against adopting `json` for this specific POC:**

1. **A table containing a `json` column cannot be mirrored to Fabric at all** — *"Currently, a table can't be mirrored if it has the **json** or **vector** data type"*, and you can't `ALTER` a column to `json` while the table is mirrored ([Azure SQL mirroring limitations](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database-limitations)). If Fabric mirroring is in the architecture, `json` and mirroring are mutually exclusive on the same table today.
2. **`OPENJSON` doesn't accept the `json` type in Azure SQL Database** — *"Explicitly convert to nvarchar(max) first."* The `.modify()` method for in-place updates is SQL Server 2025-only preview.
3. **Clients don't see the type.** `sp_describe_first_result_set` reports `varchar(max)`/`nvarchar(max)` depending on TDS version — pyodbc will see a string.

Additionally, **there is no native JSON index in Azure SQL Database**: `CREATE JSON INDEX` Applies-to is *"SQL Server 2025 (17.x)"* only and is itself in preview there. The supported indexing path in Azure SQL Database remains **computed column plus standard nonclustered index** (1,700-byte key limit, collation-aware), plus full-text search ([Index JSON data](https://learn.microsoft.com/en-us/sql/relational-databases/json/index-json-data?view=azuresqldb-current); [CREATE JSON INDEX](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-json-index-transact-sql?view=sql-server-ver17)). `JSON_CONTAINS` is likewise SQL Server 2025-only.

**Net:** the prompt is wrong that `nvarchar(max)` is the only option, but for a Fabric-mirrored design `nvarchar(max)` may still be the right choice — and it hits its own wall (see (d)).

### (d) "Fabric mirroring is available for both SQL and Cosmos"

**True — both are available with no preview banner — but both have limits that break the 1–5 MB JSON scenario:**

| | Azure SQL Database mirroring | Azure Cosmos DB mirroring |
| --- | --- | --- |
| Status | GA (no preview banner) | GA (no preview banner), **API for NoSQL only** |
| Payload ceiling in OneLake | **LOB columns > 1 MB are truncated to 1 MB** | 2 MB (`varchar(max)`) for tables created after 2025-11-18; **8,000 chars** for older tables until recreated |
| `json` type source column | **Table cannot be mirrored at all** | N/A |
| Latency | *"as fast as every 15 seconds"* (transaction-log based) | *"a few minutes"*, longer for initial snapshot / heavy update volume |
| Prerequisite | SAMI or UAMI on the logical server (UAMI preview); `ALTER ANY EXTERNAL MIRROR` | **7-day or 30-day continuous backup** (irreversible once enabled); no multi-write-region accounts |
| Auth for the connection | Managed identity of the SQL server | **Read-write account keys or Entra RBAC only — managed identities and read-only keys NOT supported** |
| Blocked by | CDC enabled, Synapse Link, delayed durability, already mirrored elsewhere | Analytical store previously disabled on a container; custom partitioning; TTL soft-deletes |
| Cost | Free replication compute; free storage at 1 TB per CU | Same, plus continuous-backup charges on the Cosmos side |

**The critical finding for this POC: neither mirroring path delivers a full 1–5 MB order document to OneLake.**

- **Azure SQL path:** a 1–5 MB order in `nvarchar(max)` is **silently truncated to 1 MB** in OneLake. Switching to the native `json` type doesn't fix it — it makes the table unmirrorable entirely. There is no documented configuration that raises the 1 MB LOB truncation.
- **Cosmos path:** caps at 2 MB, which equals the Cosmos item limit — so a *whole, un-decomposed* order that fits in Cosmos also fits in the mirror. But if the order was decomposed to fit under 2 MB (per assumption (a)), reassembly in Fabric requires joining fragments in T-SQL. And **nested JSON arrives as a JSON *string*, not a flattened schema** — you expand it with `OPENJSON` / `CROSS APPLY` / `OUTER APPLY`, or Power Query `ToJson`.
- **Compounding trap:** shortcutting a mirrored item into a lakehouse **re-imposes an 8 KB truncation** — *"Data truncation to 8 KB still applies on the tables in SQL analytics endpoint of the lakehouse, including shortcuts to a mirrored item"* ([SQL analytics endpoint limitations](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint)). Query the mirrored item's **own** SQL analytics endpoint.
- **Direct Lake trap:** Direct Lake string columns cap at **32,764 Unicode characters** ([Direct Lake overview](https://learn.microsoft.com/en-us/fabric/fundamentals/direct-lake-overview)). No multi-MB JSON blob can be surfaced in a Direct Lake semantic model at all.

**Also differing from the prompt's implicit assumptions:**

- **Mirroring is free, but not free of capacity dependency.** Replication compute doesn't consume CU, and storage is free at 1 TB per CU (F2 → 2 TB). But a paused or deleted capacity stops replication, and all *querying* is charged at normal capacity rates.
- **Automating mirrored-database creation cannot use a service principal.** `MirroredDatabase` shows Service-principal support = No in the Fabric item API matrix, and requires a definition on create ([Item management](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview)). Every other item the POC needs (Lakehouse, Warehouse, Notebook, DataPipeline, CopyJob) does support service principals.
- **Any DDL change on a mirrored Azure SQL table triggers a full reseed** of that table; stopping and restarting mirroring reseeds everything.
- **Fabric capacity throttling has a hard-rejection stage**, and warehouse/SQL-endpoint operations are classified as *background* (24-hour smoothing) — which delays the onset of throttling but means the rejection stage, when reached, rejects *all* requests.
- **Copy job is not a workaround for nested JSON:** *"Hierarchical sources/destinations are not supported, which means there is no system-defined data type conversion between source and destination interim types"* ([Copy job](https://learn.microsoft.com/en-us/fabric/data-factory/what-is-copy-job)).

---

## Unverified / could not confirm

1. **Cosmos DB point-read RU cost above 100 KB.** The documented table stops at 100 KB → 10 RU. No published figure for 500 KB, 1 MB, or 2 MB items. The RU for a ~2 MB point read must be measured empirically via the `x-ms-request-charge` response header.

2. **Cosmos DB hierarchical partition keys — Python SDK support.** The [HPK page](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys) contradicts itself: the supported-SDK table lists Python >= 4.6.0 and the page contains Python code samples, while the Limitations section says *"Support for other SDKs, including Python, isn't available currently."* Not resolvable from documentation — test with `azure-cosmos` 4.17.0.

3. **JSON INDEX availability in Azure SQL Database.** The reference page's Applies-to is SQL Server 2025 only. A Microsoft PM stated in the GA blog comments that rollout to Azure SQL Database / MI with versionless policy is *"in the process of being rolled out"*, with a separate announcement promised. No such announcement is reflected in the reference docs as of 2026-09-13. Whether it can be created on a given Azure SQL Database today is **unconfirmed** — attempt `CREATE JSON INDEX` on the target server to find out.

4. **`JSON_CONTAINS` availability in Azure SQL Database.** Not in the [JSON functions list](https://learn.microsoft.com/en-us/sql/t-sql/functions/json-functions-transact-sql?view=azuresqldb-current); documented only for SQL Server 2025. Blog comment says rollout to Azure is planned. Unconfirmed.

5. **Fabric SQL analytics endpoint concurrency limits.** No documented maximum concurrent queries, concurrent sessions, query timeout, or result-set size limit was found. [Limitations of Fabric Data Warehouse](https://learn.microsoft.com/en-us/fabric/data-warehouse/limitations) is a short page containing only T-SQL surface-area and cross-region notes plus links to feature-specific limitation pages; `/fabric/data-warehouse/limits` returns **404**. The only capacity-side control documented is CU throttling. Anyone sizing a concurrency budget must benchmark.

6. **Fabric SQL analytics endpoint per-query latency SLO.** None published. The endpoint doc claims *"high-performance, low-latency SQL queries"* but gives no numeric target. Only the *sync lag* is quantified (<1 minute typical on the legacy sync; "within seconds" with the preview new sync).

7. **Fabric Data Warehouse row-size / max-column / result-set limits.** Not found on a limits page; the linked [Data types in Fabric Data Warehouse](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types) and [Tables limitations](https://learn.microsoft.com/en-us/fabric/data-warehouse/tables#limitations) pages were not fetched in this pass.

8. **Fabric SQL database GA vs preview.** Ambiguous in live docs — no preview banner on the overview or limitations pages, but the `json` data type page, the Data Warehouse limitations page, and the Copy job connector table all still label it preview. Confirm with the Fabric release-notes / what's-new page before depending on the status.

9. **Mirrored Database REST API — exact start/stop mirroring and mirroring-status endpoint paths.** The [Items operation group](https://learn.microsoft.com/en-us/rest/api/fabric/mirroreddatabase/items) lists only the seven CRUD/definition operations by name. Start/stop replication and table-status operations are referenced from the [Mirrored database REST API](https://learn.microsoft.com/en-us/fabric/mirroring/mirrored-database-rest-api) article; their exact paths were not individually fetched. The seven CRUD paths in §C.7 follow the standard Fabric item-API shape and are given with that caveat.

10. **Copy job support for Azure Cosmos DB as a source/destination.** The [Copy job overview](https://learn.microsoft.com/en-us/fabric/data-factory/what-is-copy-job) tables do not list Cosmos DB. The authoritative list is [Copy job sources and destinations](https://learn.microsoft.com/en-us/fabric/data-factory/copy-job-connectors), which was not fetched. Cosmos DB *is* a Fabric Data Factory pipeline connector, but Copy-job-specific support (and incremental/CDC support for it) is unconfirmed.

11. **pyodbc-specific connection pooling behavior and Linux/unixODBC pooling configuration.** The Microsoft driver-aware-pooling doc is scoped to Windows. No Microsoft page was found documenting pyodbc's `pooling` module flag or unixODBC `odbcinst.ini` `Pooling`/`CPTimeout` settings for Azure SQL. Guidance in §B.8 is limited to what the ODBC driver page actually states. Pool sizing for a 50-RPS Python service is not documented by Microsoft.

12. **Azure SQL Database Hyperscale storage price — which meter applies.** The Retail Prices API returns two Hyperscale data-stored meters for West US 3: `SQL Database SingleDB Hyperscale - Storage | Hyperscale` at **$0.10/GB/month** and `SQL Database Hyperscale - Storage | Hyperscale` at **$0.25/GB/month**. Which one a given subscription bills against was not confirmed.

13. **Fabric capacity reservation pricing.** The API returns `Fabric Capacity Reservation | Fabric Capacity | Fabric Capacity CU` at $938.00 and $2,814.00 with `unitOfMeasure: "1 Hour"` and `type: "Reservation"`. The unit semantics are not self-explanatory (these are almost certainly not per-hour amounts) and were not confirmed. **Do not use these numbers.** Only the pay-as-you-go $0.18/CU/hour figure is stated with confidence.

14. **Published per-SKU Fabric prices (F2/F4/F8/F64 as discrete $/hour meters).** No per-SKU meters exist in the Retail Prices API — Fabric bills per CU-hour. The per-SKU figures in §D.5 are **derived** (CUs x $0.18) and should be labeled as such in any cost model. The pricing page that would confirm them renders prices via JavaScript and returned only `$-` placeholders.

15. **Regional pricing for regions other than West US 3 and East US.** Only these two were queried. Fabric pricing is explicitly regional (*"Pricing is regional"*). Azure SQL and Storage figures in §D.3 / §D.4 are West US 3 only.

16. **Azure SQL Database MB/s throughput per SLO.** Microsoft publishes max data IOPS and an IO-size range (8 KB to 64 KB), not MB/s. The ~160 MB/s figure cited in §B.5 for `GP_Gen5_8` is arithmetic inference, not a documented number.

---

# PART 2 — Full-Document Extension

**Verified on 2026-09-15.** Accessed date for every link in Part 2: **2026-09-15**.

This part covers the four-scenario full-document extension (SQL Full JSON, Cosmos
DB for MongoDB, and the two existing decomposed models). Same rule as Part 1:
nothing from memory, contradictions called out, unretrievable facts listed as
unverified rather than guessed.

## M. Azure Cosmos DB for MongoDB — the 16 MB document capability

### M.1 What the capability does, and the two hard constraints

> "16-MB document support raises the size limit for documents from 2 MB to 16 MB.
> This limit applies only to collections created after enabling the feature.
> After you enable this feature for a database account, it can't be disabled."

> "We recommend that you enable Server Side Retry and avoid using wildcard indexes
> to ensure that requests in larger documents succeed. Raising your database or
> collection request units might also help performance."

Source: [7.0 supported features and syntax — Azure Cosmos DB for MongoDB](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/feature-support-70)
(page `ms.date` 2025-08-20, `updated_at` 2026-04-27).

Two consequences that drive the test plan:

1. The capability must be enabled **before** the test collection is created. A
   collection created earlier keeps the 2 MB limit. The provisioning order is
   therefore not a style choice.
2. Enablement is **one-way per account**. It cannot be removed.

Enablement path: the **Features** tab in the portal, or programmatically by adding
the `EnableMongo16MBDocumentSupport` capability
([how to configure capabilities](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/how-to-configure-capabilities)).

### M.2 SECURITY — 16 MB documents and customer-managed keys are mutually exclusive

**This is the most consequential finding in Part 2 and it is not in an appendix.**

`EnableMongo16MBDocumentSupport` and CMK encryption **cannot coexist on the same
account**. Attempting to enable CMK on an account that has the capability returns
an error stating that *"EnableMongo16MBDocumentSupport and CMK encryption are not
supported together"*. Because the capability also cannot be removed (M.1), an
account created for 16 MB documents can **never** be brought under
customer-managed-key encryption. The only remedy is to create a new account
without the capability and migrate the data.

Sources: [Configure customer-managed keys — Azure Cosmos DB](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-setup-customer-managed-keys),
[Configure CMK on existing accounts](https://learn.microsoft.com/en-us/azure/cosmos-db/how-to-setup-customer-managed-keys-existing-accounts),
[CMK troubleshooting guide](https://learn.microsoft.com/en-us/azure/cosmos-db/cmk-troubleshooting-guide),
and the Microsoft Q&A thread [How to remove EnableMongo16MBDocumentSupport capability to enable CMK encryption](https://learn.microsoft.com/en-gb/answers/questions/5520221/title-how-to-remove-enablemongo16mbdocumentsupport).

For a title and escrow workload the order payload carries SSNs, bank and wire
instructions, and loan detail. Where CMK is a stated control, **Scenario B is
disqualified at the platform level regardless of how well it benchmarks.** That
is a design finding, not a performance one, and it is recorded here before any
measurement was taken.

### M.3 Indexing and query constraints relevant to a large nested document

From [feature-support-70](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/feature-support-70):

| Capability | Supported |
| --- | --- |
| Single field / compound / multikey index | Yes |
| `2dsphere` | Yes |
| **Text index** | **No** (use `$regex`) |
| **Hashed index** | **No** |
| **Sparse** index property | **No** |
| `Partial` index property | Only for unique indexes |
| **Case-insensitive** index property | **No** |
| TTL, Unique, Background | Yes |

Also documented on the same page and directly relevant here:

- Wildcard indexes are explicitly discouraged with 16 MB documents (M.1). Broadly
  indexing a deeply nested order payload is therefore contraindicated by the
  vendor, not merely by our own measurement.
- Multi-document transactions work **only within a single non-sharded
  collection**, never across collections or shards, with a fixed **5 second**
  timeout.
- Retryable writes require the shard key in the filter for updates and deletes on
  sharded collections (`ShardKeyNotFound(61)` otherwise), and do not support bulk
  *unordered* writes. Capability: `EnableMongoRetryableWrites`.
- Write concerns specified by client code are **ignored**; all writes are quorum.
- Users and roles are not supported; access is via Azure RBAC or account keys.
- Documents are BSON. Accounts on 4.0+ use an improved internal encoding, and
  documents written before an upgrade do not benefit until rewritten.

### M.4 Product direction — Microsoft now routes away from this API in both directions

The feature-support page for MongoDB 7.0 opens with two redirections:

> "Are you looking to migrate an existing MongoDB application or use MongoDB Query
> Language (MQL) features? Consider Azure DocumentDB."

> "Are you looking for a database solution for **high-scale** scenarios with a
> 99.999% availability service level agreement (SLA), instant autoscale, and
> automatic failover across multiple regions? Consider Azure Cosmos DB for NoSQL."

Azure DocumentDB is the service **formerly named Azure Cosmos DB for MongoDB
(vCore)**, renamed to align with the Linux Foundation open-source DocumentDB
project, and is now generally available. Free online migration from MongoDB (RU)
to Azure DocumentDB is GA.

Sources: [feature-support-70](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/feature-support-70),
[Azure DocumentDB is now generally available](https://devblogs.microsoft.com/cosmosdb/azure-documentdb-is-now-generally-available/),
[Azure DocumentDB FAQ](https://learn.microsoft.com/en-us/azure/documentdb/faq),
[Migrate to Azure DocumentDB](https://learn.microsoft.com/en-us/azure/cosmos-db/mongodb/how-to-migrate-documentdb),
[Migration from MongoDB (RU) to Azure DocumentDB is now GA](https://devblogs.microsoft.com/cosmosdb/mongoru-to-documentdb/).

**Difference from an assumption in the prompt.** The prompt asks whether Cosmos DB
for MongoDB "remains appropriate for this POC". Per M.1, the 16 MB capability is a
**MongoDB (RU)** account capability, so the scenario the prompt specifies can only
be built on the API that Microsoft's own documentation now steers new workloads
away from. Both facts are recorded; the scenario is still built and measured, as
the prompt directs.

### M.5 Fabric analytics — no native mirroring for the MongoDB API

Fabric mirroring from Azure Cosmos DB supports **Azure Cosmos DB for NoSQL
accounts only**. There is no native mirrored-database source for the API for
MongoDB, so Scenario B's analytics path must use a different mechanism (Open
Mirroring with a custom extractor, or a Data Factory / pipeline copy).

Sources: [Mirrored databases from Azure Cosmos DB](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db),
[Limits and quotas in mirrored databases from Azure Cosmos DB](https://learn.microsoft.com/en-us/fabric/mirroring/azure-cosmos-db-limitations),
[Copy data from Azure Cosmos DB for MongoDB (Data Factory connector)](https://learn.microsoft.com/en-us/azure/data-factory/connector-azure-cosmos-db-mongodb-api).

Also documented for the NoSQL mirroring path: continuous backup is a
prerequisite, and replication itself does not consume RUs.

## N. Azure SQL Database — the native `json` type for Scenario A

### N.1 The type is real, GA on Azure SQL Database, and present on this server

> "The **json** data type stores JSON documents in a native binary format."

> is generally available for Azure SQL Database and Azure SQL Managed Instance with
> the **SQL Server 2025** or **Always-up-to-date** update policy.
> is in preview for SQL Server 2025 (17.x) and SQL database in Fabric.

Documented size limits:

| Field | Limitation |
| --- | --- |
| JSON data type size (binary) | **Up to 2 GB** |
| Number of unique keys | Up to 32K |
| Per key string size | 7,998 bytes |
| Per string value size | 536,870,911 bytes |
| Number of properties in one object | **Up to 65,535** |
| Number of elements in one array | **Up to 65,535** |
| Number of nested levels | **128** |

Source: [json data type — SQL Server / Azure SQL](https://learn.microsoft.com/en-us/sql/t-sql/data-types/json-data-type)
(page `ms.date` 2026-01-14, `updated_at` 2026-01-15).

Verified against the POC's own provisioned database on 2026-09-15:
`SERVERPROPERTY('EngineEdition')` = **5** (Azure SQL Database) and
`SELECT COUNT(*) FROM sys.types WHERE name='json'` = **1**. The type exists here.

At 1-5 MB an order is four orders of magnitude inside the 2 GB ceiling. The limits
that could actually bite are the **65,535 elements per array** and **65,535
properties per object** ceilings, which the >10 MB boundary profiles must be
checked against rather than assumed safe.

Other documented behaviours that affect the implementation:

- The `json` type **cannot be an index key column**; it may be an *included*
  column, and may appear in a filtered index's `WHERE` clause.
- `sp_describe_first_result_set` does not report the type correctly, so "many data
  access clients and drivers see a **varchar** or **nvarchar** data type" — TDS
  >= 7.4 sees `varchar(max)` with `Latin1_General_100_BIN2_UTF8`. Observed
  directly: pyodbc raised `ODBC SQL type -16 is not yet supported` on a probe that
  surfaced a native type to the driver, which is the same class of driver gap.
- `OPENJSON()` does not accept the `json` type on some platforms; cast to
  `nvarchar(max)` explicitly first.
- No implicit conversions. `CAST`/`CONVERT` to and from char/nchar/varchar/nvarchar
  only. A `varchar(max)` column can be altered **to** `json`, but a `json` column
  can never be altered back to a string type.

### N.2 The blocking constraint — a mirrored table may not contain a `json` column

> "A table can't be mirrored if it has the json or vector data type."

> "You can't ALTER a column to the vector or json data type when a table is
> mirrored."

Sources: [Limitations and behaviors for Fabric mirrored databases from Azure SQL Database](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-database-limitations),
[Limitations of Fabric mirrored databases from SQL Server](https://learn.microsoft.com/en-us/fabric/mirroring/sql-server-limitations),
[Limitations in mirrored databases from Azure SQL Managed Instance](https://learn.microsoft.com/en-us/fabric/mirroring/azure-sql-managed-instance-limitations).

This re-confirms, against current documentation, the constraint that shaped the
existing hybrid model: **the faster, purpose-built JSON type and Fabric mirroring
are mutually exclusive on the same table.** Scenario A is therefore built and
measured **both** ways to answer the prompt honestly:

| Variant | Column type | Mirrorable to Fabric | Purpose |
| --- | --- | --- | --- |
| `sql-full-json` | `nvarchar(max)` + `ISJSON` check | Yes | the deployable design |
| `sql-full-json-native` | `json` | **No** | the performance ceiling the constraint costs |

Measuring only one of these would either overstate what is deployable or
understate what the type can do. The prompt's instruction to "measure actual
behavior rather than assuming one representation is faster" is why both exist.
