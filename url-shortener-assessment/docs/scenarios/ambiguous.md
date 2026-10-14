# SC-A - Ambiguous expiration requirement

## Original input, severity and affected scope

Original input: **"Make links expire soon."** This leaves default TTL, maximum TTL, timezone,
retroactive behavior, expiry boundary, API response and retention undefined. Severity is high for
correctness and compatibility: guessing could invalidate existing customer links or count unexpected
redirects. Affected components are specification, create validation, activity checks, contracts,
analytics tests, setup examples and migration/retention policy.

## Detection and safe suspension

The structured workflow includes the unresolved TTL question and no complete criteria. The engine
records the lexical/completeness checks and enters WAITING_CLARIFICATION. The worker attempts no
implementation task. Operator credentials cannot approve their own clarification. The original text,
questions, stage and reason remain in signed state and audit records.

## Explicit proposed interpretation

The demonstration fixture records: omitted expiry means no automatic expiry; an explicitly supplied
aware instant must be in the future within 365 days; resolution at or after that instant returns 410;
existing links are not retroactively changed; analytics is retained. This is the implemented proposal,
**not a real customer or candidate decision**. The real human gate stays pending in the submission
approval register. Do not describe the fixture's automatic action as an actual human clarification.

## Impact, replan and resumption

The fixture first demonstrates clarification and the requirements gate. It then proposes a formal
upstream revision with an impact list, enters WAITING_CHANGE and records reviewer-fixture acceptance.
The original revision remains in history. Downstream source/results/gates are invalidated, revision
increments, and the new explicit criteria return through requirements/architecture approval before
snapshot and parallel validation. Stale old tokens cannot publish into the new revision.

## Decomposition and validation

Identify material questions -> block affected path -> present alternatives -> record a reviewed choice
-> enumerate impacted requirements/APIs/tests/docs -> propose change -> approve replan -> rerun affected
tasks and negative checks. `tests/test_workflows.py` verifies blocked execution, clarification, retained
original text, replan invalidation and decision lineage. API tests verify authorization and stale versions.
The replay/expiry tests validate actual behavior separately from the workflow demonstration.

## Evidence and outcome

`evidence/scenarios/ambiguous.json` includes original/clarified/replanned snapshots and verified audit,
plus actual command outcomes from the local worker. Fixture provenance is visible at every approval.
The final state reflects any missing external check, not a fabricated successful release. A real
candidate must decide these ambiguities and approve a separate credential-action workflow before
claiming the guide's end-to-end human-governed scenario is complete.
