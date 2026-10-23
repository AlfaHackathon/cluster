# Code quality follow-up — 2026-10-23

Scope: ContextService, InsightService, AgentService and shared verification tooling.
Reference implementations were inspected at CompanyService `f6c3413c1c6dc536b31fe2a24ad272b6c6c358cd`
and UserService `23b88c7cd14a1d4d0b8e07968533f896121c74f7`.

## Changes

- Replaced InsightService's ordinal JSON row reader, mutable query parameter dictionary
  and SQL result/count envelopes with typed Dapper results and anonymous parameters.
  Revision persistence has explicit identity, head advancement and dependency operations.
- Added the approved `ITransaction.FirstOrDefault<T>`, `ListOrEmpty<T>` and
  `CommandWithCount` methods to DapperContext 1.2.0. All use the existing connection
  and transaction. Existing methods retain their behavior. Third-party implementations
  of the interface must implement the three additions when upgrading.
- ContextService now reads material identities and evidence directly through the same
  transaction. Change rows use C# property names with explicit SQL aliases.
- Fixed disposal of the projection semaphore, made chunk-offset formatting explicitly
  culture-invariant, and fixed logging analyzer findings, unused imports and compact
  unbraced statements. Replaced
  the synthetic async HTTP status check and object-typed projection response.
- Agent clients consistently translate malformed upstream DTOs into
  `502 / UPSTREAM_CONTRACT`. Real HTTP fixtures cover twelve previously inconsistent
  client operations and the valid null job claim.
- Added `.editorconfig`, C# recommended analyzers with build errors, and
  `tools/Test-ServiceQuality.ps1`. Source package runs have immutable feeds/configs,
  commit-keyed source checkouts and one fresh shared cache, with per-service hash checks.

## Verified source packages and PostgreSQL behavior

- DapperContext source commit: `593de96d8340418cab21db902a6e99e5be0a56ec`, branch
  [codex/transaction-queries](https://github.com/AlfaHackathon/AlfaHackaton.DapperContext/tree/codex/transaction-queries).
  The branch is pushed; this is a source-built package, not a claimed GitHub Packages release.
- Source build: `.artifacts/org-packages/runs/20261023T191407Z-e68d916c/provenance.json`.
  All six clean public source pins and twelve per-service archive origin/SHA256 checks
  passed. Service API/test Release builds had zero warnings/errors; NuGet audit stayed enabled.
- ContextService: **27/27** PostgreSQL tests. InsightService: **54/54**. No skips.
- Package tests: **3/3**, covering typed/empty/nullable reads, read-your-writes,
  isolation from another connection, affected-row counts, commit and rollback.
- Recommended analyzer baseline failed with six ContextService diagnostics and five
  InsightService diagnostics. Service diagnostics are fixed rather than suppressed.
  Source libraries retain pre-existing upstream warnings, including the DapperContext
  migration-runner nullable warning; service dependency security overrides remain in place.

## Final gates

- `tools/Test-ServiceQuality.ps1 -NoRestore`: **PASS**, log `.artifacts/quality-gate-final.log`.
  C# whitespace, IDE0005/IDE0011, recommended analyzer Release builds and PostgreSQL
  suites passed; both builds had zero warnings/errors. Biome checked 130 files,
  dependency boundaries and TypeScript passed. AgentService **184/184** tests and
  built-application HTTP tests **4/4** passed.
- Published three-process proof: **PASS**,
  `research/org-adoption-v2/runs/20261023T192201313879Z/result.json` (local, not committed).
  All four stages passed: SQLite baseline, imported PostgreSQL restart, fresh PostgreSQL
  and PostgreSQL restart, with **158 recorded HTTP operations**. Source-built origins and installed archive hashes were verified
  against the explicit source manifest; the published-registry flag correctly remains false.
  Damaged-FK import rolled back, nonempty-target import was refused, source SQLite files
  remained unchanged and normal migration verification passed.

## Failure evidence and review limits

The added Agent regression tests initially failed in all twelve malformed-response cases;
the null-claim case passed. The focused corrected suite passed 20 tests across three files.
The C# refactor initially exposed a TEXT-to-Guid scalar mapping regression in two existing
recipient tests. Reading strings and explicitly parsing GUIDs preserved SQL cursor ordering;
all 54 tests passed afterward.

Initial environment failures were separate from application failures: the old temporary
PostgreSQL directory no longer existed, Roslyn exhausted host memory, a fresh package run
hit a full C: drive, and a subsequent public Git clone failed its TLS handshake. Local logs
under `.artifacts/quality-*` retain the outcomes. Verification was repeated after recovery.

Separate read-only standards and contract reviews found no unresolved issues after the
recipient fix. Those reviews are static evidence, not a substitute for the runtime suites.
Only isolated synthetic PostgreSQL databases and controlled reasoning fixtures are used;
no paid LLM, Kafka worker, production migration, credentials or historical data reports are
part of the committed evidence.
