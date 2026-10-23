[CmdletBinding()]
param(
    [switch]$ValidateOnly,
    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$registry = 'https://nuget.pkg.github.com/AlfaHackathon/index.json'
$credentialName = 'NuGetPackageSourceCredentials_github'
$services = @('ContextService', 'InsightService')

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

foreach ($service in $services) {
    $config = Join-Path $workspace "$service/NuGet.config"
    [xml]$settings = Get-Content -LiteralPath $config -Raw
    $sources = @($settings.configuration.packageSources.add)
    if ($sources.Count -ne 2 -or
        !($sources | Where-Object { $_.key -eq 'github' -and $_.value -eq $registry }) -or
        !($sources | Where-Object { $_.key -eq 'nuget.org' -and $_.value -eq 'https://api.nuget.org/v3/index.json' }) -or
        $null -eq $settings.configuration.packageSources.clear -or
        $null -ne $settings.configuration.packageSourceCredentials) {
        throw "$service requires the credential-free, exclusive GitHub/NuGet.org configuration"
    }
    $orgMapping = $settings.configuration.packageSourceMapping.packageSource | Where-Object key -eq 'github'
    $publicMapping = $settings.configuration.packageSourceMapping.packageSource | Where-Object key -eq 'nuget.org'
    if (@($orgMapping.package).Count -ne 1 -or $orgMapping.package.pattern -ne 'AlfaHackaton.*' -or
        @($publicMapping.package).Count -ne 1 -or $publicMapping.package.pattern -ne '*') {
        throw "$service must map AlfaHackaton.* exclusively to github"
    }
    foreach ($project in @("Api/$service.Api.csproj", "Tests/$service.Tests.csproj")) {
        if (!(Test-Path -LiteralPath (Join-Path $workspace "$service/$project") -PathType Leaf)) {
            throw "Missing $service/$project"
        }
    }
}
if ($ValidateOnly) {
    Write-Output 'Both repository configs and project paths validated; no credentials read, no restore performed.'
    return
}
if ($RunTests -and (!$env:CONTEXT_TEST_POSTGRES -or !$env:INSIGHT_TEST_POSTGRES)) {
    throw 'Set CONTEXT_TEST_POSTGRES and INSIGHT_TEST_POSTGRES to an isolated synthetic PostgreSQL instance before -RunTests.'
}

$cacheRoot = Join-Path $workspace ('.scratch/published-package-restore/' + [guid]::NewGuid().ToString('N'))
$securePat = $null
$plainPat = $null
$credential = [Environment]::GetEnvironmentVariable($credentialName, 'Process')
try {
    if (!$credential) {
        $login = Read-Host 'GitHub login for package read access'
        if ([string]::IsNullOrWhiteSpace($login) -or $login.Contains(';')) { throw 'A valid GitHub login is required' }
        $securePat = Read-Host 'GitHub PAT classic with read:packages (entered locally, never saved)' -AsSecureString
        if ($securePat.Length -eq 0) { throw 'A package-read credential is required' }
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePat)
        try { $plainPat = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        $credential = "Username=$login;Password=$plainPat;ValidAuthenticationTypes=Basic"
    }
    [Environment]::SetEnvironmentVariable($credentialName, $credential, 'Process')
    $plainPat = $null
    $credential = $null
    New-Item -ItemType Directory -Path $cacheRoot | Out-Null
    $proof = @()
    foreach ($service in $services) {
        $packageCache = Join-Path $cacheRoot $service
        $config = Join-Path $workspace "$service/NuGet.config"
        $apiProject = Join-Path $workspace "$service/Api/$service.Api.csproj"
        $testProject = Join-Path $workspace "$service/Tests/$service.Tests.csproj"
        foreach ($project in @($apiProject, $testProject)) {
            Invoke-Checked dotnet @('restore', $project, '--configfile', $config, '--packages', $packageCache,
                '--no-http-cache', '--force', '-p:NuGetAudit=true', '-p:NuGetAuditMode=all')
        }
        $packageIds = @('alfahackaton.auth', 'alfahackaton.context', 'alfahackaton.result',
            'alfahackaton.controllers', 'alfahackaton.dappercontext', 'alfahackaton.httpclient')
        foreach ($packageId in $packageIds) {
            $packageDirectory = Join-Path $packageCache $packageId
            $metadataFiles = @(Get-ChildItem -LiteralPath $packageDirectory -Filter '.nupkg.metadata' -Recurse -File -Force)
            if ($metadataFiles.Count -eq 0) { throw "Missing published package provenance for $service/$packageId" }
            foreach ($file in $metadataFiles) {
                $metadata = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
                if ($metadata.source.TrimEnd('/') -ne $registry.TrimEnd('/')) {
                    throw "Unexpected package source for $service/$packageId; refusing to claim a registry restore"
                }
                $proof += [ordered]@{
                    service = $service
                    package = $packageId
                    version = $file.Directory.Name
                    source = $registry
                    metadata = $file.FullName
                }
            }
        }
        Invoke-Checked dotnet @('build', $apiProject, '-c', 'Release', '--no-restore', "-p:RestorePackagesPath=$packageCache")
        if ($RunTests) {
            Invoke-Checked dotnet @('test', $testProject, '-c', 'Release', '--no-restore', "-p:RestorePackagesPath=$packageCache")
        }
    }
    $proof | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $cacheRoot 'published-package-provenance.json') -Encoding utf8
    Write-Output "Published packages restored from GitHub and builds passed. Fresh caches and provenance: $cacheRoot"
    if ($RunTests) { Write-Output 'Both test suites passed using those fresh caches.' }
} finally {
    [Environment]::SetEnvironmentVariable($credentialName, $null, 'Process')
    $plainPat = $null
    $credential = $null
    if ($securePat) { $securePat.Dispose() }
}
