# v0.5 parallel-ladder fixtures

These inputs are **synthetic**. They are not licensed real CAD, surveys,
customer sites, or constructability proofs. Do not mix them with real-site
evidence.

| File | Case | Split | Role |
| --- | --- | --- | --- |
| `../../examples/parallel_ladder_rect_site.json` | N-T01 | development | Positive: three parallel parking aisles + both-end cross aisles |
| `../../examples/parallel_ladder_l_site.json` | N-T02 | holdout | Positive: L-shape that should use the second wing |
| `parallel_ladder_tight_reject.json` | N-T03 | development | Hard reject: 10 m cannot hold two 6 m aisles |
| `parallel_ladder_turn_reject.json` | N-T04 | holdout | Hard reject: graph-contact T whose turning envelope hits `t-fillet-block` |

`regression_expectations` and `metadata.v0_5` are test metadata, not solver
input. Predicates live in `tests/v0_5_parallel_ladder_support.py`. Development
cases must not be retuned using holdout outcomes.
