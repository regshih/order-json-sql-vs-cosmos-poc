<#
.SYNOPSIS
    Provision the complete POC on Windows: Azure resources, SQL schema, Fabric.
.DESCRIPTION
    PowerShell equivalent of scripts/deploy.sh. Every step is idempotent.
.EXAMPLE
    .\scripts\deploy.ps1
    .\scripts\deploy.ps1 -Location eastus2 -SqlSku GP_Gen5_4 -CosmosMaxRu 10000
#>
[CmdletBinding()]
param(
    [string]$Location        = 'westus3',
    [string]$BaseName        = 'orderjsonpoc',
    [string]$ResourceGroup   = '',
    [string]$SqlSku          = 'GP_S_Gen5_2',
    [int]   $SqlMaxCapacity  = 4,
    [string]$SqlMinCapacity  = '0.5',
    [int]   $CosmosMaxRu     = 4000,
    [string]$FabricCapacity  = 'fabordjsonpoc915d',
    [string]$FabricSku       = 'F2',
    [string]$FabricWorkspace = 'ws-order-json-poc',
    [bool]  $DeployVms       = $true,
    [string]$SshKeyPath      = "$HOME\.ssh\orderjsonpoc",
    [switch]$SkipFabric
)

$ErrorActionPreference = 'Stop'
if (-not $ResourceGroup) { $ResourceGroup = "rg-order-json-poc-$Location" }

Write-Host "=== POC deployment ===" -ForegroundColor Cyan
Write-Host "  location        : $Location"
Write-Host "  resource group  : $ResourceGroup"
Write-Host "  SQL SKU         : $SqlSku"
Write-Host "  Cosmos max RU/s : $CosmosMaxRu"
Write-Host "  Fabric capacity : $FabricCapacity ($FabricSku)"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw "az CLI not found" }
az account show -o none; if (-not $?) { throw "run 'az login' first" }

$oid = az ad signed-in-user show --query id -o tsv
$upn = az ad signed-in-user show --query userPrincipalName -o tsv
$myip = (Invoke-RestMethod -Uri 'https://ifconfig.me/ip' -TimeoutSec 20).ToString().Trim()
Write-Host "  admin principal : $upn"
Write-Host "  client IP       : $myip"

$sshPub = ''
if ($DeployVms) {
    if (-not (Test-Path "$SshKeyPath.pub")) {
        Write-Host "-- generating SSH key $SshKeyPath"
        ssh-keygen -t rsa -b 4096 -f $SshKeyPath -N '""' -q
    }
    $sshPub = Get-Content "$SshKeyPath.pub" -Raw
}

Write-Host "`n-- [1/4] Azure infrastructure (Bicep) --" -ForegroundColor Cyan
$deployName = "$BaseName-" + (Get-Date -Format 'yyyyMMddHHmmss')
az deployment sub create `
    --name $deployName --location $Location `
    --template-file infra/bicep/main.bicep `
    --parameters baseName=$BaseName location=$Location `
                 resourceGroupName=$ResourceGroup `
                 sqlSkuName=$SqlSku sqlMaxCapacity=$SqlMaxCapacity `
                 sqlMinCapacity=$SqlMinCapacity `
                 cosmosMaxThroughput=$CosmosMaxRu `
                 adminPrincipalObjectId=$oid adminPrincipalName=$upn `
                 clientIpAddress=$myip `
                 deployBenchmarkVms=$DeployVms sshPublicKey=$sshPub `
    --query "properties.outputs" -o json | Out-File -Encoding utf8 artifacts/_deploy_raw.json

python -c @'
import json, pathlib
raw = json.load(open("artifacts/_deploy_raw.json"))
out = {k: v["value"] for k, v in raw.items()}
pathlib.Path("artifacts").mkdir(exist_ok=True)
json.dump(out, open("artifacts/deployment-outputs.json", "w"), indent=2)
for k in sorted(out):
    if "ConnectionString" not in k:
        print(f"  {k} = {out[k]}")
'@
Remove-Item artifacts/_deploy_raw.json -ErrorAction SilentlyContinue

$outputs   = Get-Content artifacts/deployment-outputs.json -Raw | ConvertFrom-Json
$sqlFqdn   = $outputs.sqlServerFqdn
$cosmosAcc = $outputs.cosmosAccountName
$storage   = $outputs.storageAccountName
$sub       = az account show --query id -o tsv

Write-Host "`n-- [2/4] data-plane RBAC for the VM identities --" -ForegroundColor Cyan
foreach ($mi in @($outputs.apiVmPrincipalId, $outputs.loadVmPrincipalId)) {
    if (-not $mi) { continue }
    az cosmosdb sql role assignment create -g $ResourceGroup -a $cosmosAcc `
        --role-definition-id '00000000-0000-0000-0000-000000000002' --principal-id $mi `
        --scope "/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.DocumentDB/databaseAccounts/$cosmosAcc" `
        -o none 2>$null
    az role assignment create --assignee-object-id $mi --assignee-principal-type ServicePrincipal `
        --role "Storage Blob Data Contributor" `
        --scope "/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/$storage" `
        -o none 2>$null
}
Write-Host "  done"

Write-Host "`n-- [3/4] VM bootstrap + SQL schema --" -ForegroundColor Cyan
if ($DeployVms) {
    $bootstrap = Get-Content scripts/vm_bootstrap.sh -Raw
    foreach ($vm in @('vm-orderjsonpoc-api', 'vm-orderjsonpoc-load')) {
        Write-Host "  bootstrapping $vm ..."
        az vm run-command invoke -g $ResourceGroup -n $vm --command-id RunShellScript `
            --scripts $bootstrap --query "value[0].message" -o tsv | Select-String 'BOOTSTRAP_OK|error'
    }
    Write-Host "  creating SQL schema and granting the API identity ..."
    $token = az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv
    $script = "export HOME=/root; cd /opt/poc; export SQL_ACCESS_TOKEN='$token'; " +
              "./.venv/bin/python tools/sql_bootstrap.py --server $sqlFqdn --database OrderDb " +
              "--grant-identity vm-orderjsonpoc-api 2>&1 | tail -20"
    az vm run-command invoke -g $ResourceGroup -n vm-orderjsonpoc-api --command-id RunShellScript `
        --scripts $script --query "value[0].message" -o tsv | Select-String 'SQL_BOOTSTRAP_OK|rows=|FAILED|Error'
} else {
    Write-Host "  SKIPPED (DeployVms=false). Run tools/sql_bootstrap.py from inside the VNet."
}

Write-Host "`n-- [4/4] Fabric environment --" -ForegroundColor Cyan
if ($SkipFabric) {
    Write-Host "  SKIPPED (-SkipFabric)"
} else {
    az deployment group create -g $ResourceGroup --name "fabric-$(Get-Date -Format 'HHmmss')" `
        --template-file infra/bicep/modules/fabric.bicep `
        --parameters location=$Location capacityName=$FabricCapacity skuName=$FabricSku `
                     adminMembers="['$upn']" `
                     tags='{\"purpose\":\"POC\",\"project\":\"order-json-sql-vs-cosmos\",\"createdBy\":\"deploy.ps1\"}' `
        --query "properties.outputs.capacityName.value" -o tsv
    python fabric/provision_fabric.py --capacity $FabricCapacity --workspace $FabricWorkspace
    python fabric/setup_mirroring.py --open-mirroring
}

Write-Host "`n=== deployment complete ===" -ForegroundColor Green
Write-Host "Next: docs/RUNBOOK.md"
