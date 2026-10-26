# CSV source import candidate — 2026-10-26

This candidate implements bounded owner batches, immutable manifest binding,
transactional checkpoints, resume and post-write verification. It has not run
against production or a test database. Native .NET builds and owner fixtures need
the normal native gate: no SDK/private package restore was available here.

Company/User models from existing PRs are untouched. User PR6 is now deployed at
1a18bdebb114851eda3cc8c471ef94f431f956e1, tree
28b8e338b2987fe419613913a75ced541fec1dac, per root confirmation. New batch
endpoints/migrations are separate prerequisites. Company base is PR8 merge
fe184e120451ce552ed698339d5b498c4fc939f3/tree678da9e25c9758ea89f46a2be6dd8c7765a8a6cf;
Context base5321943051b6efa7deec037d6b3a24ca054532c5/tree2e1e9e420feed0f25e91bedbd6acf2a4e3f44e81.

## Admission semantics

One dataset material cannot fit Context's limits of 1000 Company UUIDs and 200000
text characters. Operational GroupProfile also requires a name absent from CSV.
The additive contract uses a verified declared_source material per bounded raw
batch, containing exact headers, row numbers/values and source/version/manifest hash.
Context hashes the normalized material; Company verifies its exact returned envelope
and hash before committing owner rows. Frozen reservations retain server time/body
across crashes. No placeholder evidence, names or creators are invented.

Context declared_source is excluded from company/market evidence retrieval and raw
search projection. CSV bank revisions require CsvImport:ProjectionEnabled for both
indexing and reading stale index points. Import requires Context worker disabled;
it never enables projection or insight generation. Post-import projection remains
a separate controlled operator step.

Groups/persons/F-U links are immutable raw declared lineage in Company owner tables,
not named GroupProfile, employment, ownership or access assertions. Canonical bank
profiles contain all 23 owner fields and verified batch references. PROFILE_INCOMPLETE
stays in raw lineage. User batches populate existing canonical source identities
and revisions with exact F/U and raw geography; ROLE stays raw and grants nothing.
Auth-backed UUIDs are blocked and require the individual membership-validating owner
contract. No credentials, fabricated FIO/email/job, roles or membership are generated.

Payments use the actual PaymentRowInput serializer/validation and owner advisory
lock319264383. Bounded JSONB set inserts use one transaction per batch, not one HTTP
request per row. SourceRowId is payments:<logical row>; every duplicate directed
pair and self row is independent. Amount/count strings retain exact lexical text.

## Persistence and verification

Manifest hash is SHA256 of the exact canonical ASCII JSON string sent to owners,
including every file hash/header/mapping/count/total. Owners bind source/version to
it. Checkpoint and admitted rows share one transaction. Exact replay returns its
original receipt before Context calls or a new clock; changed bodies conflict.
A crash after Context publication reuses the frozen material; a crash after owner
commit resumes from its checkpoint. Retry keeps the same batch size/body.

This release is initial-load-only for companies and initial source accounts. An
existing bank profile or auth-backed User UUID blocks instead of overwriting.
Links append immutable geography/source revisions for the admitted F/U pair.
Corrected-version full company re-import is not implemented and needs a separately
reviewed policy. Gaps/overlaps, unknown outcomes and prerequisites fail nonzero.

Order: groups -> companies -> persons -> User accounts -> Company raw links ->
User geography links -> payments. Each phase requires owner verification. User
failure preserves the verified Company prefix; rerun resumes without duplicates.
Verification checks counts/checkpoint continuity, canonical bank/User revisions,
groups, exact payment NUMERIC totals, duplicate multiplicities and separate self
rows. Owner verification markers share the successful verification transaction.
Client done requires all seven verified owner phases; no local done file is trusted.

Reports expose skipped/conflicts/incomplete. Preflight incomplete means a row with
an empty/.NET-whitespace nonidentity cell; optional GROUP_ID absence alone is
excluded. Such rows are retained. Owner raw verification additionally counts rows
with any empty cell, which is a distinct measure. Errors identify partial scan scope
and unknown incomplete totals. Apply persisted counts are verified owner:phase
counts, keeping Company and User links separate. No cell values are logged.

## Confirmed input

importer/manifest.UUID_DEMO_ESTIMATED_RUB2025_v1_20261026.json contains root's exact
metadata: UTF-8 without BOM, semicolon, LF, six file SHA256 values. persons.csv is
a byte-identical alias of ZIP users.csv; accounts are under
account_mapping_addendum/accounts.csv. Root mounts that layout or changes relative
paths once and retains the resulting manifest identity. Do not regenerate data.

Headers: companies25 including COMPANY_ID/PROFILE_INCOMPLETE; persons USER_ID;
accounts ACCOUNT_ID/PERSON_ID/COMPANY_ID/ROLE; links USER_ID/CITY_NAME/RC_NAME/COMPANY_ID;
payments BENEFICIARY/PAYER/PAY_AMT/PAY_CNT; groups GROUP_ID. Full ordered headers
are in the manifest. Empty ROLE remains non-authoritative.

Counts:18098 groups,204708 companies including incomplete,11120 persons,12514 accounts
and links,1380585 payments. PAY_AMT55991235586000.0 RUB; PAY_CNT24515786; self27406;
duplicate directed pairs9/extra rows17. The agreed601 revenue demo replacements
occurred upstream and are not repeated. Payment year/end remain unknown.

## Verification and operator gates

33 Python fixtures pass, including lost receipt/resume, unavailable User, repeated
links, bad checkpoint/verification and explicit prefix stop. Native Company fake
persistence, User package-free protocol and Context declared-source fixtures are
supplied but unrun here. Static combined Compose config passes without a daemon.
Independent Standards/Spec reviews fixed FK lookup indexing, stale projection reads,
persisted reporting and Docker wiring. No new database, provider or production call.

An earlier preflight-only synthetic scale run measured83.46s/424.7MiB including
generation/hash; it predates new metadata/statistics and is not a current writable
or VPS benchmark. No further large CSV generation occurred for the writable slice.

Offline command: python3 importer/run.py --input "$CSV_INPUT_DIR" --manifest
importer/manifest.UUID_DEMO_ESTIMATED_RUB2025_v1_20261026.json --mode dry-run

Tests: python3 -m unittest discover -s importer/tests -q

After native gates/root deployment, require CsvImport:Enabled=true, migrations
Company20261026003/User20261027002, internal secret, owner URLs and Context guards
with worker disabled. Overlay defaults import-enabled=false and projection=false.
Explicit profiles do not run on normal restart. csv-import calls readiness/schema
gates and fails nonzero if unavailable. run.py --mode apply --stop-after companies
supports a verified prefix only. No ready production apply command is asserted
until native/runtime gates pass. Input is read-only; no secrets are embedded.
