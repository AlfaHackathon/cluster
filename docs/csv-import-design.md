# CSV source import — blocked candidate, 2026-10-26

## Decision and current deliverable

Use bounded COPY batches inside the owning service, with one transaction per batch,
not a data migration and not 1.38 million HTTP requests. Keep identity as immutable
`source / version / part / logical CSV row` and bind it to a SHA256 manifest. Preserve
every repeated payment pair and every self row. Keep input read-only and make import
an explicit one-shot Compose profile. Context projection is a subsequent controlled
step. No model, provider, login, password, membership or role creation belongs here.

**Apply is blocked. This PR contains the offline preflight and explicit refusal path,
not a complete production importer.** It performs no database writes. A successful
dry-run is not an imported/done state. No checkpoint or done marker is emitted because
no owner transaction has occurred. Reruns of preflight are deterministic and harmless.
Do not deploy this as a completed importer or treat schema existence as semantic readiness.

## Pinned contracts inspected through GitHub

No AGENTS.md or SKILL.md exists in the complete trees inspected for these three repos.
The workspace search also found no AGENTS.md. Implementation and review skills were read.

| Owner | Pinned evidence | Available contract and consequence |
|---|---|---|
| Company | PR8 head `b5d9c1dc3deabc9c319c5325993e1e6e270e413b`, tree `678da9e25c9758ea89f46a2be6dd8c7765a8a6cf` | `CompanyBankProfileService.Capture` reserves an operation, publishes and verifies a Context material, then persists a canonical profile with exactly one verified source reference. There is no independent deferred-projection bulk contract. |
| Company | Same tree, `KnowledgeService.ValidateAsync` | Group/person/link writes need verified Context evidence. GROUP_ID alone does not justify an operational parent, ownership, grants or an invented group name. |
| Company | Same tree, `PaymentRowStore`, `PaymentRowValidation`, `PaymentRowSql.resx` | Exact immutable row API and transaction invariants exist. Single-row HTTP is unsuitable at 1,380,585 rows. COPY must retain the owner advisory lock, exact serialization/hash, server monotonic knowledge time, version binding and exact replay semantics. No direct writer was added without proving these invariants. |
| User | PR6 head `fcabb00024f2f8c924dd18dba2437dad8790c47b`, tree `28b8e338b2987fe419613913a75ced541fec1dac` | Source-only `UserId=UUID(F,U)` is supported independently of auth. PK account and UNIQUE person/company pair; immutable revisions and exact raw geography. Existing auth-backed UUIDs additionally require owner membership/lineage validation. Candidate is not deployed; native build is blocked by NuGet 401 per parent. |
| cluster | master `ce709d887cf4eba259eb24b262600926e1d84399` | PostgreSQL has healthcheck. Company/User run migrations before opening HTTP, but have no healthcheck routes/config. The overlay has a read-only schema gate after DB health and service start, with bounded retry while migrations run. Table presence is not a complete readiness/semantic contract. |

The source instruction was compressed as `CompanyPR8b5...` / `UserPR6fc...`.
PR metadata confirms that 8 and 6 are PR numbers, not leading SHA characters.
An initial read with those digits included returned missing-ref 404; tree/blob reads
and PR metadata resolved the correct exact revisions. No access-denied operation was retried.

## Exact input contract still required from root

Do not download the production ZIP to cloud or read dataset rows into a conversation.
Root holds `/root/contour-data/UUID_DEMO_ESTIMATED_RUB2025_v1_20261026.zip`.
Need exact extracted file names, ordered headers, delimiter, SHA256 per file, null
encoding (if any), exact PAY_AMT/PAY_CNT totals and self-row count. Persons/accounts
headers are not inferred from synthetic fixtures. Manifest is deliberately unfinished.

Expected source counts supplied by parent: companies 204708 including incomplete;
persons 11120; distinct groups 18098; accounts/links 12514; payments 1380585.
Payment duplicate directed pairs 9, extra rows 17. The agreed 601 technical revenue
values were replaced upstream with demo estimates; importer must not repeat a transformation.
RUB / since January 1 does not establish a payment year or end date. The version
label's RUB2025 revenue reference must not be applied to the payment period.

The validator preserves file bytes through hashes, raw empty/whitespace cells and
payment lexical values. CSV contains strings; it does not infer a null sentinel.
An explicitly confirmed sentinel must be specified and implemented before owner writes.
Group IDs are derived as the distinct nonempty GROUP_ID values, not operational companies.
No standalone person name/email/job is generated. F/U geography is service geography.

## Required writable slice before ready

1. Owner decides and implements admission of canonical CSV bank/group/person/link
   data with file lineage and deferred Context projection, or explicitly coordinates
   the existing synchronous material workflow. Do not fabricate source references,
   names, creators, relations or source provenance to fit existing DTOs.
2. Add a bounded owner payment batch endpoint or owner-local COPY writer using the
   actual owner serializer and validations. Transaction: validate all batch rows;
   acquire `KnowledgeSql.WriteLock` (319264383); bind/check source version hash;
   compare existing row hashes; identical replay returns original receipt; changed
   body/import metadata conflicts; assign monotonic server ticks; COPY only new rows;
   record checkpoint in the same transaction. Never UPDATE a previous immutable row.
3. Deploy and verify User PR6 through the normal native package path. Do not bypass
   NuGet authentication or create auth accounts from CSV.
4. Stage order: groups/companies -> persons/account lineage -> links -> payments.
   Default full import preflights all readiness gates before writes; phase blockage
   returns nonzero and never reports the whole batch as successful. Explicitly
   completed Company work remains replayable without duplicates when User is blocked.
5. Checkpoint key includes immutable manifest hash, source/version, phase and batch
   ordinal; checkpoint and writes share a transaction. On crash, resume from owner
   checkpoints, replay an uncertain batch with exactly the same key and body. Source
   identity is not a business pair. Changed bytes under the same source/version fail.
6. Update policy: immutable new version for corrected data; no overwrite, no default
   merging or deleting prior source versions. A new manifest must never silently
   reuse a previous source/version. Server assigns knowledge time, not source year.
7. Verify scoped committed row counts, FK registry coverage, totals, duplicate pair
   multiplicities, self rows and hashes. Write done marker only after verification
   in owner state; a local JSON file is not sufficient evidence. Context sync then
   runs separately with an explicit cap/cursor and no bulk insight generation.

## Current executable verification

From `importer/`: `python3 -m unittest discover -s tests -v`.
Fixtures are entirely synthetic; no PostgreSQL, Docker container, native package,
production record or provider call is used. Cases cover lexical precision, duplicate
pairs/self rows, account pair uniqueness, two companies per person, headers/hash/FK,
UTF-16 geography limits, silent PII-safe errors and explicit apply refusal.

Verification: 26 unit fixtures pass. Static Compose rendering with synthetic
interpolation and `--no-env-resolution --quiet` passes; no daemon/container is used.
Two independent review axes found Unicode-policy mismatches; fixes now cover .NET
whitespace/control predicates and UTF-16 length, plus accurate apply-mode reporting.
Original full-import gaps remain blocked, not approved as completed functionality.

Reports explicitly expose `skipped=0`, `conflicts=0`, `persisted=0` for a successful
full preflight. `incomplete` counts rows per role with an empty or .NET-whitespace
nonidentity cell; these rows remain counted and are never silently dropped. This
is raw-field completeness, not inferred employment, account access readiness or an
assertion that an optional field is required. Group absence is optional identity,
so it does not alone increment this measure. On the first invalid row preflight
fails nonzero: statistics explicitly say `stopped_at_first_error_not_full_dataset`,
incomplete is unknown/null and a duplicate-identity failure reports one observed
conflict, not a fabricated total across unscanned rows. No records are skipped to
turn an invalid source into a successful import. Apply remains wholly blocked.

A synthetic full-population run (204708 companies, 11120 persons, 12514 accounts
and links, 1380585 distinct payment pairs) validated in 83.46 seconds with 424.7 MiB
peak RSS in this cloud environment, including synthetic CSV generation and hashing.
This is not a VPS timing guarantee or validation of real source data. Reproduce with
`python3 importer/tests/benchmark_preflight.py`; it uses only temporary synthetic
files and removes them. Compose caps the preflight at 768 MiB, below the 4 GiB VPS
budget; concurrent production services still require their own available-memory check.

Offline operator preflight only, after root fills and verifies manifest:

```bash
python3 importer/validate.py --input "$CSV_INPUT_DIR" --manifest "$CSV_INPUT_DIR/manifest.json" --mode dry-run
docker compose -f docker-compose-prod.yaml -f docker-compose-import.yaml --profile csv-validate run --rm --no-deps csv-validate
```

The first command needs only Python. The second builds a minimal dependency-free
Python image if not already present and gives the container no network. Normal
service restart never selects either import profile. There is **no ready production
apply command**. `--mode apply` always returns exit 3 after validation; bad input
returns exit 2; valid dry-run returns exit 0. Reports contain aggregates and metadata,
not data rows. Keep root reports local; do not publish source identity if sensitive.
