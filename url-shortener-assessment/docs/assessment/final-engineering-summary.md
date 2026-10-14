# Final engineering summary

## 1. Executive engineering outcome and release-readiness status

A runnable local URL shortener and a separate governed evidence workbench are implemented. The strict
checkpoint passed 143 tests with 89.76% branch-aware coverage; final rerun evidence is under
`evidence/validation/final`. Actual loopback HTTP succeeds. **Formal release decision: NOT READY.**
The candidate still owes explicit material approvals, exact guide-toolchain execution, full external
quality/security checks and independent review. This summary does not turn fixture approvals into
real sign-off or a passing test suite into production certification.

## 2. Project objective, scope and 2-3-day timebox outcome

The objective is reviewable engineer-led AI-assisted engineering applied to a URL shortener. The plan
uses a proposed two-to-three-day milestone structure from the assessment, prioritizing working slices,
scenario evidence and validation over infrastructure. This package was generated and tested during
this assistant session, not a falsely dated multi-day candidate project. The guide's expanded orchestration
mechanics are demonstrated as a local workbench, not an autonomous coding service.

## 3. Confirmed requirements, assumptions, exclusions and deferred scope

AS-01..06 map the source deliverables. FR-01..10, WF-01..09 and NFR-01..05 normalize the proposed
application/workbench behavior. HTTP statuses, eight-character codes, analytics semantics, TTL bounds,
SQLite and coverage targets are engineering proposals, not client-supplied facts. Public anonymous
creation, unique visitors, multitenancy, SSO, reputation scanning and production deployment are excluded.
See the specification and `docs/governance/approvals.md` for pending decisions.

## 4. Architecture overview and accepted ADRs

FastAPI transport, domain/use-case ports, SQLite adapters, safety middleware and an offline console form
the application plane. A pure workflow engine, signed store and bounded local validation worker form
the control plane. Six diagrams document context, components, persistence, redirect flow, dependency
fan-out and recovery. Ten ADRs document alternatives and consequences. **No ADR is marked Accepted**;
all remain Proposed pending human review. Detailed ADRs were finalized during the build, not claimed
to have been approved before implementation.

## 5. API contracts and schema deliverables

Versioned `/api/v1` endpoints and the public `/r/{code}` route are described by the generated OpenAPI
snapshot. Standalone JSON Schemas cover creation, links, statistics, errors, requirements, workflow
state, decisions, exceptions, failures, stage outcomes and audit events. Tests compare snapshots and
validate real responses. Three checked SQL migrations preserve v1 data while adding keyed replay
and independent workflow tables. API examples and compatibility/restore rules are documented.

## 6. Orchestration model, dependency graph and state persistence

The graph conditionally clarifies input, conditionally reviews brownfield impact, requires requirements
and architecture approvals, captures an existing source manifest, executes tests/security/docs in parallel,
then joins and evaluates release policy. State, versions, revisions, claims, results and audit events
persist in SQLite. Source/lease fencing rejects stale results. The implementation stage captures source;
it does not generate application code. Fixed commands execute only in a trusted local checkout.

## 7. Human approvals, governance gates and decision lineage

Separate reviewer credentials and current-version checks protect approval APIs. Clarification, impact,
requirements, architecture, change, exception, resumption and release actions record reasons and actor
provenance. Fixture actors in scenario/transition tests are labeled and are not candidate approvals.
The actual approval register remains pending. Shared-key authorization is not enterprise human identity.

## 8. Compliance and change-control policy results

Every run identifies `assessment-policy/v1`. Mandatory tests, contracts, bounded secret/static hygiene,
lint, typing, dependency audit and documentation must satisfy policy before release. Missing tools
produce UNEXECUTED evidence and effective failure. A changed source or requirement requires an
impact-listed proposal, reviewed revision and invalidation of old downstream results/approvals.
The local workbench demonstrates mechanics, not legal or regulatory compliance certification.

## 9. Policy exceptions, compensating controls, expiry and approvals

Only dependency audit is exception-eligible; requests require reason, scope/check, compensating
control and expiry within seven days, plus a separate reviewer decision. Exceptions bind to the current
revision and expiry is re-evaluated. Tests cover approval/expiry/denial using fixtures. No actual exception
has been approved for this submission. Unwaivable tests/typing/lint/approval failures remain blockers.

## 10. Security controls, findings and residual risks

Authentication, role separation, explicit hosts, rate/body/time bounds, strict URL policy, parameterized
SQL, no target fetch, privacy-safe logs and memory-only console credentials are implemented. HMAC
protects workflow evidence within a local key trust boundary. Tests found and corrected multicast URL
acceptance and resource-warning issues. External lint/type/vulnerability checks remain unexecuted;
reputation, SSO, retention and hostile-host protection remain production concerns. See SECURITY.md.

## 11. Reliability, retry, fallback, rollback/compensation and safe-stop

Database busy errors retry at most three times; code allocation attempts are bounded. Worker leases,
command time/output bounds and at most three attempts per epoch limit execution. Up to two explicit
reviewer resumptions are allowed for recoverable stops. Permanent failures require reviewed replan.
Git-unavailable source inventory is a recorded fallback, not a security-check bypass. SQL rollback,
candidate-withdrawal compensation and operational backup restoration are distinct mechanisms.

## 12. MTTR definition, population, calculation, exclusions and unrecovered failures

Each failure records detection, recovery start and recovery completion when present. MTTR is the sum
of detected-to-recovered durations divided by recovered failures only; unrecovered failures are reported
separately and an empty recovered population yields null. A recovery fixture uses a labeled simulated
clock and injected errors to validate the arithmetic. It is not production reliability data. Wall-clock
correction and the choice of failure-event rather than incident population are explicit limitations.

## 13. Observability, auditability and evidence integrity

Request logs contain IDs, route templates, method, status and elapsed time without raw destinations,
queries, codes or credentials. Authenticated process metrics have bounded labels. Audit events and
signed state retain actor, revision, version, decisions and failures. Chain verification detects edits and
tail removal without the key, but a host administrator with it remains trusted. Actual command logs,
JUnit, coverage, source inventories and the real Git build history support independent inspection.

## 14. Greenfield well-defined requirement scenario outcome

Core tests first failed because the API module did not exist, then passed the implemented core. A
complete workflow input records quality checks and skips only unnecessary clarification. It retains
requirements/architecture/release gates. Fixture approvals allow actual worker validation to be exercised;
missing external tools remain blocking outcomes. See `docs/scenarios/greenfield.md` and retained logs.

## 15. Brownfield scenario outcome

A working baseline was measured before optional idempotency was added. Impact analysis identifies
service, SQL schema, API headers and regression risks. The new tests produced nine failures and one
pass, then the enhanced core/replay suite passed 39 cases. Concurrent duplicate requests resolve to one
persisted resource; no-key behavior remains independent and v1 data survives the additive migration.
The actual change patch and commit baseline are retained. Human impact approval remains pending.

## 16. Ambiguous-requirement scenario outcome

"Make links expire soon" blocks execution with explicit TTL/boundary questions. The fixture records a
proposed interpretation, retained original input, formal impact-reviewed revision and downstream
invalidation/resumption. API authorization prevents operator self-approval. These are executable mechanism
demonstrations, not a fabricated human clarification. A candidate must decide the actual expiry policy.

## 17. Test strategy, executed validation and actual results

Unit, API, contract, migration, persistence, concurrency, workflow, policy, subprocess and negative-path
tests are executed. The checkpoint is 143 passing tests/89.76% branch-aware coverage. Final JUnit and
coverage reports and the HTTP smoke JSON are the reproducible source of results. JavaScript syntax
passes; managed browser policy blocks a full GUI run. Docker and external security tooling are not
claimed as executed. Failed red/hardening logs remain available and no universal zero-bug claim is made.

## 18. Requirement-to-design-to-task-to-code-to-test traceability

The machine-checked 30-row matrix maps AS/FR/WF/NFR identifiers to scenarios, task IDs, ADRs, exact
code paths, named tests and evidence. The documentation checker validates path/test existence, not
business completeness. Source requirements and proposed behaviors remain distinguished. Tasks preserve
explicit dependencies and guardrails. Human approval obligations are represented as pending, not passed.

## 19. Known limitations, technical debt and deferred enhancements

Single-host SQLite and shared-key identity are deliberate limits. Counts measure attempts, not people
or destination visits. Indefinite retention needs policy. The worker trusts repository code; HMAC is not
external immutability. General semantic AI orchestration, public deployment, target reputation, cloud
HA, asynchronous analytics, browser certification and complete dependency/static findings are deferred
or open. Exact Claude/SpecKit provenance and real independent review cannot be retroactively invented.

## 20. Repository paths, reproducible commands and reviewer verification points

Start at README.md and `docs/assessment/reviewer-guide.md`. Run `python -m shortener`, the pytest
suite, `scripts/quality.py --local`, `scripts/smoke.py`, `scripts/scenario_demo.py` and the full quality
command after installing authorized tools. Inspect `evidence/validation`, `evidence/scenarios`, the
OpenAPI/schema snapshots, ten ADRs, six diagrams, traceability and the Git history bundle. The provided
clean-checkout evidence explicitly states its dependency provisioning boundary.

## 21. Final engineering judgment, unresolved blockers and recommended next actions

The implementation is a substantive runnable review candidate with defensible consistency/security
boundaries and genuine regression evidence. It is not honestly ready for a guide-compliant final release
until a human reviews the source conflict and proposed decisions, completes the specified tooling flow,
executes/remediates external checks, validates the intended runtime/browser/container environment and
records independent review plus final submission ownership. Do not remove this disclosure or relabel
fixture approvals when submitting. Resolve the blockers, then record a new evidence-based release decision.
