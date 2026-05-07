#
# Run the SQLAlchemy compliance suite against a self-contained Firebird
# environment provisioned by PSFirebird.
#

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FirebirdVersion,

    [Parameter()]
    [string]$EnvironmentPath = (Join-Path ([System.IO.Path]::GetTempPath()) "sqlalchemy-firebird-tests/$FirebirdVersion"),

    [Parameter()]
    [string[]]$PytestArgs
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Module PSFirebird -ListAvailable)) {
    Write-Host "Installing PSFirebird module..."
    Install-Module PSFirebird -Scope CurrentUser -Force
}
Import-Module PSFirebird

# Provision a self-contained Firebird tree (no system PATH or registry side-effects).
Write-Host "Provisioning Firebird $FirebirdVersion at $EnvironmentPath ..."
$envObj = New-FirebirdEnvironment -Version $FirebirdVersion -Path $EnvironmentPath -Force

# Create a fresh test database inside the environment folder.
$databasePath = Join-Path $EnvironmentPath 'sqlalchemy-firebird-tests.fdb'
Write-Host "Creating database $databasePath ..."
$db = New-FirebirdDatabase -Database $databasePath -Environment $envObj -Force

$clientLibrary = $envObj.GetClientLibraryPath()

# Embedded URL — sysdba, no host/port, explicit client library.
$dbUri = "firebird+firebird://sysdba@/$($db.Path -replace '\\','/')?charset=UTF8&fb_client_library=$($clientLibrary -replace '\\','/')"

Write-Host "Running pytest against:"
Write-Host "  $dbUri"

& uv run pytest --dburi $dbUri @PytestArgs
exit $LASTEXITCODE
