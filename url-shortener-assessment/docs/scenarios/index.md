# Three scenario records and evidence provenance

| Scenario | Distinct objective | Documentation | Executable evidence |
|---|---|---|---|
| SC-G greenfield | New shortener from a failing baseline, no artificial clarification | `greenfield.md` | `evidence/greenfield`, `evidence/scenarios/greenfield.json`, core tests |
| SC-B brownfield | Add idempotent creation to a measured working API without breaking it | `brownfield.md` | `evidence/brownfield`, `evidence/scenarios/brownfield.json`, replay/migration tests |
| SC-A ambiguous | Suspend "expire soon", record explicit choice and invalidate downstream evidence | `ambiguous.md` | `evidence/scenarios/ambiguous.json`, state/API tests |

Run `python scripts/scenario_demo.py` from the trusted checkout. It creates isolated temporary
storage and writes actual state/audit/command evidence. Automatic reviewer actions are explicitly
`test_fixture` and **must not be cited as human approvals**. Normal credential-action workflows
created by a candidate remain blocked until that candidate's separate reviewer acts.

The runtime worker's tests/contracts/documentation outcomes are real commands. Unavailable external
security tools remain UNEXECUTED and the scenario's resulting policy block is retained, not hidden.
A separate recovery fixture demonstrates retries, compensation and MTTR calculation using injected
failure inputs; its simulated clock/population are identified in the resulting JSON.

These records cover both actual development sequence and reproducible control-plane demonstrations.
They do not claim the candidate personally completed the guide's required tool sessions or approvals.
