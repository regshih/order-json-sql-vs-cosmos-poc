<#
.SYNOPSIS
    Tear down everything this POC created. Nothing else is touched.
.DESCRIPTION
    Deletes ONLY the POC resource group, the Fabric workspace and its items, and
    (unless -KeepFabricCapacity) the Fabric capacity. Refuses to act on a
    resource group that is not tagged project=order-json-sql-vs-cosmos.
.EXAMPLE
    .\scripts\destroy.ps1
    .\scripts\destroy.ps1 -Yes
    .\scripts\destroy.ps1 -Yes -KeepFabricCapacity
#>
[CmdletBinding()]
param(
    [string]$Location        = 'westus3',
    [string]$ResourceGroup   = '',
    [string]$FabricCapacity  = 'fabordjsonpoc915d',
    [string]$FabricWorkspace = 'ws-order-json-poc',
    [switch]$KeepFabricCapacity,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
if (-not $ResourceGroup) { $ResourceGroup = "rg-order-json-poc-$Location" }

Write-Host "=== POC teardown ===" -ForegroundColor Yellow
Write-Host "  resource group  : $ResourceGroup"
Write-Host "  Fabric workspace: $FabricWorkspace"
Write-Host "  Fabric capacity : $FabricCapacity (keep=$KeepFabricCapacity)"

$rgExists = $false
az group show -n $ResourceGroup -o none 2>$null
if ($?) { $rgExists = $true }

if ($rgExists) {
    # Safety guard: never delete a group that is not tagged as this POC.
    $projectTag = az group show -n $ResourceGroup --query "tags.project" -o tsv 2>$null
    if ($projectTag -ne 'order-json-sql-vs-cosmos') {
        Write-Host "REFUSING: resource group '$ResourceGroup' is not tagged" -ForegroundColor Red
        Write-Host "  project=order-json-sql-vs-cosmos (found: '$projectTag')."
        Write-Host "  This guard exists so the script can never delete unrelated resources."
        exit 1
    }
    Write-Host "  resource group tag verified: project=$projectTag"
    Write-Host "`n  resources that will be deleted:"
    az resource list -g $ResourceGroup --query "[].{name:name,type:type}" -o tsv |
        ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "  resource group $ResourceGroup does not exist (already deleted?)"
}

if (-not $Yes) {
    $reply = Read-Host "`nDelete ALL of the above? Type 'destroy' to confirm"
    if ($reply -ne 'destroy') { Write-Host "aborted"; exit 1 }
}

Write-Host "`n-- Fabric workspace --" -ForegroundColor Cyan
$py = @"
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
from fabric.provision_fabric import Fabric, get_token
fab = Fabric(get_token())
ws = fab.find_workspace('$FabricWorkspace')
if not ws:
    print('  workspace $FabricWorkspace not found')
else:
    for item in fab.list_items(ws['id']):
        s, b, _ = fab.call('DELETE', f"/workspaces/{ws['id']}/items/{item['id']}")
        print(f"  deleted {item['type']:<20} {item['displayName']:<24} -> {s}")
    s, b, _ = fab.call('DELETE', f"/workspaces/{ws['id']}")
    print(f'  deleted workspace $FabricWorkspace -> {s}')
"@
try { python -c $py } catch { Write-Host "  (workspace cleanup skipped: $_)" }

Write-Host "`n-- Fabric capacity --" -ForegroundColor Cyan
if ($KeepFabricCapacity) {
    az fabric capacity suspend -g $ResourceGroup --capacity-name $FabricCapacity -o none 2>$null
    if ($?) { Write-Host "  suspended $FabricCapacity (billing stopped, data retained)" }
    else    { Write-Host "  could not suspend $FabricCapacity" }
} else {
    Write-Host "  (deleted with the resource group below)"
}

Write-Host "`n-- resource group --" -ForegroundColor Cyan
if ($rgExists) {
    az group delete -n $ResourceGroup --yes --no-wait
    Write-Host "  deletion started (async). Track with:"
    Write-Host "    az group show -n $ResourceGroup"
}

Write-Host "`n=== teardown initiated ===" -ForegroundColor Green
Write-Host "NOT deleted (delete by hand if you want them gone):"
Write-Host "  - the GitHub repository"
Write-Host "  - local data/ and results/ directories"
