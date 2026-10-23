# Organization package builds

Use the actual organization NuGet packages through normal `PackageReference` dependencies.
There are two explicit ways to obtain them; neither changes their source or API.

## Build the public GitHub source without a token

From the workspace root, with Git and the .NET 10 SDK installed:

```powershell
.\cluster\tools\Build-OrganizationPackages.ps1
```

This is the token-free path. It clones six public `AlfaHackathon/AlfaHackaton.*` repositories
with credential helpers disabled, checks their exact pinned commits and clean source trees,
and packs their actual project files in Release. It then restores and builds ContextService,
InsightService and both test projects using those real packages.

Output is under `.artifacts/org-packages`: `feed/` contains the source-built `.nupkg` files,
`NuGet.source-built.config` exclusively maps `AlfaHackaton.*` to that feed, and `runs/<id>/`
contains dedicated fresh package caches and provenance. `latest.json` is written only after
successful verification. Provenance records public repository URLs, exact commits, source
integrity checks, package SHA256 and the hashes actually consumed by each service.

The result is honestly labelled **source-built**, not a downloaded registry artifact.
The script never contacts the GitHub Packages registry, reads a token, edits organization
source files, silently switches feeds, or changes service `NuGet.config` files. Ordinary
third-party dependencies come from NuGet.org. Reruns reuse only verified unchanged source
checkouts and rebuild the six selected packages; every run gets fresh caches. Unexpected
source changes or packages stop the script without deleting user files.

To run the real PostgreSQL suites as well, first provide an isolated synthetic PostgreSQL
instance with permission to create disposable databases. For the workspace's separate
local test instance:

```powershell
$env:CONTEXT_TEST_POSTGRES = 'Host=127.0.0.1;Port=55441;Database=postgres;Username=postgres;Pooling=false'
$env:INSIGHT_TEST_POSTGRES = $env:CONTEXT_TEST_POSTGRES
.\cluster\tools\Build-OrganizationPackages.ps1 -RunTests
```

The script does not start PostgreSQL. Tests create their own synthetic databases. It never
starts the Kafka/LLM worker or uses the paid model.

Use the generated source map explicitly for further verification; the normal repository
configs continue to select GitHub Packages:

```powershell
$build = Get-Content .artifacts/org-packages/latest.json -Raw | ConvertFrom-Json
dotnet restore ContextService/Api/ContextService.Api.csproj --configfile $build.config --packages "$($build.runRoot)/packages/ContextService" --no-http-cache --force
```

Use `--no-restore` and the same `RestorePackagesPath` for subsequent build/test/publish
commands to preserve the verified package origin. Source package builds can report upstream
warnings; service projects explicitly pin patched Pkcs/Xml 10.0.13, and audit remains enabled.

## Download the published GitHub Packages binaries

If package-read credentials are available, use the normal per-service `NuGet.config` files:

```powershell
.\cluster\tools\Restore-OrganizationPackages.ps1
```

It asks for GitHub login and a package-read PAT locally with `Read-Host -AsSecureString`.
The token is kept only in the process environment, inherited by restore, and cleared in
`finally`. It is never written to config, provenance, shell command arguments or chat.
The script also accepts the process environment variable
`NuGetPackageSourceCredentials_github` when configured outside the script.

Both services restore to new caches with `--no-http-cache --force`; installed metadata must
identify the actual GitHub registry. Release builds use those caches without implicit restore.
`-RunTests` additionally runs the suites and requires the PostgreSQL variables above.
`-ValidateOnly` checks repository config and project paths without credentials or network.

This registry path needs package-read authorization. A failed download never automatically
falls back to the source-built feed; the token-free script above is an explicit choice.
