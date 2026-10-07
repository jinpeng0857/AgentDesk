<#
.SYNOPSIS
  Back up an isolated PostgreSQL _test database, restore to a new _test database,
  and compare actual schema plus canonicalized row data.

.PARAMETER DatabaseName
  Existing source test database. It is read only during this drill.

.PARAMETER RestoreDatabaseName
  Optional new restore target. Must end in _test and must not already exist.
#>
[CmdletBinding()]
param (
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5432,
    [string]$Username = "agentdesk",
    [string]$DatabaseName = "agentdesk_test",
    [string]$RestoreDatabaseName = "",
    [string]$ContainerName = "agentdesk-postgres-1",
    [string]$Password = "",
    [string]$BackupDir = "data/backups"
)

$ErrorActionPreference = "Stop"

function Invoke-CheckedNative {
    param (
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = & $Command @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $rendered = ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
        throw "$Command failed with exit code $exitCode.`n$rendered"
    }
    return ($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine
}

$requiredPostgresTools = @("pg_dump", "pg_restore", "createdb", "psql")
$missingPostgresTools = @(
    $requiredPostgresTools | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) }
)
$script:UseDockerPostgresTools = $missingPostgresTools.Count -gt 0
$script:DatabasePassword = $Password
if ([string]::IsNullOrWhiteSpace($script:DatabasePassword)) {
    $script:DatabasePassword = $env:PGPASSWORD
}
if ($script:UseDockerPostgresTools) {
    if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
        throw "PostgreSQL client tools are missing and Docker is unavailable; backup drill cannot run."
    }
    $runningContainers = Invoke-CheckedNative -Command "docker" -Arguments @(
        "ps", "--filter", "name=^/$ContainerName$", "--format", "{{.Names}}"
    )
    if ($runningContainers.Trim() -cne $ContainerName) {
        throw "Local PostgreSQL tools are missing and container '$ContainerName' is not running."
    }
    if ([string]::IsNullOrWhiteSpace($script:DatabasePassword)) {
        $script:DatabasePassword = "agentdesk"
    }
    Write-Host "Using real PostgreSQL utilities inside running container '$ContainerName'."
}

function Invoke-PgUtility {
    param (
        [Parameter(Mandatory = $true)][string]$Tool,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    if (-not $script:UseDockerPostgresTools) {
        $previousPassword = $env:PGPASSWORD
        if (-not [string]::IsNullOrWhiteSpace($script:DatabasePassword)) {
            $env:PGPASSWORD = $script:DatabasePassword
        }
        try {
            return Invoke-CheckedNative -Command $Tool -Arguments $Arguments
        }
        finally {
            $env:PGPASSWORD = $previousPassword
        }
    }
    $dockerArguments = @(
        "exec", "--env", "PGPASSWORD=$script:DatabasePassword", $ContainerName, $Tool
    ) + $Arguments
    return Invoke-CheckedNative -Command "docker" -Arguments $dockerArguments
}

function Invoke-Psql {
    param (
        [Parameter(Mandatory = $true)][string]$TargetDatabase,
        [Parameter(Mandatory = $true)][string]$Sql
    )

    $arguments = @(
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--set", "ON_ERROR_STOP=1",
        "--host", $HostName,
        "--port", [string]$Port,
        "--username", $Username,
        "--dbname", $TargetDatabase,
        "--command", $Sql
    )
    return Invoke-PgUtility -Tool "psql" -Arguments $arguments
}

function Get-SchemaSnapshot {
    param ([Parameter(Mandatory = $true)][string]$TargetDatabase)

    $columnSql = @"
SELECT COALESCE(
  jsonb_agg(jsonb_build_object(
    'table_name', table_name,
    'column_name', column_name,
    'ordinal_position', ordinal_position,
    'data_type', data_type,
    'udt_name', udt_name,
    'is_nullable', is_nullable,
    'column_default', column_default
  ) ORDER BY table_name, ordinal_position),
  '[]'::jsonb
)::text
FROM information_schema.columns
WHERE table_schema = 'public';
"@
    $constraintSql = @"
SELECT COALESCE(
  jsonb_agg(jsonb_build_object(
    'table_name', table_name,
    'constraint_name', constraint_name,
    'constraint_type', constraint_type
  ) ORDER BY table_name, constraint_name),
  '[]'::jsonb
)::text
FROM information_schema.table_constraints
WHERE table_schema = 'public'
  -- PostgreSQL-generated NOT NULL constraint names embed relation/column OIDs;
  -- nullability is already compared from information_schema.columns above.
  AND constraint_name !~ '^[0-9]+_[0-9]+_[0-9]+_not_null$';
"@
    $columns = Invoke-Psql -TargetDatabase $TargetDatabase -Sql $columnSql
    $constraints = Invoke-Psql -TargetDatabase $TargetDatabase -Sql $constraintSql
    return "$columns`n$constraints"
}

function Get-PublicTableNames {
    param ([Parameter(Mandatory = $true)][string]$TargetDatabase)

    $sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
    $output = Invoke-Psql -TargetDatabase $TargetDatabase -Sql $sql
    return @($output -split "`r?`n" | Where-Object { $_ -match '^[a-zA-Z_][a-zA-Z0-9_]*$' })
}

function Get-CanonicalRowSnapshot {
    param ([Parameter(Mandatory = $true)][string]$TargetDatabase)

    $snapshot = [System.Collections.Generic.List[string]]::new()
    foreach ($tableName in (Get-PublicTableNames -TargetDatabase $TargetDatabase)) {
        $sql = "SELECT COALESCE(jsonb_agg(to_jsonb(row_data) ORDER BY to_jsonb(row_data)::text), '[]'::jsonb)::text FROM public.`"$tableName`" AS row_data;"
        $rows = Invoke-Psql -TargetDatabase $TargetDatabase -Sql $sql
        $snapshot.Add("$tableName`n$rows")
    }
    return $snapshot -join "`n---TABLE---`n"
}

if ($DatabaseName -notmatch '^[a-zA-Z0-9_]+_test$') {
    throw "Source database must be an isolated name ending in _test; received '$DatabaseName'."
}

$timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd_HHmmssfff")
if ([string]::IsNullOrWhiteSpace($RestoreDatabaseName)) {
    $RestoreDatabaseName = "agentdesk_restore_drill_${timestamp}_test"
}
if ($RestoreDatabaseName -notmatch '^[a-zA-Z0-9_]+_test$') {
    throw "Restore database must be a simple identifier ending in _test."
}
if ($RestoreDatabaseName -eq $DatabaseName) {
    throw "Restore target must be different from the source database."
}

$restoreExists = Invoke-PgUtility -Tool "psql" -Arguments @(
    "--no-psqlrc", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
    "--host", $HostName, "--port", [string]$Port, "--username", $Username,
    "--dbname", "postgres", "--command",
    "SELECT 1 FROM pg_database WHERE datname = '$RestoreDatabaseName';"
)
if (-not [string]::IsNullOrWhiteSpace($restoreExists)) {
    throw "Restore target '$RestoreDatabaseName' already exists; refusing to overwrite it."
}

$backupRoot = [System.IO.Path]::GetFullPath($BackupDir)
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$backupFile = Join-Path $backupRoot "agentdesk-${DatabaseName}-${timestamp}.dump"
$dumpFileForServer = if ($script:UseDockerPostgresTools) {
    "/tmp/agentdesk-${DatabaseName}-${timestamp}.dump"
} else {
    $backupFile
}
$dumpArgs = @(
    "--format=custom", "--no-owner", "--no-privileges",
    "--host", $HostName, "--port", [string]$Port, "--username", $Username,
    "--dbname", $DatabaseName, "--file", $dumpFileForServer
)

Write-Host "Backing up source database '$DatabaseName' to '$backupFile'..."
Invoke-PgUtility -Tool "pg_dump" -Arguments $dumpArgs | Out-Null
if ($script:UseDockerPostgresTools) {
    Invoke-CheckedNative -Command "docker" -Arguments @(
        "cp", "${ContainerName}:$dumpFileForServer", $backupFile
    ) | Out-Null
    Invoke-CheckedNative -Command "docker" -Arguments @(
        "exec", $ContainerName, "rm", "-f", $dumpFileForServer
    ) | Out-Null
}
if (-not (Test-Path -LiteralPath $backupFile) -or (Get-Item -LiteralPath $backupFile).Length -le 0) {
    throw "pg_dump did not produce a non-empty custom-format backup."
}

$schemaBefore = Get-SchemaSnapshot -TargetDatabase $DatabaseName
$rowsBefore = Get-CanonicalRowSnapshot -TargetDatabase $DatabaseName

Write-Host "Creating isolated restore target '$RestoreDatabaseName'..."
Invoke-PgUtility -Tool "createdb" -Arguments @(
    "--host", $HostName, "--port", [string]$Port, "--username", $Username,
    "--owner", $Username, $RestoreDatabaseName
) | Out-Null

Write-Host "Restoring the actual custom-format backup..."
 $restoreDumpForServer = if ($script:UseDockerPostgresTools) {
    "/tmp/agentdesk-restore-${timestamp}.dump"
} else {
    $backupFile
}
if ($script:UseDockerPostgresTools) {
    Invoke-CheckedNative -Command "docker" -Arguments @(
        "cp", $backupFile, "${ContainerName}:$restoreDumpForServer"
    ) | Out-Null
}
Invoke-PgUtility -Tool "pg_restore" -Arguments @(
    "--exit-on-error", "--no-owner", "--no-privileges",
    "--host", $HostName, "--port", [string]$Port, "--username", $Username,
    "--dbname", $RestoreDatabaseName, $restoreDumpForServer
) | Out-Null
if ($script:UseDockerPostgresTools) {
    Invoke-CheckedNative -Command "docker" -Arguments @(
        "exec", $ContainerName, "rm", "-f", $restoreDumpForServer
    ) | Out-Null
}

$schemaAfter = Get-SchemaSnapshot -TargetDatabase $RestoreDatabaseName
$rowsAfter = Get-CanonicalRowSnapshot -TargetDatabase $RestoreDatabaseName
if ($schemaBefore -cne $schemaAfter) {
    throw "Restored schema differs from source database. Backup preserved at '$backupFile'."
}
if ($rowsBefore -cne $rowsAfter) {
    throw "Restored table data differs from source database. Backup preserved at '$backupFile'."
}

Write-Host "PASS: real PostgreSQL backup restored; schema and canonicalized rows match."
Write-Host "Backup: $backupFile"
Write-Host "Restore database retained for inspection: $RestoreDatabaseName"
