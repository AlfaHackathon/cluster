# Verification — 2026-10-23

**PASS.** ContextService and InsightService use MVC controllers and real organization
NuGet packages built from unchanged public GitHub sources. No package token was required.

- Package build: `.artifacts/org-packages/runs/20261023T171740Z-92ceb688/provenance.json`.
  Six pinned repositories; six `.nupkg` files; all twelve per-service consumed package
  origin/SHA256 checks passed. Release API and test-project builds passed without warnings.
- PostgreSQL tests: ContextService **27/27**, InsightService **54/54**, no skipped tests.
- Published cross-service proof: `runs/20261023T172109550526Z/result.json`.
  Four stages passed: old SQLite baseline, imported PostgreSQL restart, fresh PostgreSQL,
  PostgreSQL restart. **158 recorded HTTP operations** across the three proof scripts.
- Migration: source files unchanged; row counts and content hashes matched; import to
  a nonempty database rejected; damaged-FK import rolled back all rows. Canonical question
  dependencies/invalidations, historical 100 ns boundary, receipt replay and job/cursor state
  survived import and restart.
- Actual AgentService HTTP clients were exercised with a controlled reasoning fixture.
  No paid LLM calls or real Kafka were used. Existing working databases were not migrated.
- Owned test API processes and the isolated PostgreSQL instance were stopped. Only the
  disposable databases created by the harness were dropped; existing PostgreSQL was untouched.

Package versions: Controllers 1.4.0, DapperContext 1.1.0, HttpClient 1.1.1, Auth 1.3.0,
Context 1.0.1, Result 1.0.0. Exact repository commits and archive hashes are in provenance.
These are source-built packages, not claimed downloads of published GitHub Packages binaries.
Service dependency overrides Pkcs/Xml 10.0.13 address the upstream transitive audit findings;
the pinned upstream library sources themselves were not edited.

Earlier failed fixture-development runs remain under `runs/`; only the final run above
is the acceptance evidence. Normal service NuGet.config files retain the GitHub registry.
The explicit token-free path is `cluster/tools/Build-OrganizationPackages.ps1`.
