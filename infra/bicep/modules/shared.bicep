// Shared POC resources: ADLS Gen2 raw archive + observability.
param baseName string
param location string
param tags object
param adminPrincipalObjectId string

var storageName = toLower('st${baseName}${uniqueString(resourceGroup().id)}')

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: length(storageName) > 24 ? substring(storageName, 0, 24) : storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    // ADLS Gen2: hierarchical namespace gives us raw/{customer}/{order}/{version}/
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true // needed by some Fabric/ADF connectors; MI preferred in app
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource archiveFs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'raw'
  properties: { publicAccess: 'None' }
}

resource curatedFs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'curated'
  properties: { publicAccess: 'None' }
}

// Storage Blob Data Contributor for the developer principal (DefaultAzureCredential).
var blobContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, adminPrincipalObjectId, blobContributorRoleId)
  scope: storage
  properties: {
    principalId: adminPrincipalObjectId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRoleId)
    principalType: 'User'
  }
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${baseName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${baseName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

output storageAccountName string = storage.name
output archiveFilesystem string = archiveFs.name
output logAnalyticsWorkspaceId string = law.id
output appInsightsConnectionString string = appi.properties.ConnectionString
