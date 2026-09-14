// PATH B: Cosmos DB for NoSQL with a HIERARCHICAL partition key.
param baseName string
param location string
param tags object
param adminPrincipalObjectId string
param maxThroughput int
param clientIpAddress string

var accountName = toLower('cosmos-${baseName}-${uniqueString(resourceGroup().id)}')

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  identity: { type: 'SystemAssigned' }
  properties: {
    databaseAccountOfferType: 'Standard'
    // Session consistency: the documented default, and the right fit for a
    // read-your-writes operational API. Strong would raise read RU on
    // multi-region; we stay single-region for the POC.
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    // Entra data-plane RBAC only. No account keys used by the application.
    disableLocalAuth: false // kept enabled for Fabric mirroring compatibility; app uses MI
    publicNetworkAccess: 'Enabled'
    ipRules: empty(clientIpAddress) ? [] : [
      { ipAddressOrRange: clientIpAddress }
    ]
    enableAnalyticalStorage: false
    capabilities: []
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: { tier: 'Continuous7Days' }
    }
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: 'orderdb'
  properties: {
    resource: { id: 'orderdb' }
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: db
  name: 'orders'
  properties: {
    resource: {
      id: 'orders'
      // ---------------------------------------------------------------
      // HIERARCHICAL PARTITION KEY - see docs/COSMOS_DESIGN.md
      //   /customerId -> tenant isolation, even spread across customers
      //   /orderId    -> every item of one order version co-located, so a
      //                  full-order read is one single-partition query
      // We deliberately stop at 2 levels: adding /id as a third level would
      // make the full-order read a cross-subpartition fan-out for no benefit.
      // ---------------------------------------------------------------
      partitionKey: {
        paths: [ '/customerId', '/orderId' ]
        kind: 'MultiHash'
        version: 2
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        // Index ONLY the routing/search fields. The payload tree under /data
        // is pass-through: indexing it would inflate write RU and storage for
        // queries we never issue.
        includedPaths: [
          // Routing keys. These MUST be indexed explicitly: being partition-key
          // paths does not make them usable as filter predicates once '/*' is
          // excluded. Omitting /orderId cost a full scan on every read - see
          // docs/COSMOS_DESIGN.md 'Indexing policy'.
          { path: '/customerId/?' }
          { path: '/orderId/?' }
          { path: '/orderVersion/?' }
          { path: '/blockType/?' }
          { path: '/blockSubType/?' }
          { path: '/sequence/?' }
          { path: '/docType/?' }
          { path: '/search/*' }
          { path: '/isCurrent/?' }
          { path: '/modifiedUtc/?' }
        ]
        excludedPaths: [
          { path: '/data/*' }
          { path: '/*' }
          { path: '/"_etag"/?' }
        ]
        compositeIndexes: [
          [
            { path: '/search/status', order: 'ascending' }
            { path: '/search/maxLoanAmount', order: 'descending' }
          ]
          [
            { path: '/search/state', order: 'ascending' }
            { path: '/search/status', order: 'ascending' }
          ]
        ]
      }
    }
    options: {
      autoscaleSettings: { maxThroughput: maxThroughput }
    }
  }
}

// Cosmos DB Built-in Data Contributor - data-plane RBAC for the developer.
resource dataRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, adminPrincipalObjectId, 'data-contributor')
  properties: {
    roleDefinitionId: resourceId('Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions', account.name, '00000000-0000-0000-0000-000000000002')
    principalId: adminPrincipalObjectId
    scope: account.id
  }
}

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = db.name
output containerName string = container.name
