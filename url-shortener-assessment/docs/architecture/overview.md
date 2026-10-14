# Architecture overview

## 1. Boundary and rationale

The application plane is a URL shortener. The engineering workbench is a separate control plane
for reviewable evidence and bounded, operator-started validation. Neither plane invokes an LLM,
executes free-form prompts as shell commands or fetches shortened target URLs. AI assistance
occurred during development; the runtime is deterministic and inspectable.

The official assignment asks for engineer-led AI acceleration, not autonomous orchestration.
The attached guide extends that scope with persistent workflows, parallel checks and human gates.
The workbench demonstrates those mechanics without claiming to replace SpecKit or to have run
Claude Code. This deliberate interpretation remains pending candidate approval.

![System context](system-context.svg)

## 2. Components and responsibility boundaries

| Component | Responsibility | Explicitly not responsible for |
|---|---|---|
| FastAPI routers | HTTP parsing, auth dependencies, schemas, status codes | Business decisions or database SQL |
| `LinkService` | Normalize and validate requests; invoke persistence port | HTTP framework, filesystem, credentials |
| Domain model | Link activity, URL policy, clock/code-generator interfaces | Database lifecycle or transport |
| `SqliteLinkRepository` | Atomic link/idempotency/counter/rate-limit operations | Network requests or reviewer decisions |
| `Database` | Connection lifetime, migrations, transactions, bounded busy retries | Infinite retries, distributed consistency |
| ASGI safety middleware | Body/time bounds, request IDs, safe headers, low-cardinality telemetry | Business authorization |
| `WorkflowEngine` | Pure dependency/state/gate/recovery/policy rules | Shell, database, network or AI generation |
| `WorkflowStore` | Signed state, version checks, atomic audit append | Identity attestation or external WORM storage |
| `Worker` | Fenced claims and up to three parallel independent checks | Approvals or unbounded autonomous execution |
| `StageExecutor` | Fixed evidence tasks; source fingerprint binding | Arbitrary user-defined commands |
| `CommandRunner` | Subprocess deadline, output bound, secret stripping/redaction | Sandboxing an untrusted repository |
| Offline console | Accessible local forms; memory-only credentials | Token persistence, hidden approval or external assets |

![Components](components.svg)

## 3. SOLID, maintainability and testing decisions

Single responsibility is expressed at domain, use-case, storage, transport and validation boundaries.
Dependency inversion uses `Clock`, `CodeGenerator`, `LinkRepository`, `CommandExecution` and
`StageExecution` protocols. Tests substitute controlled clocks, generators and stage adapters,
without requiring a mocking layer around every object. Interfaces remain small and consumer-specific.

The concrete SQLite adapter can be replaced behind its port, but that does not make PostgreSQL a
configuration-only change: transaction isolation, rate-limit writes, migrations and idempotency race
semantics must be revalidated. No generic repository framework or distributed broker was added solely
to demonstrate patterns. Policy/state logic is centralized so API routes and worker processes cannot
implement divergent approval rules. Structured models reject unknown input fields.

Open/closed design is bounded: new validation adapters can satisfy the stage interface, but a new
gate, policy or API is a material contract change requiring explicit review. Liskov substitutability
is checked through adapter behavior rather than inheritance hierarchies. An interface is not a reason
to split transactional invariants across independently committing objects.

## 4. Create control flow

Authenticate/rate-limit -> parse strict body -> normalize URL -> validate idempotency-key syntax ->
start `BEGIN IMMEDIATE` -> check replay binding -> validate a new expiry -> generate code -> insert
link -> bind hashed idempotency key -> commit -> return 201. A matching replay returns the original
resource with 200; a changed normalized payload returns 409. Without a key, identical URLs create
independent resources. Payload hashing includes expiry and normalized URL, preserving query ordering.

The transaction checks an existing idempotency binding before rejecting an expiry that has since
passed. Consequently, retrying an old request returns the original resource, even if expired or
revoked; it never resurrects that link. Code collisions retry at most five generated codes; the primary
key remains the final uniqueness guard. Eight-character base62 codes are identifiers, not secrets.

## 5. Redirect and analytics control flow

![Redirect sequence](redirect-sequence.svg)

GET validates/rate-limits the code, starts a transaction, loads the link, evaluates revocation and
expiration, atomically increments the lifetime counter and UTC daily bucket, commits, and then
returns a no-store 302 with Location. A counter failure rolls the transaction back and returns 503;
there is no successful redirect that silently omits its selected analytics write.

This favors counting consistency over maximum redirect availability. A committed count is an
**attempted redirect response**, not proof a browser received it or visited the target. The connection
can fail after commit. Retries and bots can count again. HEAD performs the same validity check but
never increments counters. Private statistics read a consistent snapshot and fill missing UTC days.
Analytics never stores IPs, agents, referers, geography or unique-visitor fingerprints.

## 6. Persistence and compatibility

![Persistence relationships](data-model.svg)

SQLite owns links, daily counters, idempotency bindings, rate-limit buckets, signed workflows and
audit events. SQL is parameterized. Foreign keys are enabled. Migrations are ordered, checksummed,
transactional and additive; edited applied SQL is rejected. Migration 002 is the real brownfield
idempotency enhancement. Migration 003 adds independent workbench tables.

A local rollback-journal configuration is deliberate. SQLite serializes writes; every request uses
its own short-lived connection. `BEGIN IMMEDIATE` prevents a check-then-write race. Busy timeouts and
retry budgets are bounded. There is no distributed cache consistency claim and no background queue
that can lose analytics at shutdown. See `data-model.md` for fields, invariants and restore semantics.

## 7. Workbench dependencies and concurrency

![Governed dependency graph](workflow-dag.svg)

A complete input skips clarification, not requirements/architecture approval. Brownfield additionally
requires an impact review referencing evidence. Implementation captures an existing candidate-source
manifest; it does not generate source code. Tests/contracts, security checks and documentation checks
then fan out into three actual worker threads and synchronize before release evaluation.

Persisted claims use lease tokens and deadlines. Duplicate workers cannot own the same active stage.
Expired or replanned tokens cannot publish a result. Missing checks are never treated as success.
Source fingerprints before and after validation must agree with the candidate snapshot. A change
requires impact analysis and reviewer approval, increments revision, withdraws the old candidate and
invalidates downstream evidence and approvals while retaining history.

## 8. Trust boundaries and security

The public boundary is limited to redirects, the static shell, contracts and health. Management
requires a key; reviewer mutations require a separate key. This is not tenant isolation or verified
human identity. A key holder can act as its role. Bind only to loopback unless a reviewed identity/TLS
front door is introduced. The workbench CLI is privileged local code and requires a trusted checkout.

Submitted URLs are never dereferenced by the server. Syntactic checks reject unsafe schemes,
credentials, local literal addresses, multicast/reserved IPs, ambiguous numeric forms and unsupported
ports. Hostname DNS resolution, reputation, phishing and destination changes are not screened.
Rate limits use short-lived hashed client identities, not raw IPs. Forwarding headers are ignored.

The HMAC chain detects unauthenticated edits, reorderings and tail truncation through a signed state
head. It is not immutable against a host administrator holding the HMAC key. Export evidence to an
independent controlled store before relying on it for an audit outside the prototype.

## 9. Reliability, compensation and recovery

![Recovery and change control](recovery-state.svg)

Storage busy/locked errors retry at most three times with short jittered exponential backoff; other
storage errors fail closed. Worker stages have a 120-second lease, at most three attempts per epoch,
and bounded retry delays. Operator recovery classifies expired claims; a reviewer may authorize at
most two additional epochs for recoverable safe-stops. A permanent failure requires a reviewed change,
not blind retry. In-flight stale outputs are discarded.

Compensation withdraws the real candidate manifest from the API and cancels dependent claims. It is
not a database rollback of previously committed user operations, a Git revert, or an undo of browser
navigation. SQL transaction rollback, artifact compensation and operational restore are different
mechanisms and are named separately throughout the submission.

## 10. Observability, deployment and scaling path

Request logs contain request ID, method, route template, status and elapsed time, but not destinations,
query strings, raw codes, request bodies or credentials. Authenticated metrics are low-cardinality;
process metrics reset on restart. Workflow audit events include actor provenance, revision, version,
reason, stage and timestamp. Workflow metrics expose recovery populations and exclusions.

The runnable target is a single host. Container isolation is supplied but not executed here. A real
production path would separately justify PostgreSQL, ingress/TLS, federated identity, managed secrets,
centralized metrics, abuse controls, backup retention and load targets. Introduce queues or caches only
after measured need; invalidation, expiration and analytics semantics must remain explicit.

## 11. Tools and execution approach

Python/FastAPI/Pydantic provide typed transport contracts; SQLite provides local ACID storage;
pytest/HTTPX/jsonschema provide domain, API, concurrency, contract and live HTTP tests; Graphviz
produces version-controlled diagrams. Ruff, mypy and pip-audit are configured external gates, not
checks that can be claimed to have run when absent. The actual coding assistant was ChatGPT in this
session. SpecKit and Claude Code execution remain a disclosed guide-compliance gap.

External technical references and source-document precedence are recorded in `../governance/sources.md`.
