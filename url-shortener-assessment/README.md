# URL Shortener - AI-assisted engineering assessment

A runnable local URL shortener and a separate, operator-driven engineering evidence workbench.
The application includes an offline web console, versioned APIs, transactional analytics,
expiration, revocation, idempotency, bounded failure handling and authenticated review gates.


## Start here

| Purpose | Location |
|---|---|
| Install, run, test, troubleshoot | [Setup and operations](docs/operations/setup.md) |
| Components, control flows and diagrams | [Architecture](docs/architecture/overview.md) |
| API requests, responses and compatibility | [API guide](docs/architecture/api.md) |
| Reviewer gates, recovery and change control | [Workflow guide](docs/operations/workflows.md) |
| Three scenario records and provenance | [Scenario index](docs/scenarios/index.md) |
| Results, risks and known limitations | [Testing report](docs/assessment/testing-and-limitations.md) |
| Evidence-based engineering summary | [Final summary](docs/assessment/final-engineering-summary.md) |
| Fast reviewer walkthrough | [Reviewer guide](docs/assessment/reviewer-guide.md) |

## Run locally

Python 3.11 or later is the declared support range; **Python 3.13.5 is the executed environment**.
Use an isolated environment and an internet connection for the initial dependency installation.

```bash
python3 -m venv .venv
# POSIX:
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python3 -m pip install -r requirements-dev.txt
python3 -m pip install --no-deps -e .
python3 scripts/init_env.py
python3 -m shortener
```

Open `http://127.0.0.1:8000`. Read the freshly generated `API_KEY` from your local `.env`
into the console's operator field. Keep `REVIEWER_KEY` separate. The console retains credentials
only in page memory; closing the tab discards them. Do not send credentials to an AI assistant.
The initializer refuses to overwrite an existing `.env`.

For application-only installation, use `requirements.lock` instead of `requirements-dev.txt`.
The workbench's validation tasks require the development dependencies. Docker configuration
is also supplied, but its build was **not executed** in the assessment environment.

## Exercise and validate

```bash
python3 -m pytest -q -W error --cov=shortener --cov-branch --cov-report=term-missing
python3 scripts/export_contracts.py --check
python3 scripts/quality.py --local
python3 scripts/smoke.py
python3 scripts/scenario_demo.py
# Full external gates, after all development tools are installed:
python3 scripts/quality.py
```

`quality.py --local` reports local evidence without certifying release readiness. The full mode
fails if mandatory tooling is unavailable or a check fails. Scenario fixture approvals are
explicitly labeled `test_fixture`; they demonstrate mechanisms, not real human sign-off.

## API essentials

| Operation | Endpoint | Result |
|---|---|---|
| Create | `POST /api/v1/links` | `201`; matching idempotent replay `200` |
| Redirect | `GET /r/{code}` | `302`, after analytics commit |
| Probe link | `HEAD /r/{code}` | Same resolution, no analytics increment |
| Metadata / revoke | `GET / DELETE /api/v1/links/{code}` | Private; revocation is idempotent |
| Analytics | `GET /api/v1/links/{code}/stats?days=30` | Lifetime and daily UTC counts |
| Workflow | `POST /api/v1/workflows` | Persisted requirements and review gates |
| Review | `POST /api/v1/workflows/{id}/decisions` | Separate reviewer credential, expected version |
| Contract | `GET /openapi.json` | OpenAPI contract; offline snapshot also included |
| Health | `GET /health/live`, `GET /health/ready` | Process health / schema readiness |

Management requests use `X-API-Key`. Redirects are public but rate-limited. No endpoint accepts
arbitrary commands or user-supplied successful test results.

