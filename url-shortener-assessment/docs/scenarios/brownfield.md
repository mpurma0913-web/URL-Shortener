# SC-B - Brownfield: retry-safe creation

## Existing baseline and impact analysis (recorded before changing behavior)
The working baseline creates a new random link for every successful POST. It already validates
input, persists links, redirects, records analytics and supports revocation. Baseline test result:
29 tests pass, with warning-free final output in evidence/greenfield/green-final.log. The exact Git
commit is recorded in evidence/greenfield/baseline-commit.txt.

New requirement FR-06: network retries carrying the same Idempotency-Key and normalized payload
must return the original link; a changed payload must return 409. Without a key, keep old behavior.

## Decomposition and dependencies
T06 impact analysis -> T07 replay/conflict/race regression tests -> T08 additive migration and
atomic repository lookup+insert -> transport response adjustment -> full baseline regression.
Impacted: API request header and response code/header contract; LinkService signature; repository
protocol and SQL adapter; additive idempotency table; OpenAPI; tests; setup/API docs; traceability.
Unaffected: public redirect URL, link codes, existing link rows, analytics semantics and UI schema.

## Risks and controls
Check-then-insert outside a transaction can duplicate links: BEGIN IMMEDIATE serializes the whole
operation, with unique key_hash as final guard. Replays after expiry/revocation return the original
resource without reactivating it. Normalization must preserve query ordering. Hash full normalized
payload, not just URL. Store a digest, not the raw caller key. Existing databases upgrade additively.
A key is retained for the life of its link; explicit retention/purge is deferred.

Rollback: older code can read the unchanged links schema and ignore the additive table. Do not
remove the table automatically: rollback loses retry semantics until v2 is restored. No destructive
down migration is supplied. Restore a verified backup when data rollback is actually required.

## Approval status
This change is implemented in the requested review candidate. No candidate-reviewed impact approval
is invented. Formal acceptance of ADR-003 and FR-06 remains a human handoff gate.

## Execution and validation
See evidence/brownfield/red.log, green.log, change.patch and the truthful Git history. Initial
regression is expected to fail because a retry returns 201/new code, not 200/the original code.
Final replay, conflict, concurrency, expired replay and migration tests are executable in
`python -m pytest tests/test_idempotency.py -q`. Test status is recorded, not assumed.


## Completed execution and validation record

The retained `evidence/brownfield/change.patch` compares the working core baseline with the actual
idempotency enhancement. The new regression suite first produced nine failures and one pass against
that baseline. After the implementation and additive migration, the combined core/replay suite passed
39 cases. Historical logs preserve the sequence rather than retroactively manufacturing TDD commits.
Later upgrade and security tests verify expiry/revocation replay and real v1 data preservation.

The runtime scenario records a brownfield requirement, an impact-review gate with explicit references,
requirements/architecture approvals by **test-fixture actors only**, and actual local validation.
Normal human approval remains pending. External security checks are not synthesized as successful.
Run `python -m pytest tests/test_idempotency.py tests/test_contracts_migrations.py -q -W error`.

Rollout is additive and no-key calls remain independent. There is no automatic TTL purge of replay
bindings, a conscious retention limitation. Restoring a prior binary after schema upgrade requires
review of migration compatibility and may require its matched backup; do not claim instant safe
rollback merely because the migration added a table.
