# Data ownership, invariants and migration model

Schema authority is `src/shortener/migrations/*.sql`, not this prose. The three immutable migration
files are applied in lexical order and their SHA-256 checksums stored in `schema_migrations`.

| Table | Key / fields | Invariant / retention |
|---|---|---|
| `schema_migrations` | version, checksum | Applied text must never change; no automatic destructive down migration |
| `links` | code, url, created_at, expires_at, revoked, total_redirects, last_redirect_at | Unique code; count nonnegative; URLs retained until reviewed deletion |
| `daily_clicks` | (code, day), redirects | FK to link; UTC YYYY-MM-DD bucket; no visitor-level events |
| `idempotency_keys` | key_hash, payload_hash, code, created_at | One request binding per hashed key; retained, so replay survives restart |
| `rate_limits` | (subject_hash, window), count | Hashed client/bucket; old buckets cleaned on requests, not a background retention SLA |
| `workflows` | id, version, created_at, state_json, state_mac | UUID, optimistic version, signed serialized workflow |
| `workflow_audit` | (run_id, sequence), event_json, previous_hash, event_hash | Contiguous per-run order; FK; HMAC chain tied to signed state head |

Timestamps are UTC Unix seconds in storage. API link timestamps are rendered as timezone-aware strings.
Expiry checks use `now >= expires_at`; revocation takes precedence as a disabled resource. Codes are
case-sensitive ASCII base62. Statistics retain zero-activity days in the response only; they do not
create rows for zero days.

## Transaction boundaries

Create, idempotency binding and generated-code collision checks form one write transaction. GET
resolution, lifetime increment and daily upsert form one write transaction. Workflow state and its
audit events commit together. Statistics and paginated audit reads explicitly use a consistent read
transaction. No application transaction performs remote network I/O.

The first schema supports a complete core service. The second adds idempotency without changing the
old no-key API behavior or rewriting existing links. The third adds workbench state. The upgrade test
creates a genuine v1 database with an existing count, then starts the current app and verifies both
data preservation and the new replay behavior.

## Compatibility and rollback

OpenAPI `/api/v1` is the current version; additive optional `Idempotency-Key` behavior remains backward
compatible. Response schema changes require regenerated snapshots and `--check` validation. Reusing a
code with a different destination is unsupported, preventing cached-link retargeting ambiguity.

Code rollback is not automatically schema rollback. A prior binary that rejects unknown migration
versions is not guaranteed to run against an upgraded database. Before upgrade, stop conflicting
writers, make a verified backup, deploy a compatible binary, run readiness plus smoke tests, and retain
the old binary and backup. To roll back an incompatible migration, stop writes and restore the matched
backup/binary pair. That can lose post-backup writes and therefore requires explicit owner approval.

## Privacy and retention

Targets can contain confidential query data; database files and backups must be access controlled.
Analytics contains aggregate counts only, but rate-limit hashes are still operational pseudonyms.
Their cleanup is demand-driven; offline backups may retain them until the backup is retired.
Links, idempotency bindings, daily aggregates and workflow histories currently have no automated purge.
A production retention schedule, right-to-delete semantics and legal policy require explicit decisions.
