[CmdletBinding()]
param(
    [switch]$RunTests,
    [string]$ArtifactsRoot = ''
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (!$ArtifactsRoot) { $ArtifactsRoot = Join-Path $workspace '.artifacts/org-packages' }
$ArtifactsRoot = [IO.Path]::GetFullPath($ArtifactsRoot)
$sourceRoot = Join-Path $ArtifactsRoot 'pinned-sources'
$runId = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$runRoot = Join-Path $ArtifactsRoot "runs/$runId"
$feed = Join-Path $runRoot 'feed'
$sourcePackages = Join-Path $runRoot 'packages'
$configuration = Join-Path $runRoot 'NuGet.source-built.config'
$services = @('ContextService', 'InsightService')

# These are actual public organization repositories and their unmodified project versions.
# Changing a pin is an explicit source/dependency update, not an automatic latest-version lookup.
$sources = @(
    @{ Name = 'AlfaHackaton.Context'; Version = '1.0.1'; Commit = '3c69e0888e854d039db56b9af712994a3bdff90d' },
    @{ Name = 'AlfaHackaton.Result'; Version = '1.0.0'; Commit = 'fc613a788ff2b6ea3bfeca5be6e560b2cb673a3f' },
    @{ Name = 'AlfaHackaton.Auth'; Version = '1.3.0'; Commit = 'f4eb6449c0d427f155bfdb85d205970c47dcb226' },
    @{ Name = 'AlfaHackaton.HttpClient'; Version = '1.1.1'; Commit = '8782062514b65f9b6cf4732ad78ae63966b19fcf' },
    @{ Name = 'AlfaHackaton.DapperContext'; Version = '1.2.0'; Commit = '593de96d8340418cab21db902a6e99e5be0a56ec' },
    @{ Name = 'AlfaHackaton.Controllers'; Version = '1.4.0'; Commit = 'bf536d0c99a29ae9d0af73c406452407083262c6' }
)

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

function Invoke-PublicGit([string[]]$Arguments) {
    # Public HTTPS access only: do not query credential managers or use configured auth headers.
    Invoke-Checked git (@('-c', 'credential.helper=', '-c', 'core.askPass=', '-c', 'http.extraHeader=') + $Arguments)
}

function Assert-UnmodifiedSource([string]$Checkout, [string]$ExpectedCommit) {
    $head = (& git -C $Checkout rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -ne $ExpectedCommit) {
        throw "Unexpected commit at $Checkout; refusing to overwrite an existing checkout"
    }
    $dirty = & git -C $Checkout status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0 -or $dirty) {
        throw "Modified source at $Checkout; preserve or move it before rerunning"
    }
}

Get-Command git, dotnet -ErrorAction Stop | Out-Null
foreach ($service in $services) {
    foreach ($project in @("Api/$service.Api.csproj", "Tests/$service.Tests.csproj")) {
        if (!(Test-Path -LiteralPath (Join-Path $workspace "$service/$project") -PathType Leaf)) {
            throw "Missing $service/$project"
        }
    }
}
if ($RunTests -and (!$env:CONTEXT_TEST_POSTGRES -or !$env:INSIGHT_TEST_POSTGRES)) {
    throw 'Set CONTEXT_TEST_POSTGRES and INSIGHT_TEST_POSTGRES to an isolated synthetic PostgreSQL instance before -RunTests.'
}

New-Item -ItemType Directory -Force $ArtifactsRoot, $sourceRoot | Out-Null
New-Item -ItemType Directory -Path $runRoot | Out-Null
New-Item -ItemType Directory -Path $feed | Out-Null
$feedXml = [System.Security.SecurityElement]::Escape($feed)
@"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="organization-source-built" value="$feedXml" />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="organization-source-built"><package pattern="AlfaHackaton.*" /></packageSource>
    <packageSource key="nuget.org"><package pattern="*" /></packageSource>
  </packageSourceMapping>
</configuration>
"@ | Set-Content -LiteralPath $configuration -Encoding utf8

$expectedArchives = @($sources | ForEach-Object { "$($_.Name).$($_.Version).nupkg" })
foreach ($existing in Get-ChildItem -LiteralPath $feed -Filter '*.nupkg' -File) {
    if ($existing.Name -notin $expectedArchives) {
        throw "Unexpected package $($existing.Name) in dedicated feed; refusing an ambiguous source-built restore"
    }
}

$priorPrompt = [Environment]::GetEnvironmentVariable('GIT_TERMINAL_PROMPT', 'Process')
$priorInteractive = [Environment]::GetEnvironmentVariable('GCM_INTERACTIVE', 'Process')
$priorAskpass = [Environment]::GetEnvironmentVariable('GIT_ASKPASS', 'Process')
$packageProvenance = @()
$consumedPackages = @()
try {
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'Never'
    [Environment]::SetEnvironmentVariable('GIT_ASKPASS', $null, 'Process')
    Write-Output 'Building real NuGet packages from pinned public organization source; no registry token is required.'
    foreach ($source in $sources) {
        $checkout = Join-Path $sourceRoot "$($source.Name)/$($source.Commit)"
        $url = "https://github.com/AlfaHackathon/$($source.Name).git"
        # Reuse a verified checkout from the older flat layout when its pin still matches.
        $legacyCheckout = Join-Path $ArtifactsRoot "sources/$($source.Name)"
        if (!(Test-Path -LiteralPath $checkout) -and (Test-Path -LiteralPath $legacyCheckout)) {
            $legacyHead = (& git -C $legacyCheckout rev-parse HEAD).Trim()
            if ($LASTEXITCODE -eq 0 -and $legacyHead -eq $source.Commit) {
                Assert-UnmodifiedSource $legacyCheckout $source.Commit
                $checkout = $legacyCheckout
            }
        }
        if (!(Test-Path -LiteralPath $checkout)) {
            Invoke-PublicGit @('clone', '--no-checkout', $url, $checkout)
            Invoke-PublicGit @('-C', $checkout, 'checkout', '--detach', $source.Commit)
        }
        $origin = (& git -C $checkout remote get-url origin).Trim()
        if ($LASTEXITCODE -ne 0 -or $origin -ne $url) { throw "Unexpected source remote at $checkout" }
        Assert-UnmodifiedSource $checkout $source.Commit
        $project = Join-Path $checkout "$($source.Name).csproj"
        [xml]$projectXml = Get-Content -LiteralPath $project -Raw
        if ($projectXml.Project.PropertyGroup.Version -ne $source.Version) {
            throw "Project version does not match the explicit pin at $project"
        }
        Invoke-Checked dotnet @('restore', $project, '--configfile', $configuration, '--packages', $sourcePackages,
            '--force', '--no-http-cache', '-p:NuGetAudit=true', '-p:NuGetAuditMode=all')
        Invoke-Checked dotnet @('pack', $project, '-c', 'Release', '--no-restore', '-o', $feed)
        Assert-UnmodifiedSource $checkout $source.Commit
        $archive = Join-Path $feed "$($source.Name).$($source.Version).nupkg"
        $packageProvenance += [ordered]@{
            package = $source.Name
            version = $source.Version
            repository = $url
            commit = $source.Commit
            sourceModified = $false
            artifact = $archive
            sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
            distribution = 'source-built from unchanged pinned public organization repository; not a downloaded registry artifact'
        }
    }

    foreach ($service in $services) {
        # Each run owns one fresh cache. Reusing it across the two services avoids
        # downloading the same dependency graph three times; hashes/origins are still checked per service.
        $packageCache = $sourcePackages
        $apiProject = Join-Path $workspace "$service/Api/$service.Api.csproj"
        $testProject = Join-Path $workspace "$service/Tests/$service.Tests.csproj"
        foreach ($project in @($apiProject, $testProject)) {
            Invoke-Checked dotnet @('restore', $project, '--configfile', $configuration, '--packages', $packageCache,
                '--force', '--no-http-cache', '-p:NuGetAudit=true', '-p:NuGetAuditMode=all')
        }
        foreach ($package in $packageProvenance) {
            $packageId = $package.package.ToLowerInvariant()
            $installed = Join-Path $packageCache "$packageId/$($package.version)"
            $metadata = Get-Content -LiteralPath (Join-Path $installed '.nupkg.metadata') -Raw | ConvertFrom-Json
            if ([IO.Path]::GetFullPath($metadata.source) -ne [IO.Path]::GetFullPath($feed)) {
                throw "Unexpected restore source for $service/$packageId"
            }
            $archive = Join-Path $installed "$packageId.$($package.version).nupkg"
            $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($hash -ne $package.sha256) { throw "Package content mismatch for $service/$packageId" }
            $consumedPackages += [ordered]@{ service = $service; package = $package.package; version = $package.version; sha256 = $hash; source = $feed }
        }
        Invoke-Checked dotnet @('build', $apiProject, '-c', 'Release', '--no-restore', "-p:RestorePackagesPath=$packageCache",
            '-m:1', '-nr:false', '-p:UseSharedCompilation=false')
        Invoke-Checked dotnet @('build', $testProject, '-c', 'Release', '--no-restore', "-p:RestorePackagesPath=$packageCache",
            '-m:1', '-nr:false', '-p:UseSharedCompilation=false')
        if ($RunTests) {
            Invoke-Checked dotnet @('test', $testProject, '-c', 'Release', '--no-restore', '--no-build', "-p:RestorePackagesPath=$packageCache")
        }
    }
    $provenance = [ordered]@{
        runId = $runId
        completedAt = [DateTime]::UtcNow.ToString('O')
        distribution = 'source-built public GitHub repositories, not downloaded GitHub Packages binaries'
        config = $configuration
        feed = $feed
        runRoot = $runRoot
        packages = $packageProvenance
        consumedPackages = $consumedPackages
        releaseBuildsPassed = $true
        testsExecuted = [bool]$RunTests
    }
    $manifest = Join-Path $runRoot 'provenance.json'
    $provenance | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest -Encoding utf8
    $provenance | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ArtifactsRoot 'latest.json') -Encoding utf8
    Write-Output "Source-built packages and Release builds verified. Provenance: $manifest"
    Write-Output "Generated explicit source-built config: $configuration"
    if ($RunTests) { Write-Output 'Both PostgreSQL test suites passed from the dedicated fresh caches.' }
} finally {
    [Environment]::SetEnvironmentVariable('GIT_TERMINAL_PROMPT', $priorPrompt, 'Process')
    [Environment]::SetEnvironmentVariable('GCM_INTERACTIVE', $priorInteractive, 'Process')
    [Environment]::SetEnvironmentVariable('GIT_ASKPASS', $priorAskpass, 'Process')
}
