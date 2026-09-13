// Microsoft Fabric capacity for the POC analytics layer.
//
// Smallest practical SKU (F2) per the brief. Fabric capacity is a flat cost
// independent of the operational database choice, so it is neutral in the
// SQL-vs-Cosmos comparison - but it is real money, so it is created small and
// the destroy script removes it.
param location string
param tags object
param capacityName string
@description('Entra UPNs/object ids that administer the capacity.')
param adminMembers array
@allowed([ 'F2', 'F4', 'F8', 'F16', 'F32', 'F64' ])
param skuName string = 'F2'

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: capacityName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: adminMembers
    }
  }
}

output capacityName string = capacity.name
output capacityId string = capacity.id
output skuName string = skuName
