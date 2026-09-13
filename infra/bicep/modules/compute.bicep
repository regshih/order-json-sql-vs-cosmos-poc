// Benchmark compute: API host + load generator, inside the POC VNet.
//
// Two VMs rather than one so that Workload C (multi-megabyte full-order
// responses at 50 RPS ~= 250 MB/s) actually crosses a network boundary and the
// load generator's CPU never competes with the API's. Both VMs use managed
// identity for SQL / Cosmos / Storage data-plane access - no secrets.
param location string
param tags object
param subnetId string
param clientIpAddress string
@secure()
param sshPublicKey string
param apiVmSize string = 'Standard_D8s_v5'
param loadVmSize string = 'Standard_D4s_v5'
param adminUsername string = 'pocadmin'

var vms = [
  { name: 'vm-orderjsonpoc-api',  size: apiVmSize,  role: 'api' }
  { name: 'vm-orderjsonpoc-load', size: loadVmSize, role: 'load' }
]

resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = [for vm in vms: {
  name: 'pip-${vm.name}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}]

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = [for (vm, i) in vms: {
  name: 'nic-${vm.name}'
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: subnetId }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip[i].id }
        }
      }
    ]
  }
}]

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = [for (v, i) in vms: {
  name: v.name
  location: location
  tags: union(tags, { role: v.role })
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: v.size }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'Premium_LRS' }
        diskSizeGB: 128
      }
    }
    osProfile: {
      computerName: v.name
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            { path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: sshPublicKey }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [ { id: nic[i].id } ]
    }
  }
}]

output apiVmName string = vms[0].name
output loadVmName string = vms[1].name
output apiVmPublicIp string = pip[0].properties.ipAddress
output loadVmPublicIp string = pip[1].properties.ipAddress
output apiVmPrincipalId string = vm[0].identity.principalId
output loadVmPrincipalId string = vm[1].identity.principalId
output adminUsername string = adminUsername
