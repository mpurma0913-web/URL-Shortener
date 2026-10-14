# Executed scenario outcome index

These are actual local command/state observations. Fixture approvals are not human sign-off.

| Scenario | Run ID | Final observed state | Audit verification |
|---|---|---|---|
| greenfield | `1e72aead-c0c1-448b-90fe-b2507d881121` | BLOCKED_POLICY | True |
| brownfield | `72760447-3f71-44ff-9b53-868caed4f774` | BLOCKED_POLICY | True |
| ambiguous | `28ba20dd-0b80-493c-bbd7-419892ede32a` | BLOCKED_POLICY | True |

All four worker tasks completed evidence collection in each scenario. Actual test, contract,
documentation and local static checks passed; Ruff, mypy and dependency audit remained UNEXECUTED.
The workbench therefore blocked release instead of manufacturing a green result. Each raw JSON
record includes initial input, snapshots, stage results, source digest and audit events.

The recovery fixture records three injected failures recovered after 15, 10 and 5 simulated seconds:
30 / 3 = 10 seconds MTTR, zero unrecovered failures after the successful retry. Before resumption,
all three were unrecovered and the denominator was empty. These values validate the calculation;
they are not operational production measurements.
