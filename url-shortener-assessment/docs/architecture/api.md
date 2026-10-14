# API contract and examples

The executable contract is `specs/001-url-shortener/contracts/openapi.json`; standalone JSON Schema
files cover application requests/responses, workflow state, decisions, exceptions, failures and audit
events. `python scripts/export_contracts.py --check` rejects drift; tests validate actual responses
against these schemas. All example values below are illustrative, not evidence of execution.

## Authentication and common errors

Private calls use `X-API-Key`. The operator may create/revoke links and propose changes. The reviewer
may read private resources and decide gates/exceptions/resumption. Public redirects require neither
key. Keys identify roles, not individual humans. The system has one local administrative namespace,
not per-owner tenant authorization. Unknown request fields are rejected.

```json
{"error":{"code":"invalid_url","message":"URL must use HTTP or HTTPS"},"request_id":"<uuid>"}
```

Validation errors may additionally contain bounded `issues` with field/type, but do not echo
submitted values. Relevant errors: 401 missing/invalid key; 403 wrong role; 404 absent link/workflow;
409 conflicting replay, stale workflow version or invalid transition; 410 expired/revoked link or
withdrawn candidate; 413 body bound; 422 invalid input; 429 rate limit; 503 dependency/integrity failure.
Review concrete `error.code` values in tests and source; the example text is not a stable error catalog.

## Link creation and replay

```http
POST /api/v1/links
X-API-Key: <operator credential from local environment>
Idempotency-Key: release-notes-20261010
Content-Type: application/json

{"url":"https://example.com/release?channel=stable"}
```

The original response is 201 with a Location resource path and `Idempotency-Replayed: false`.
Repeating the same normalized URL and expiry with the same key returns 200, the same code, and
`Idempotency-Replayed: true`. The key must be 8..128 characters from letters, digits, `.`, `_`, `:`, `-`.
A different normalized payload with the same key returns 409. Omitting the key retains independent
creation behavior. A replay of an expired/revoked link returns that same resource without reactivation.

```json
{"url":"https://example.com/","expires_at":"2030-01-01T12:00:00Z"}
```

The expiry above illustrates syntax only; a real request must choose a future date no more than
365 days from the server's current time. Do not copy a stale fixed timestamp into a smoke test.
The source contract declares the exact `LinkResponse` fields. Generated `short_url` uses trusted
`BASE_URL`, never an untrusted Host header.

## Resolution, expiry and revocation

`GET /r/{code}` commits one redirect attempt and returns 302 Location with no-store/no-referrer.
`HEAD /r/{code}` has no body and no analytics increment. Both reject missing, expired or revoked codes.
Only eight-character alphanumeric codes are accepted. `DELETE /api/v1/links/{code}` is authenticated
soft revocation and remains idempotent on an already revoked link. No endpoint retargets an existing code.

`GET /api/v1/links/{code}` reads metadata. `GET /api/v1/links/{code}/stats?days=30` returns lifetime count,
last redirect, and a zero-filled ordered UTC window. `days` is bounded 1..90. Counts are committed GET
attempts, including repeated requests and bots; neither successful destination visits nor unique people.

## Workflows and review

```json
{
  "scenario":"greenfield",
  "requirement":"Create a short URL for a public HTTP(S) target.",
  "acceptance_criteria":["POST returns 201.","Invalid schemes return 422.","GET returns 302."],
  "ambiguities":[]
}
```

Submit that body to `POST /api/v1/workflows` with the operator credential. A complete input initially
waits for requirements approval. Read `id`, `version`, `status`, nodes and `policy_results` from the
response; do not assume that the initial version remains current across actors.

```json
{
  "expected_version":0,
  "gate":"requirements",
  "approve":true,
  "reason":"I reviewed the requirement and its negative acceptance criteria.",
  "evidence_refs":["specs/001-url-shortener/spec.md"]
}
```

POST to `/api/v1/workflows/{id}/decisions` using the separate reviewer key. Replace version 0 with the
current observed value. The server requires a reason and current gate, validates evidence-reference
syntax, and stores actor/revision/version lineage. Reference syntax validation is not proof a reviewer
actually read the document; that remains an explicit human obligation.

Other routes: `changes`, `recover`, `resume`, `exceptions`, `exception-decisions`, `audit`, `metrics`,
`candidate`. Their typed models and authorization live in `workflow_api.py` and the OpenAPI snapshot.
There is deliberately no endpoint to run a shell or post arbitrary PASS test results.

## Contract change checklist

Record affected requirement and consumers; identify additive versus breaking behavior; review the
ADR and data migration; regenerate OpenAPI/schemas; run schema validation, API tests and old-behavior
regressions; update examples; obtain human review. A new field does not justify quietly changing the
meaning of existing analytics or approval states. Retain migration checksums and source/evidence links.
