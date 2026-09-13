// =====================================================================
// POC: Azure SQL hybrid vs Azure Cosmos DB for large order JSON
// Subscription-scope deployment: resource group + all POC resources.
// =====================================================================
targetScope = 'subscription'

@description('Base name for all POC resources. Must include "poc".')
param baseName string = 'orderjsonpoc'

@description('Azure region for all resources.')
param location string = 'westus3'

@description('Resource group name.')
param resourceGroupName string = 'rg-order-json-poc-${location}'

@description('Entra object id of the principal that will administer SQL and read data.')
param adminPrincipalObjectId string

@description('Entra display name / UPN of the SQL admin principal.')
param adminPrincipalName string

@description('Azure SQL database SKU. GP_S_Gen5_2 = General Purpose serverless 2 vCore.')
param sqlSkuName string = 'GP_S_Gen5_2'

@description('Azure SQL max vCores for serverless autoscaling.')
param sqlMaxCapacity int = 4

@description('Azure SQL minimum vCores for serverless.')
param sqlMinCapacity string = '0.5'

@description('Cosmos DB container max throughput (autoscale RU/s).')
param cosmosMaxThroughput int = 4000

@description('Client IP allowed through the SQL and Cosmos firewalls. Empty = skip.')
param clientIpAddress string = ''

@description('Deploy the in-VNet API + load-generator VMs used for benchmarking.')
param deployBenchmarkVms bool = true

@description('SSH public key for the benchmark VMs.')
@secure()
param sshPublicKey string = ''

@description('Tags applied to every resource.')
param tags object = {
  purpose: 'POC'
  project: 'order-json-sql-vs-cosmos'
  createdBy: 'claude-code'
  deleteAfter: 'poc-complete'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module shared 'modules/shared.bicep' = {
  name: 'shared'
  scope: rg
  params: {
    baseName: baseName
    location: location
    tags: tags
    adminPrincipalObjectId: adminPrincipalObjectId
  }
}

module sql 'modules/sql.bicep' = {
  name: 'sql'
  scope: rg
  params: {
    baseName: baseName
    location: location
    tags: tags
    adminPrincipalObjectId: adminPrincipalObjectId
    adminPrincipalName: adminPrincipalName
    skuName: sqlSkuName
    maxCapacity: sqlMaxCapacity
    minCapacity: sqlMinCapacity
    clientIpAddress: clientIpAddress
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    baseName: baseName
    location: location
    tags: tags
    adminPrincipalObjectId: adminPrincipalObjectId
    maxThroughput: cosmosMaxThroughput
    clientIpAddress: clientIpAddress
  }
}

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    location: location
    tags: tags
    sqlServerName: sql.outputs.serverName
    cosmosAccountName: cosmos.outputs.accountName
    storageAccountName: shared.outputs.storageAccountName
    clientIpAddress: clientIpAddress
  }
}

module compute 'modules/compute.bicep' = if (deployBenchmarkVms) {
  name: 'compute'
  scope: rg
  params: {
    location: location
    tags: tags
    subnetId: network.outputs.computeSubnetId
    clientIpAddress: clientIpAddress
    sshPublicKey: sshPublicKey
  }
}

output resourceGroupName string = rg.name
output apiVmName string = deployBenchmarkVms ? compute.outputs.apiVmName : ''
output loadVmName string = deployBenchmarkVms ? compute.outputs.loadVmName : ''
output apiVmPublicIp string = deployBenchmarkVms ? compute.outputs.apiVmPublicIp : ''
output loadVmPublicIp string = deployBenchmarkVms ? compute.outputs.loadVmPublicIp : ''
output apiVmPrincipalId string = deployBenchmarkVms ? compute.outputs.apiVmPrincipalId : ''
output loadVmPrincipalId string = deployBenchmarkVms ? compute.outputs.loadVmPrincipalId : ''
output location string = location
output storageAccountName string = shared.outputs.storageAccountName
output archiveFilesystem string = shared.outputs.archiveFilesystem
output logAnalyticsWorkspaceId string = shared.outputs.logAnalyticsWorkspaceId
output appInsightsConnectionString string = shared.outputs.appInsightsConnectionString
output sqlServerFqdn string = sql.outputs.serverFqdn
output sqlDatabaseName string = sql.outputs.databaseName
output cosmosAccountName string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDatabaseName string = cosmos.outputs.databaseName
output cosmosContainerName string = cosmos.outputs.containerName
