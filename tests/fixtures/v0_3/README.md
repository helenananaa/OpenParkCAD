# v0.3 representative fixtures

These inputs are synthetic, representative real-site-shaped regression assets.
They mimic common boundary, gate, obstruction, reservation, and quota patterns,
but they are not customer data, surveys, code-compliance examples, or human CAD
validation results.

| Fixture | Purpose | Intended v0.3 decision |
| --- | --- | --- |
| `irregular_courtyard_pass.json` | Non-rectangular court with scoped obstacle, walkway, and fire-edge exclusions | Produce a valid layout only if every requested check is active and conflict-free |
| `tight_rear_court_reject.json` | Tight rear court and demanding design vehicle | Fail closed on the requested vehicle check; never pass via the legacy proxy alone |
| `offset_gate_quota_reject.json` | Offset entrance, protected route, and explicit accessible/EV minimums | Fail closed if qualifying stalls cannot be generated and preserved |

`regression_expectations` is test metadata, not solver input. Outcome assertions
should bind to stable report categories and object IDs instead of exact stall
counts, because safe geometry improvements may change the optimum. Any future
human CAD comparison must record tool/version, vehicle, tolerance, reviewer, and
date beside the fixture; do not turn synthetic provenance into a field claim.
