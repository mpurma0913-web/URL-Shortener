# SC-G - Greenfield URL shortening capability

## Input and interpretation

Implement public HTTP(S) URL creation, random code generation, 302 resolution, explicit expiry,
private aggregate analytics, revocation, health and bounded failure handling. The proposed criteria
are observable: valid POST 201, invalid input rejected, GET 302, HEAD uncounted, expired/revoked 410,
parallel GET counts exact, stored links survive application restart. No free-text "fast" requirement
is invented. A well-defined workbench input skips clarification but retains material review gates.

## Decomposition and dependencies

T01 scope/spec -> T02 contracts/ports -> T03 failing tests -> T04 domain/storage/service ->
T05 HTTP safety/console -> T12 regression/negative/security checks -> T13 documentation.
The app's transactional behavior must exist before claiming useful API behavior. Core tests can
exercise injected clocks/generators independently of the UI. Later workbench validation fans out only
after a source manifest exists, then joins before release policy.

## Actual execution

Core acceptance tests were written before the application module. The initial execution failed because
`shortener.api` was missing, establishing the actual greenfield baseline. Implementation then satisfied
29 core cases. The exact baseline commit and logs are retained under `evidence/greenfield`. Later
security, schema, worker and failure-path tests extend that baseline; not every integration change is
represented as test-first work.

## API, schema and design impact

Introduced `/api/v1/links`, private metadata/statistics/revocation, `/r/{code}`, health and metrics.
Migration 001 owns links, daily aggregates and fixed-window rate counters. ADR-001..004 describe the
local architecture, storage, code allocation and analytics semantics. The API contract is generated
from typed models and compared to a committed snapshot.

## Validation and failure behavior

Run `python -m pytest tests/test_core.py tests/test_security_reliability.py -q -W error`.
Tests cover input rejection, role separation, persistence, concurrency, counter rollback, lock timeout,
expiry boundary, host/header/body safety and private metrics. Run `scripts/smoke.py` for real loopback
HTTP without external navigation. These are local demonstrations, not a public traffic SLO.

## Workbench evidence and outcome

The recorded greenfield workflow performs quality checks, records why clarification was skipped,
then waits for real approval. A separately labeled fixture path supplies test approvals so the actual
worker can be exercised. Source snapshot and independent test/security/documentation tasks are recorded
with audit events. Missing external tools cause BLOCKED_POLICY in this environment. No release or
candidate approval is manufactured to make the outcome appear complete.
