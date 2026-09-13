// PATH A: Azure SQL logical server + database, Entra-only auth.
param baseName string
param location string
param tags object
param adminPrincipalObjectId string
param adminPrincipalName string
param skuName string
param maxCapacity int
param minCapacity string
param clientIpAddress string

@description('Set false when tenant policy forces publicNetworkAccess=Disabled; firewall rules cannot then be created.')
param allowPublicFirewallRules bool = false

var serverName = toLower('sql-${baseName}-${uniqueString(resourceGroup().id)}')

resource server 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: serverName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    // Entra-only authentication: no SQL logins, no passwords anywhere.
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      login: adminPrincipalName
      sid: adminPrincipalObjectId
      principalType: 'User'
      tenantId: subscription().tenantId
    }
  }
}

resource db 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: server
  name: 'OrderDb'
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: startsWith(skuName, 'HS') ? 'Hyperscale' : 'GeneralPurpose'
    family: 'Gen5'
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 107374182400 // 100 GB
    autoPauseDelay: 120        // serverless: pause after 2h idle to keep POC cost down
    minCapacity: json(minCapacity)
    zoneRedundant: false
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
  }
}

// Allow Azure services (Fabric mirroring, ADF) through the firewall.
resource allowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (allowPublicFirewallRules) {
  parent: server
  name: 'AllowAllWindowsAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource allowClient 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (allowPublicFirewallRules && !empty(clientIpAddress)) {
  parent: server
  name: 'AllowPocClient'
  properties: { startIpAddress: clientIpAddress, endIpAddress: clientIpAddress }
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output serverName string = server.name
output databaseName string = db.name
output maxCapacityApplied int = maxCapacity
