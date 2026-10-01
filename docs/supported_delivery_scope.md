# Supported delivery scope (private surface lot)

Chosen scene: a private surface parking lot for a rigid passenger car.
Inputs are versioned JSON or a support-range DXF. Outputs are DXF/SVG/JSON for
engineering review, not construction documents.

Rule profile `private_surface_lot_v1` only names checks the software actually
runs: containment, traffic graph, maneuvers, declared hard exclusions, declared
quotas, optional road traversal, and operational-quality proxies.

It does not claim slope, statutory accessible ratios, fire-apparatus swept
path, charging-equipment design, or permit certification. Those require human
review. The software cannot mark human review as approved.

This repository's trial is synthetic. No licensed real site is included, so no
field-effect claim is made.
