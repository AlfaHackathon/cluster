[CmdletBinding()]
param(
    [switch]$NoRestore,
    [switch]$SkipTests,
    [string]$NuGetConfig = ''
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

if (!$SkipTests -and (!$env:CONTEXT_TEST_POSTGRES -or !$env:INSIGHT_TEST_POSTGRES)) {
    throw 'Set CONTEXT_TEST_POSTGRES and INSIGHT_TEST_POSTGRES to an isolated PostgreSQL instance, or use -SkipTests for static checks only.'
}

# Keep Roslyn workloads sequential and bounded on developer machines.
$previousProcessorCount = [Environment]::GetEnvironmentVariable('DOTNET_PROCESSOR_COUNT', 'Process')
$previousHeapLimit = [Environment]::GetEnvironmentVariable('DOTNET_GCHeapHardLimit', 'Process')
$previousServerGc = [Environment]::GetEnvironmentVariable('DOTNET_gcServer', 'Process')
try {
    $env:DOTNET_PROCESSOR_COUNT = '2'
    if (!$previousHeapLimit) { $env:DOTNET_GCHeapHardLimit = '0x10000000' }
    $env:DOTNET_gcServer = '0'
    foreach ($service in @('ContextService', 'InsightService')) {
        $solution = Join-Path $workspace "$service/$service.slnx"
        if (!$NoRestore) {
            $arguments = @('restore', $solution, '-p:NuGetAudit=true', '-p:NuGetAuditMode=all')
            if ($NuGetConfig) { $arguments += @('--configfile', [IO.Path]::GetFullPath($NuGetConfig)) }
            Invoke-Checked dotnet $arguments
        }
        Invoke-Checked dotnet @('format', 'whitespace', $solution, '--no-restore', '--verify-no-changes')
        # IDE0005 needs XML documentation generation to run during build; check it explicitly here.
        Invoke-Checked dotnet @('format', 'style', $solution, '--no-restore', '--verify-no-changes',
            '--diagnostics', 'IDE0005', 'IDE0011', '--severity', 'info')
        Invoke-Checked dotnet @('build', $solution, '--no-restore', '-c', 'Release',
            '-m:1', '-nr:false', '-p:UseSharedCompilation=false', '--warnaserror')
        if (!$SkipTests) {
            Invoke-Checked dotnet @('test', $solution, '--no-restore', '--no-build', '-c', 'Release')
        }
    }

    Push-Location (Join-Path $workspace 'AgentService')
    try {
        Invoke-Checked npm.cmd @('run', 'check')
        Invoke-Checked npm.cmd @('run', 'typecheck')
        if (!$SkipTests) {
            Invoke-Checked npm.cmd @('test')
            Invoke-Checked npm.cmd @('run', 'test:built')
        }
    }
    finally { Pop-Location }
}
finally {
    [Environment]::SetEnvironmentVariable('DOTNET_PROCESSOR_COUNT', $previousProcessorCount, 'Process')
    [Environment]::SetEnvironmentVariable('DOTNET_GCHeapHardLimit', $previousHeapLimit, 'Process')
    [Environment]::SetEnvironmentVariable('DOTNET_gcServer', $previousServerGc, 'Process')
}
