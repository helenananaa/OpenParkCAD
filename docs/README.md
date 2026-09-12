# Documentation and workspace guide

Start with the [project README](../README.md) for setup and a first solve.

## Find the right document

| Question | Document |
| --- | --- |
| What works today, and which checks can I rely on? | [Current status and capability matrix](current_status.md) |
| What input fields are supported? | [Input model](input_model.md) and [JSON Schema](../schema/openparkcad-input.schema.json) |
| Which demo exercises a feature? | [Examples catalog](examples_catalog.md) |
| What does vehicle and constraint validation mean? | [v0.3 vehicle and constraint contract](v0.3_vehicle_and_constraints.md) |
| How do candidate selection and stall modules work? | [v0.4 discrete candidate contract](v0_4_discrete_candidates.md) |
| What remains to be built? | [Roadmap](roadmap.md) |
| How do I execute the next development stages step by step? | [Next five stages execution plan](next_stages_execution_plan.md) (planned; start with road traversal R1–R4) |
| How was the multi-spine iteration implemented? | [v0.4 benchmark and multi-spine execution plan](v0_4_multi_spine_execution_plan.md) (E0–E9 implemented) |
| What evidence closes the multi-spine iteration? | [v0.4 acceptance results](v0_4_multi_spine_acceptance.md) and [benchmark statistics](verification/v0_4_20260903.json) |
| How is a release checked? | [v0.3 release checklist](v0_3_release_checklist.md) and [CI workflow](../.github/workflows/ci.yml) |
| Why was the algorithm designed this way? | [Algorithm design discussion](algorithm_design_discussion.md) |
| What changed, and in what order? | [Changelog](../CHANGELOG.md) and [phased implementation history](phased_plan.md) |
| Where are the representative regression sites? | [v0.3 fixtures](../tests/fixtures/v0_3/README.md) |

The capability matrix and generated reports describe current behavior. Design
discussion and implementation history provide context for earlier decisions.

## Workspace layout

| Path | Purpose |
| --- | --- |
| `openparkcad/` | Python package, layout generation, validation, and exporters |
| `tests/` | Regression tests and versioned fixtures |
| `examples/` | Small runnable input JSON files |
| `schema/` | Published input JSON Schema |
| `docs/` | Contracts, capability documentation, design notes, and release guidance |
| `.github/workflows/` | CI definitions |
| `output/` | Local generated DXF, SVG, and report sets; ignored by Git |
| `dist/` | Fresh wheel and source-distribution builds; ignored by Git |
| `.venv/`, `*.egg-info/` | Local development environment and editable-install metadata |

## Keep generated output together

For a demo, put all three outputs under `output/examples/<name>/`; for a release
smoke run, use `output/smoke/<name>/`. The commands in the
[examples catalog](examples_catalog.md) and [release checklist](v0_3_release_checklist.md)
set all three paths explicitly. `--out` names one DXF file, so also set
`--preview` and `--report` when keeping several runs. Re-running the same name
replaces its previous output set; choose a new directory to retain both runs.

Local historical drawings, reports, and package builds are kept under
`output/archive/<timestamp>/`, with their original directory names and a
SHA-256 manifest. Files inherited from the old `out/` directory can be DXFs
without a `.dxf` extension; their original names are preserved in the archive.
Cleanup inventories and verification receipts live under
`output/maintenance/<timestamp>/`. These folders are local and ignored by Git.
Any fixture needed for a regression belongs in `tests/fixtures/`.

Tool caches (`__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`)
can be regenerated. Keep `.venv/` and editable-install metadata in place during
routine cleanup so the development environment remains usable.
