// Private networking for the POC.
//
// Tenant policy (MCAPSGov) forces publicNetworkAccess=Disabled on Azure SQL
// and Cosmos DB accounts, so the operational data plane is reachable only
// over Private Link. This module supplies the VNet, private endpoints and
// private DNS zones, and is also what makes the benchmark credible: the API
// and load generator run inside this VNet rather than over the internet.
param location string
param tags object
param vnetName string = 'vnet-orderjsonpoc'
param sqlServerName string
param cosmosAccountName string
param storageAccountName string

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.60.0.0/16' ] }
    subnets: [
      {
        name: 'snet-compute'
        properties: {
          addressPrefix: '10.60.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.60.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-orderjsonpoc-compute'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowSshFromClient'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: clientIpAddress
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'AllowIntraVnet'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '10.60.0.0/16'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

param clientIpAddress string

var subnetPeId = '${vnet.id}/subnets/snet-private-endpoints'

// ---- Private DNS zones -------------------------------------------------
var zoneNames = [
  'privatelink${environment().suffixes.sqlServerHostname}'
  'privatelink.documents.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.dfs.${environment().suffixes.storage}'
]

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for z in zoneNames: {
  name: z
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (z, i) in zoneNames: {
  name: '${z}/link-${vnetName}'
  location: 'global'
  dependsOn: [ zones[i] ]
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}]

// ---- Private endpoints -------------------------------------------------
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' existing = { name: sqlServerName }
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = { name: cosmosAccountName }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: storageAccountName }

resource pesql 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-sql-orderjsonpoc'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPeId }
    privateLinkServiceConnections: [
      {
        name: 'sql'
        properties: {
          privateLinkServiceId: sqlServer.id
          groupIds: [ 'sqlServer' ]
        }
      }
    ]
  }
}

resource pesqldns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: pesql
  name: 'default'
  dependsOn: [ zones ]
  properties: {
    privateDnsZoneConfigs: [
      { name: 'sql', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', zoneNames[0]) } }
    ]
  }
}

resource pecosmos 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-cosmos-orderjsonpoc'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPeId }
    privateLinkServiceConnections: [
      {
        name: 'cosmos'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: [ 'Sql' ]
        }
      }
    ]
  }
}

resource pecosmosdns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: pecosmos
  name: 'default'
  dependsOn: [ zones ]
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cosmos', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', zoneNames[1]) } }
    ]
  }
}

resource peblob 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-blob-orderjsonpoc'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPeId }
    privateLinkServiceConnections: [
      { name: 'blob', properties: { privateLinkServiceId: storage.id, groupIds: [ 'blob' ] } }
    ]
  }
}

resource pedfs 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'pe-dfs-orderjsonpoc'
  location: location
  tags: tags
  properties: {
    subnet: { id: subnetPeId }
    privateLinkServiceConnections: [
      { name: 'dfs', properties: { privateLinkServiceId: storage.id, groupIds: [ 'dfs' ] } }
    ]
  }
}

resource pestoragedns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: peblob
  name: 'default'
  dependsOn: [ zones ]
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', zoneNames[2]) } }
    ]
  }
}

resource pedfsdns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: pedfs
  name: 'default'
  dependsOn: [ zones ]
  properties: {
    privateDnsZoneConfigs: [
      { name: 'dfs', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', zoneNames[3]) } }
    ]
  }
}

output vnetId string = vnet.id
output computeSubnetId string = '${vnet.id}/subnets/snet-compute'
