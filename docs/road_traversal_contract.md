# Road traversal contract (road-traversal-1)

This contract is implemented by `openparkcad.road_traversal` and related
modules. It is the runtime source of truth for requested interior journeys.
Unrequested solves keep the previous validity, geometry, scoring, default
legacy/greedy, and promotion behaviour.

## Coordinate frame

- Site coordinates are metres.
- Pose reference is the rear-axle centre (`VehiclePose`).
- Headings use `heading_degrees`, increasing counter-clockwise, 0 along +X.

## Scope and vehicle

- First-edition scope is `site_interior` only: entrance-inner anchor → supported
  forward road templates → parking/exit motion → exit-inner anchor.
- The report always records this scope. Off-site public-road access is not
  claimed.
- Supported design vehicle: rigid passenger-car parameters that already audit
  through `vehicle_kinematics` / `swept_path`. Articulated/trailer vehicles are
  `unsupported`.
- Road templates are forward-only. Parking templates may reverse using the
  existing stall families. Required multi-point road reversing is
  `unsupported`, not silently enabled.

## Anchors and occupancy

- An entrance-inner (or exit-inner) pose places the **entire body**, including
  swept-path margin, inside the declared interior drivable area. Placing only
  the rear axle on the boundary is not sufficient.
- Entrance throat width is checked against vehicle width plus margin.
- Missing off-site pavement required to turn in from outside is `unsupported`.
- Ordinary road travel may not occupy any stall face.
- A stall’s parking motion may use its serving aisle, allowed junction area,
  and the target stall. Other stall faces are occupied exclusions.
- Each retained stall needs at least one allowed entrance / parking / exit /
  exit-entrance combination. Stalls may use different gates. The first edition
  does not delete unreachable stalls to obtain a pass.

## Input

```json
{
  "constraints": {
    "road_traversal": {
      "enabled": true,
      "scope": "site_interior",
      "time_budget_seconds": 10.0
    }
  }
}
```

| Configuration | Meaning |
| --- | --- |
| missing `road_traversal` or `enabled=false` | `not_requested`; existing decisions unchanged |
| `enabled=true` | every retained stall needs a complete interior journey |
| `scope` | only `site_interior`; any other value is an input error |
| `time_budget_seconds` | positive finite number (booleans rejected); default 10 s; cooperative budget for one layout validation |

Unknown keys inside `road_traversal` are input errors. Source maneuvering
requests are preserved; the road check records an effective policy instead of
rewriting the input. A shorter remaining outer search budget wins and is
recorded.

## Status

Official publication requires `status=passed` **and** `valid=true` together.
Excluding only `valid=false` is not sufficient.

| status | executed | valid | Meaning after a request |
| --- | --- | --- | --- |
| `not_requested` | false | null | not a hard road gate |
| `passed` | true | true | every retained stall has a complete valid journey |
| `failed` | true | false | a concrete motion failed, or supported templates are exhausted |
| `unsupported` | recorded | null | vehicle/motion/boundary outside this edition; not a pass |
| `incomplete` | recorded | null | budget, cancel, or required evidence unfinished; not a pass |

Input structure/value errors use the existing input-error channel. Internal
exceptions keep their exception class and fail the test/benchmark; they are
not rewritten as `failed` or “site unsolvable”. Exhausting the finite template
set is `no_supported_route_found`, not a proof that no continuous path exists.

## Pose joining

- Position tolerance: `1e-3` m.
- Heading tolerance: `0.05` deg after wrapping to (-180, 180].
- Tolerances absorb floating-point error only. Geometry is never translated to
  close a gap.
- A forward/reverse change is allowed only at a stop with the same position and
  heading. Reversing travel direction is not a heading flip.

## Envelope

Road checks reuse `simulate_bicycle_path`, `vehicle_footprint`, and
`validate_swept_path`. Between-sample coverage uses the conservative convex
hull plus a derived heading-step sagitta buffer of the farthest body corner.
Numeric join tolerances, sample step/heading, and `swept_path_margin` are
reported separately. Enlarging numeric join tolerance is not used to hide a
collision.

## Identity

Evidence is bound to the exact aisle/stall geometry, entrance headings,
vehicle parameters, occupancy assumptions, policy, and algorithm version
`road-traversal-1`. A candidate id or stall count is not an identity. Official
rebuilds re-run the check on the rebuilt object.

## Supported template families

Delivered families have pass and reject pairs. Families listed unsupported are
reported as `unsupported`, never skipped-as-pass.

Supported road families: `straight_forward`, `entrance_throat_to_main`,
`main_to_exit_throat`, `main_branch_left_turn`, `main_branch_right_turn`,
`dogleg_single_jog`, `dogleg_double_jog`, `turnaround_u_turn`, `u_connector`,
`exit_turn`.

Supported parking families: `perpendicular` (90° reverse-in), `angled`,
`parallel`, `t_end`, using the current stall templates.

Unsupported in this edition: `reverse_road_travel`, `offsite_approach`,
`articulated_vehicle`, `arbitrary_junction_angle`.
