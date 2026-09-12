# OpenParkCAD Input Model Reference and Discussion

This document discusses the JSON input model. It is not final API law yet. The
main goal is to separate site facts, design constraints, and optimization choices
so the algorithm can evolve without changing the whole file format every time.
The machine-readable accepted shape is
[`schema/openparkcad-input.schema.json`](../schema/openparkcad-input.schema.json).
Schema acceptance does not mean every field is active; runtime diagnostics and
the support matrix below define enforcement.

## 1. Design Principle

The input file should describe three different kinds of information:

```text
facts
  Things that are true about the site and cannot be changed by the solver.

constraints
  Things the solver must obey.

choices
  Things the solver may choose while searching for a good layout.
```

Examples:

```text
site boundary shape          -> fact
entrance location and width  -> fact
vehicle turning radius       -> constraint
allow narrow aisles          -> constraint or choice, depending on mode
actual aisle placement       -> choice
stall orientation            -> choice unless user locks it
```

This distinction matters. If a narrow aisle is a site requirement, the solver
must use it. If narrow aisles are only allowed, the solver should compare them
against wide aisles and pick the better design.

## 2. Proposed Top-Level JSON Shape

```json
{
  "version": "0.1",
  "name": "example site",
  "units": "m",
  "standards": {},
  "site": {},
  "site_features": [],
  "entrances": [],
  "pedestrian_and_emergency": {},
  "parking": {},
  "vehicles": {},
  "aisles": {},
  "constraints": {},
  "optimization": {},
  "diagnostics": {},
  "metadata": {}
}
```

Top-level responsibilities:

- `site`: land geometry, obstacles, setbacks, reserved areas.
- `site_features`: fixed site equipment such as columns, curbs, gates, charging
  stations, wheel stops, and signs.
- `entrances`: how vehicles enter and leave the site.
- `pedestrian_and_emergency`: people, accessible routes, fire lanes, emergency
  access, and related no-parking areas.
- `parking`: stall types, quotas, preferred stall families.
- `vehicles`: design vehicle assumptions.
- `aisles`: allowed aisle types and aisle behavior.
- `standards`: jurisdiction and rule profile metadata.
- `constraints`: hard rules.
- `optimization`: soft preferences and scoring weights.
- `diagnostics`: what explanations and debug layers should be exported.
- `metadata`: notes, source, author, coordinate system comments.

## 3. Standards and Rule Profiles

Real parking layouts depend on local rules. The JSON should not pretend one set
of dimensions works everywhere.

The first version should not hard-code legal compliance. It should only record
which standard profile the user intends to apply.

```json
{
  "standards": {
    "jurisdiction": "CN",
    "standard_profile": "custom",
    "strictness": "conservative",
    "notes": "Use project-specific dimensions until local code profiles exist."
  }
}
```

Suggested fields:

```text
jurisdiction
  Country, region, city, or project-specific code context.

standard_profile
  Named rule set. Early values may be custom, conservative, or project_default.

rule_profile
  Executable OpenParkCAD rule-profile registry id. Unknown explicit ids are
  rejected; if omitted, the built-in default profile is used. This is distinct
  from the legacy free-form `standard_profile` metadata field.

strictness
  relaxed, normal, or conservative. This should influence default margins and
  penalties, not override explicit hard constraints.
```

Important: until the project has verified rule libraries, standards should be
metadata and default guidance, not a claim of legal compliance. The executable
delivery profile `private_surface_lot_v1` (`rule-profile-1`) only names checks
the software actually runs. Selecting it does not apply a jurisdiction code.
Unknown profile ids fail closed. See
[supported delivery scope](supported_delivery_scope.md).

## 4. Site Geometry

The site should support straight edges and curves. First-edition DXF import
(`import-dxf`) only accepts closed straight modelspace polylines and directed
LINE entrances; see [the CAD import contract](cad_import_contract.md).

### 4.1 Why Curves Matter

Real land parcels often include:

- arc-shaped curbs,
- rounded corners,
- road frontage curves,
- landscape islands,
- irregular boundary survey lines.

If the input only accepts polygon points, curves must be approximated manually.
That is workable but unpleasant. The JSON should allow curve intent while still
letting the algorithm internally convert curves to polylines.

### 4.2 Proposed Geometry Format

Use a boundary made of segments:

```json
{
  "site": {
    "boundary": {
      "type": "curve_loop",
      "segments": [
        {"type": "line", "from": [0, 0], "to": [60, 0]},
        {"type": "arc", "from": [60, 0], "to": [66, 6], "radius": 6, "clockwise": false},
        {"type": "line", "from": [66, 6], "to": [54, 42]},
        {"type": "line", "from": [54, 42], "to": [0, 36]},
        {"type": "line", "from": [0, 36], "to": [0, 0]}
      ]
    }
  }
}
```

Also allow a simpler polygon form for easy manual input:

```json
{
  "site": {
    "boundary": {
      "type": "polygon",
      "points": [[0, 0], [60, 0], [60, 36], [0, 36]]
    }
  }
}
```

Supported geometry object types should eventually include:

```text
polygon
curve_loop
circle
rectangle
polyline_buffer
```

Phase 0 can document all of these while only implementing `polygon` first.

### 4.3 Curve Tessellation

Algorithms like Shapely work best with polygons. Curves should be converted into
short segments before geometry validation.

Suggested settings:

```json
{
  "constraints": {
    "geometry": {
      "curve_tolerance": 0.05,
      "max_curve_segment_angle_degrees": 5
    }
  }
}
```

Meaning:

- `curve_tolerance`: maximum geometric deviation from the true curve.
- `max_curve_segment_angle_degrees`: maximum angle per generated arc segment.

## 5. Obstacles, Reserved Areas, and Site Features

The site should support multiple area classes:

```json
{
  "site": {
    "obstacles": [
      {
        "id": "building-1",
        "type": "building",
        "geometry": {"type": "polygon", "points": [[20, 10], [30, 10], [30, 18], [20, 18]]},
        "clearance": 0.5
      }
    ],
    "reserved_areas": [
      {
        "id": "landscape-1",
        "type": "landscape",
        "geometry": {"type": "circle", "center": [42, 22], "radius": 3}
      }
    ]
  }
}
```

Current v0.3 behavior:

- polygon obstacles are hard no-go areas by default and use the larger of their
  declared clearance and `constraints.setbacks.obstacle`;
- reserved areas use `parking_allowed`, `vehicle_allowed`, and `affects` to
  select stall, aisle, and swept-path exclusion scopes;
- hard clearances expand the exclusion geometry;
- `authority: advisory` or an advisory/soft/draw-only/future `priority` makes a
  declaration non-blocking; and
- malformed, out-of-bound, or unsupported hard definitions fail closed instead
  of disappearing from the report.

### 5.1 Site Features

Some fixed objects are not full obstacles, but they affect clearance, visibility,
or maneuvering. These should be represented separately from generic obstacles.

Examples:

```text
columns
charging stations
wheel stops
curbs
guardrails
gates
barriers
sign posts
lighting poles
height limit bars
toll or payment equipment
```

Proposed shape:

```json
{
  "site_features": [
    {
      "id": "column-a12",
      "type": "column",
      "geometry": {"type": "circle", "center": [18, 12], "radius": 0.35},
      "clearance": 0.3,
      "affects": ["vehicle_clearance", "door_clearance"]
    },
    {
      "id": "gate-arm-1",
      "type": "gate_arm",
      "geometry": {"type": "rectangle", "origin": [6, 0], "width": 4.5, "height": 0.3, "rotation_degrees": 0},
      "affects": ["entrance_throat", "queueing"]
    }
  ]
}
```

Why keep this separate:

- not every fixed object fully blocks all movement,
- some objects only affect one side of a stall,
- some objects create clearance buffers instead of no-go polygons,
- diagnostics can explain that a stall failed because of a column, post, or curb.

## 6. Entrances and Exits

An entrance should not be just a point. It should describe a boundary opening or
driveway throat.

Proposed shape:

```json
{
  "entrances": [
    {
      "id": "main-gate",
      "mode": "shared",
      "center": [8, 0],
      "width": 7.0,
      "side_hint": "south",
      "heading_degrees": 90,
      "allowed_movements": ["enter", "exit"]
    }
  ]
}
```

Entrance modes:

```text
shared
  The same opening can be used to enter and exit.

entry_only
  Vehicles may enter but not exit.

exit_only
  Vehicles may exit but not enter.
```

Alternative for exact boundary-based input:

```json
{
  "id": "east-exit",
  "mode": "exit_only",
  "edge": [[60, 12], [60, 20]],
  "width": 8.0,
  "heading_degrees": 180
}
```

Important questions:

- Should an entrance be allowed anywhere on the boundary, or must it snap to the
  nearest boundary segment?
- If a user gives `center`, should the solver infer the boundary cut line?
- Should a shared entrance require two-way capacity, or can it be a controlled
  single-lane gate?

Suggested Phase 0 rule:

- accept `center`, `width`, `heading_degrees`, and `mode`,
- draw it in preview,
- do not yet cut the boundary geometry automatically.

## 7. Pedestrian and Emergency Requirements

Parking lots include people and emergency access. These should not be mixed into
ordinary vehicle aisles.

Proposed shape:

```json
{
  "pedestrian_and_emergency": {
    "pedestrian_routes": [
      {
        "id": "walkway-main",
        "geometry": {"type": "polyline_buffer", "points": [[0, 18], [60, 18]], "width": 1.8},
        "priority": "hard"
      }
    ],
    "accessible_routes": [
      {
        "id": "accessible-path-1",
        "connects": ["accessible-stalls", "building-entry"],
        "min_width": 1.5,
        "max_slope": 0.083
      }
    ],
    "fire_lanes": [
      {
        "id": "fire-lane-1",
        "geometry": {"type": "polyline_buffer", "points": [[4, 0], [4, 36]], "width": 4.0},
        "min_turning_radius": 9.0,
        "parking_allowed": false
      }
    ],
    "emergency_access_required": false
  }
}
```

Current v0.3 behavior:

- hard pedestrian, accessible, fire, and access-route geometries become scoped
  stall/aisle/swept-path exclusions;
- advisory/future routes remain visible but non-blocking;
- `emergency_access_required` requires hard fire/access geometry, and a positive
  accessible quota requires hard accessible-route geometry;
- classified accessible stalls must geometrically reach a hard accessible route
  when `accessible_min` is positive (`constraints.accessible_route_touch_tolerance`,
  default 1.5 m);
- hard fire/access routes must geometrically reach an entrance gate when
  `emergency_access_required` is set (`constraints.emergency_route_touch_tolerance`,
  default 1.0 m);
- classified EV stalls must geometrically reach a placed charging post when
  `ev_min` is positive and hard `charging_post` / `ev_charger` site features
  exist (`constraints.ev_charger_touch_tolerance`, default 2.0 m);
- serving accessible-route pieces must form one contact network, and
  `accessible_routes.connects` ids (except reserved stall tokens) must name
  existing site feature / reserved / entrance / route geometry that the
  serving network can reach;
- declared `min_width` is checked against `polyline_buffer` width; other
  geometries fail closed as unauditable; declared `max_slope` fail-closes
  because elevation is not modeled; and
- ADA path graphs, pedestrian conflict scoring, electrical equipment design,
  and fire-apparatus swept paths remain future work.

This module is important because fire access, accessible routes, and pedestrian
safety often override stall-count optimization.

## 8. Parking Stall Information

Parking settings should describe allowed stall families, dimensions, and whether
they are fixed or optional.

```json
{
  "parking": {
    "stall_types": [
      {
        "id": "standard-90",
        "family": "perpendicular",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [90],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": true
      },
      {
        "id": "standard-angled",
        "family": "angled",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [45, 60],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": false
      },
      {
        "id": "parallel",
        "family": "parallel",
        "width": 2.2,
        "length": 6.0,
        "drive_over": false,
        "access_sides": ["left", "right"],
        "enabled": false
      },
      {
        "id": "t-end",
        "family": "t_end",
        "width": 2.5,
        "length": 5.3,
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": false
      },
      {
        "id": "painted-flex",
        "family": "painted",
        "width": 2.5,
        "length": 5.3,
        "drive_over": true,
        "access_sides": ["front", "back", "left", "right"],
        "enabled": false
      },
      {
        "id": "ev-front-post",
        "family": "perpendicular",
        "width": 2.6,
        "length": 5.5,
        "drive_over": false,
        "access_sides": ["front"],
        "blocked_sides": ["back"],
        "fixed_features": [
          {
            "type": "charging_post",
            "side": "back",
            "offset": 0.5,
            "clearance": 0.4
          }
        ],
        "enabled": false
      }
    ],
    "quotas": {
      "accessible_min": 0,
      "ev_min": 0
    }
  }
}
```

Important distinction:

- `family` controls the maneuver model.
- `allowed_angles` controls generation choices.
- `enabled` tells the solver whether it may use that type.
- `drive_over` says whether other vehicles may pass across the marked stall
  surface when it is empty. Most normal stalls should be `false`; some painted
  flexible areas may be `true`.
- `access_sides` says which sides may be used to enter or leave the stall.
- `blocked_sides` says which sides cannot be used because of walls, charging
  posts, curbs, columns, or other fixed objects.
- `fixed_features` retains stall-local equipment metadata; charging-type
  features contribute to EV classification, but are not yet placed as physical
  collision geometry inside each generated stall.
- `classifications` is the canonical v0.3 list used to identify explicit
  `accessible` and `ev` stall types for quota accounting; `qualifications` and
  `tags` are accepted as compatibility aliases.
- v0.3 enforces non-negative integer `accessible_min` and `ev_min` counts on the
  final layout. An unknown positive quota key fails closed; no statutory default
  is inferred.

### 8.1 Stall Access and Pass-Through Behavior

Not all marked parking rectangles behave the same way.

Some spaces are only painted on pavement. When empty, a vehicle may be able to
drive across them in a low-speed maneuver. Other spaces have physical equipment
or obstacles at one side, such as:

- charging posts,
- wheel stops,
- walls,
- columns,
- curbs,
- landscape strips,
- guardrails.

These details change maneuver validity. A stall with a charging post at the back
may only be usable from the aisle-facing front side. A parallel space along a
curb may only be entered from the traffic side. A painted flexible area may be
allowed as temporary pass-through space, but should not be treated like a normal
aisle.

Recommended model:

```text
drive_over
  Whether the stall footprint can be used as temporary drivable surface when
  empty. This is a traffic/maneuver property, not a parking-count property.

access_sides
  Sides from which the design vehicle may enter or leave.

blocked_sides
  Sides blocked by fixed equipment or physical barriers.

fixed_features
  Objects attached to the stall that create clearance or access restrictions.
```

Suggested side names:

```text
front
  The normal aisle-facing side.

back
  The side opposite the aisle.

left / right
  The lateral sides, relative to the stall's local orientation.
```

This distinction matters for optimization:

- a drive-over painted stall may help a maneuver but cannot be assumed available
  if occupied,
- a stall with a blocked side needs a different maneuver envelope,
- a charging stall may need extra clearance around the post,
- a one-sided stall should not be placed where the only usable approach is on
  the blocked side.

## 9. Vehicle Information

Vehicle rules should be explicit. A stall is not valid if the design vehicle
cannot realistically reach and use it.

```json
{
  "vehicles": {
    "design_vehicle": {
      "id": "passenger-car",
      "length": 4.8,
      "width": 1.9,
      "wheelbase": 2.8,
      "min_turning_radius": 5.5,
      "turning_radius_reference": "outer_front_wheel",
      "track_width": 1.6,
      "front_overhang": 1.0,
      "rear_overhang": 1.0,
      "swept_path_margin": 0.3,
      "max_reverse_distance": 12.0,
      "configuration": "rigid"
    }
  }
}
```

Articulated vehicles add a hitch and trailer. Presence of `trailer` implies
`configuration: "articulated"` when the field is omitted:

```json
{
  "vehicles": {
    "design_vehicle": {
      "id": "wb-15",
      "configuration": "articulated",
      "length": 6.2,
      "width": 2.55,
      "wheelbase": 3.8,
      "min_turning_radius": 12.0,
      "turning_radius_reference": "outer_front_wheel",
      "track_width": 2.0,
      "front_overhang": 1.4,
      "rear_overhang": 1.0,
      "hitch_offset": 0.5,
      "swept_path_margin": 0.3,
      "max_reverse_distance": 40.0,
      "trailer": {
        "length": 13.6,
        "width": 2.55,
        "wheelbase": 8.0,
        "front_overhang": 1.5,
        "rear_overhang": 4.1
      }
    }
  }
}
```

`hitch_offset` is the tractor-rear-axle to hitch distance, positive toward the
rear. If omitted, conservative checks assume zero and report that assumption.
Unknown `configuration` values without a trailer fail closed.

`turning_radius_reference` is required to interpret the declared radius:

- `rear_axle_center` uses `min_turning_radius` directly; and
- `outer_front_wheel` converts it with wheelbase and track width using
  `R_rear = sqrt(R_outer^2 - wheelbase^2) - track_width/2`.

Hard checks require auditable inputs; missing track width cannot silently fall
back to an assumed value. Explicit front/rear overhangs must sum with wheelbase
to the vehicle length. If omitted, the non-wheelbase length is split evenly and
that assumption is reported.

With `require_swept_path_check: false`, requested turning/reverse checks use the
`active_conservative` analytic mode: radius conversion, vehicle/stall fit, and a
quarter-turn-plus-stall reverse upper bound. With it set to `true`,
`active_exact` supports perpendicular-90 reverse-in, acute-angled reverse-in,
parallel reverse S-curve, and T-end reverse-in from the serving turnaround.
Perpendicular, angled, and T-end templates integrate a constant-curvature
bicycle arc and straight segment; parallel uses two equal-radius opposite
reverse arcs. Collision uses a conservative envelope between sampled body
poses. Unsupported templates (obtuse approaches, multi-leg site paths, articulated
vehicles), missing parameters, boundary/centerline/hard-exclusion conflicts, and
excessive reverse distance fail closed. Articulated vehicles never run the rigid
bicycle stall templates. Without `require_swept_path_check`, they use a
conservative analytic: combination length/width versus the stall, steady-state
trailer off-tracking versus declared aisle width, and a tractor quarter-arc plus
trailer-length reverse bound.

## 10. Aisle Information

This is the most important modeling question: should narrow and wide aisles be
given by the user, or should the solver choose them?

The answer should be: both, depending on the field.

### 10.1 Aisle Classes

The input should define aisle classes:

```json
{
  "aisles": {
    "classes": [
      {
        "id": "narrow-one-way",
        "width": 3.5,
        "capacity": "single_vehicle",
        "directionality": "one_way",
        "centerline_crossing": "not_applicable",
        "enabled": false
      },
      {
        "id": "wide-two-way-no-cross",
        "width": 6.0,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "forbidden",
        "enabled": true
      },
      {
        "id": "wide-two-way-cross",
        "width": 6.5,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "allowed",
        "enabled": true
      }
    ],
    "selection_mode": "optimize"
  }
}
```

Suggested fields:

```text
width
  Physical aisle width.

capacity
  single_vehicle or two_vehicle.

directionality
  one_way or two_way.

centerline_crossing
  allowed, forbidden, or not_applicable. `not_applicable` is treated as allowed
  by the current two-way maneuver template.

enabled
  Whether the solver may use this aisle class.

selection_mode
  How aisle classes are chosen.
```

### 10.2 Aisle Selection Modes

There are three useful modes.

#### fixed

The user says exactly which aisle class to use.

```json
{
  "aisles": {
    "selection_mode": "fixed",
    "fixed_class": "wide-two-way-no-cross"
  }
}
```

Use this when:

- the project has a strict standard,
- the user wants predictable output,
- early algorithm phases need reduced complexity.

#### allowed

The user gives a set of allowed classes. The solver may use any of them, but it
does not necessarily optimize between them deeply.

```json
{
  "aisles": {
    "selection_mode": "allowed",
    "allowed_classes": ["wide-two-way-no-cross", "wide-two-way-cross"]
  }
}
```

Use this when:

- the user wants to ban unsafe types,
- the solver can try a small set of variants.

#### optimize

The solver treats aisle class as a design variable.

```json
{
  "aisles": {
    "selection_mode": "optimize"
  }
}
```

Use this when:

- the solver is mature enough to compare tradeoffs,
- the scoring model includes safety and operational penalties,
- narrow aisles and wide aisles may be mixed.

Phase 0 should support documenting all three modes. Phase 1 should probably only
implement `fixed` or a restricted `allowed` mode for wide two-way aisles.

### 10.3 Narrow vs Wide Aisle Recommendation

Do not let early versions freely optimize narrow two-way aisles.

Reason:

- a narrow two-way aisle can maximize stall count while creating head-to-head
  deadlocks,
- a solver that only counts geometry will overuse it,
- narrow two-way aisles require passing-bay, visibility, priority, or short-run
  rules.

Recommended first policy:

```text
Phase 0
  Represent narrow and wide aisle classes.

Phase 1
  Only generate wide two-way aisles.

Phase 2
  Add one-way narrow aisles as directed graph edges.

Phase 5
  Consider narrow two-way aisles only with deadlock and passing-bay checks.
```

## 11. Constraints

Constraints should include hard rules that invalidate a layout.

```json
{
  "constraints": {
    "setbacks": {
      "site_boundary": 0.2,
      "obstacle": 0.3
    },
    "circulation": {
      "require_all_stalls_reachable": true,
      "require_exit_path": true,
      "allow_dead_end_aisles": false,
      "max_dead_end_length": 0,
      "allow_narrow_two_way": false
    },
    "maneuvering": {
      "require_turning_radius_check": true,
      "require_swept_path_check": false,
      "max_reverse_distance": 12.0
    },
    "road_traversal": {
      "enabled": false,
      "scope": "site_interior",
      "time_budget_seconds": 10.0
    }
  }
}
```

`constraints.road_traversal.enabled=true` requests a site-interior journey for
every retained stall. Missing the block or `enabled=false` is `not_requested`
and does not change existing validity. Official publication requires
`status=passed` and `valid=true` together. See
[the road-traversal contract](road_traversal_contract.md).

These maneuvering requests are active in v0.3 for supported perpendicular-90 reverse-in,
acute-angled reverse-in, parallel reverse S-curve, and T-end reverse-in stalls.
A swept path or reverse-distance request also
activates turning radius as a prerequisite for constructing/bounding the path;
`declared_requests` preserves what the caller explicitly set. The stricter of
the vehicle and constraint reverse-distance limits is used. If a hard request
cannot run, the layout is invalid; legacy rectangular/L-shaped proxies are not
a fallback pass.

## 12. Optimization Preferences

Optimization settings should not be mixed with hard constraints.

```json
{
  "optimization": {
    "objective": "balanced",
    "weights": {
      "stall_count": 100.0,
      "aisle_area": -1.0,
      "dead_end": -30.0,
      "reverse_distance": -5.0,
      "conflict_point": -10.0,
      "stall_family": {
        "perpendicular": 100.0,
        "parallel": 80.0,
        "angled": 90.0
      },
      "segment_family_mix": 0.0
    },
    "prefer_uniform_segments": false,
    "connector_allow_l_shape_end_stalls": true,
    "maneuver_l_shape_fallback": true
  }
}
```

Suggested objectives:

```text
max_stalls
  Prefer stall count, still respecting hard constraints.

balanced
  Balance stall count, circulation quality, and ease of use.

conservative
  Penalize operational risk heavily.
```

`weights.stall_family` optionally values perpendicular / parallel / angled /
t_end stalls differently. `weights.segment_family_mix` (or
`prefer_uniform_segments`) is a per-aisle-side mix penalty; 0 keeps independent
per-segment family picks. Neither is a statutory mix rule.

## 13. Diagnostics

A useful design tool should explain what it checked and what it skipped.

```json
{
  "diagnostics": {
    "explain_rejections": true,
    "export_debug_layers": true,
    "include_unsupported_fields": true,
    "report_constraint_status": true
  }
}
```

Suggested diagnostic outputs:

```text
accepted stalls
rejected stall candidates with reasons
aisle candidates
entrance connection status
unsupported input fields
active vs inactive constraints
clearance conflicts
maneuver conflicts
```

This matters because early versions will document more fields than they can fully
use. Reports must be honest about which rules are active.

## 14. Minimal Phase 0 Example

```json
{
  "version": "0.1",
  "name": "phase0 minimal site",
  "units": "m",
  "standards": {
    "jurisdiction": "CN",
    "standard_profile": "custom",
    "strictness": "conservative"
  },
  "site": {
    "boundary": {
      "type": "polygon",
      "points": [[0, 0], [60, 0], [60, 36], [0, 36]]
    },
    "obstacles": []
  },
  "entrances": [
    {
      "id": "main",
      "mode": "shared",
      "center": [8, 0],
      "width": 7.0,
      "heading_degrees": 90,
      "allowed_movements": ["enter", "exit"]
    }
  ],
  "site_features": [],
  "pedestrian_and_emergency": {
    "pedestrian_routes": [],
    "fire_lanes": [],
    "emergency_access_required": false
  },
  "vehicles": {
    "design_vehicle": {
      "id": "passenger-car",
      "length": 4.8,
      "width": 1.9,
      "wheelbase": 2.8,
      "min_turning_radius": 5.5,
      "turning_radius_reference": "outer_front_wheel",
      "track_width": 1.6,
      "front_overhang": 1.0,
      "rear_overhang": 1.0,
      "swept_path_margin": 0.3,
      "max_reverse_distance": 12.0
    }
  },
  "parking": {
    "stall_types": [
      {
        "id": "standard-90",
        "family": "perpendicular",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [90],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": true
      }
    ]
  },
  "aisles": {
    "selection_mode": "fixed",
    "fixed_class": "wide-two-way-no-cross",
    "classes": [
      {
        "id": "wide-two-way-no-cross",
        "width": 6.0,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "forbidden",
        "enabled": true
      }
    ]
  },
  "constraints": {
    "circulation": {
      "require_all_stalls_reachable": true,
      "require_exit_path": true,
      "allow_dead_end_aisles": false,
      "allow_narrow_two_way": false
    }
  },
  "optimization": {
    "objective": "conservative",
    "promote_candidate_layout_preview": false
  },
  "diagnostics": {
    "explain_rejections": true,
    "export_debug_layers": true,
    "report_constraint_status": true
  }
}
```

## 15. Phase Support Matrix

The JSON is intentionally future-facing, while implementation remains
incremental. This matrix records the current implementation boundary; see
[Current status](current_status.md) for the concise trust model.

```text
Field or module                         Current implementation status
------------------------------------------------------
version / name / units                  active
standards                               metadata; required source/profile/effective date for jurisdictional declarations
site.boundary polygon                   active
site.boundary curve_loop / arc          documented, future
site.obstacles polygon                  active hard exclusion with effective clearance
site.reserved_areas                     active for supported geometry and declared stall/aisle/swept scopes
site_features                           active for declared hard scopes; advisory shapes remain non-blocking; passing bays feed Phase 5Q
entrances center / width / heading      active for straight main aisle
pedestrian / accessible routes          active geometric exclusions when hard
accessible stall-to-route reachability  active when accessible_min > 0; geometric contact only
accessible-route continuity             active when accessible_min > 0; piece contact + connects destinations
accessible-route min_width / max_slope  active when declared; polyline width audited, slope unsupported without elevation
accessible/EV contact filter            active when quota + contact geometry exist; drops unreachable classified stalls
accessible/EV contact retarget          active when quota + classified type + contact geometry; converts adjacent same-family stalls, may drop overlapping same-side neighbors, pack a contiguous contact run, or fill empty contact-band pavement on an existing aisle (perpendicular, parallel, or angled)
emergency route-to-entrance             active when emergency_access_required; geometric contact only
EV stall-to-charger reachability        active when ev_min > 0 and hard charging posts are placed
fire / emergency access routes          active geometric exclusions when hard; no fire-apparatus model
parking.stall_types standard-90         active
parking stall classifications           active for accessible/EV quota identity
parking accessible_min / ev_min quotas  active hard minimums on final layout
parking angled maneuver rule            active proxy for generated main/branch stalls
parking angled main-aisle generation    active, main aisle only
parking angled branch generation        active
parking angled connector generation     active official connector-side angled stalls
parking parallel main/branch generation active
parking parallel maneuver proxy         active traffic-side rectangle; exact S-curve is a separate vehicle check
parking parallel connector              active official connector-side parallel stalls
parking t_end main/branch generation    active dead-end end-cap bays on straight, dogleg, and multi-jog rear turnarounds; exact swept path insets far edge by swept_path_margin
parking t_end caps on other families    active via optimization.enable_t_end_caps
parking t_end maneuver proxy            active front-access turnaround rectangle
parking maneuver rule dispatch          active for active/future rule reporting
parking enabled stall type candidates   active for enumerative comparison
stall drive_over                        documented, future maneuver rule
stall access_sides / blocked_sides      active for parallel traffic-side access; otherwise future
vehicles design_vehicle                 active when requested; otherwise parsed/available
vehicles articulated                    parsed trailer/hitch; conservative analytic when requested; exact swept path fail-closed
vehicle outer-front-wheel radius        active audited conversion; explicit track width required for hard check
vehicle rear-axle-center radius          active direct reference
vehicle conservative analytic check     active optional turning/fit/reverse upper-bound mode
vehicle articulated analytic check      active combination fit + trailer off-tracking + tractor-arc-plus-trailer reverse bound
vehicle parallel analytic check         active fit + reverse length bound; exact S-curve when swept_path is requested
vehicle t_end analytic check            active fit + reverse-in length bound; exact when swept_path is requested
vehicle perpendicular-90 swept template active optional exact-path/conservative-envelope mode
vehicle angled reverse-in swept template active optional exact-path/conservative-envelope mode
vehicle angled analytic check           active scaled-arc fit + reverse bound; exact when swept_path is requested
vehicle parallel reverse S-curve        active optional two-arc exact-path/conservative-envelope mode
vehicle T-end reverse-in swept template active straight reverse from turnaround/parent court; 90-degree fallback
aisles fixed wide two-way               active first
aisles fixed one-way (incl. narrow)     active generation + direction-aware graph
aisles narrow two-way                   active when enable_passing_bay_synthesis (or auto with operational_min_passing_bays)
aisles passing-bay synthesis            active via enable_passing_bay_synthesis; role=passing_bay + site_features; non-circulation graph
optimization.passing_bay_length/width/spacing  active optional bay geometry/spacing
entrances dual entry/exit attach        active via enable_dual_entrance (default on) when a far exit-capable gate exists
entrances dogleg dual-exit corridors    active; turnaround-relative lateral + optional L-elbow; optimization.exit_lateral_budget optional
aisles main lateral offset candidates   active via enable_main_aisle_lateral_offsets / main_aisle_lateral_offsets; also auto when prefer_obstacle_clearance or auto_lateral_offsets_for_obstacles
aisles main dogleg bypass               active via enable_main_aisle_dogleg, or auto when obstacles + prefer_obstacle_clearance; mode phase1_main_aisle_dogleg
aisles main multi-jog bypass            active under dogleg enablement when ≥2 jogs improve depth; mode phase1_main_aisle_multi_jog
optimization.max_dogleg_jogs            active cap on chained jogs (default 3; multi-jog needs ≥2)
aisles multi-spine branches             active when enable_branches; all dogleg/multi-jog main segments share max_branches; connectors scoped per parent
aisles dogleg multi-spine branches      active (alias of multi-spine branches for front/rear dogleg)
optimization.dogleg_offsets             active optional lateral offset list for dogleg/multi-jog spines (±aisle widths default)
optimization.enable_adaptive_dogleg_offsets  active; default on with obstacles + dogleg/clearance preference; merges envelope-based offsets
optimization.auto_lateral_offsets_for_obstacles  active opt-in (also implied by prefer_obstacle_clearance)
optimization.prefer_obstacle_clearance  active opt-in score weight + lateral offset fan-out (+ dogleg auto-enable)
optimization.obstacle_clearance_weight  active intensity when prefer_obstacle_clearance (default 10; weights.* override)
optimization.obstacle_clearance_cap     active metres cap for clearance metric (default 12)
branch clip diagnostics                 active on branch candidates (clear_length / open_boundary_length / clip_amount)
optimization.branch_clear_length_bonus  active soft greedy-branch bonus per clear metre (default 0.35)
optimization.branch_clip_penalty        active soft greedy-branch penalty per clipped metre (default 0.2)
optimization.branch_clipped_side_penalty active soft penalty when side is clipped and opposite is freer
aisles same-side U connector            active
aisles opposite-side cross connector    active via enable_opposite_connectors (default on)
aisles opposite-side end-loop           active via enable_opposite_end_loops (default with opposite)
optimization.prefer_loops               active opt-in connector loop score bonus
optimization.prefer_obstacle_clearance  active opt-in clearance score bonus
constraints.circulation.one_way_allows_reverse_egress  active (default true) for stall egress on one-way modules
aisles narrow two-way                   active opt-in with passing-bay synthesis (deadlock still proxy-only via Phase 5Q)
constraints entrance-to-main-aisle      active first
constraints dead-end turnaround         active first
constraints stall-to-aisle association  active first
constraints phase1 aisle connectivity   active as simple parent links
constraints full graph circulation      active for generated Phase 2A graph
constraints maneuvering access envelope active as Phase 3A proxy
constraints turning sweep proxy         active as Phase 3B proxy
constraints maneuver rule support       active for Phase 3C-1 dispatch
constraints exact turning radius        active for supported vehicle mode
constraints swept path                  active for perpendicular-90, acute-angled, parallel S-curve, and T-end reverse-in
constraints reverse distance            active exact measurement or conservative upper bound
constraints scoped hard exclusions      active for stall, aisle, and swept-path purposes
constraints authority / priority        active and report-visible; no built-in jurisdictional rules
optimization heading_deltas_degrees     active for main aisle heading trials
optimization entrance_offsets           active for entrance-width offset trials
optimization branch_start_positions     active for perpendicular branch trials
optimization branch_start_step          active for branch start auto-sampling
optimization branch_sides               active for branch side filtering
optimization max_branches               active for limited multi-branch search
optimization.selector_backend           active greedy default; cpsat uses OR-Tools when installed, else fail-closes to greedy
optimization.selector_num_workers       optional CP-SAT workers; omitted keeps OR-Tools default; illegal values are input errors
optimization.layout_search              optional; default mode=legacy. mode=multi_spine compares complete spines (Top-K, extra budget). Illegal values are input errors, not silent legacy.
optimization stall modules              active per-side strips (`stall_module`); depend on parent aisle; preview uses selected modules
optimization stall module families      active on spine, branch, and connectors; at most one stall_type per family_slot (side or segment)
optimization.stall_module_segment_stalls  active default 4 stalls per module; explicit 0 = whole side
optimization.mixed_segment_scoring      active when multiple families, stall_family weights, or mix penalty requested
optimization.weights.stall_family       optional per-family stall value; default is weights.stall_count / selector 100
optimization.weights.segment_family_mix optional penalty per mixed aisle-side; prefer_uniform_segments sets -50
optimization.weights.accessible_contact extra shadow points per reachable accessible stall (default 100)
optimization.weights.ev_contact         extra shadow points per reachable EV stall (default 100)
optimization.selector_time_limit_seconds  active for CP-SAT (default 2)
optimization.selector_seed              active optional CP-SAT random seed
optimization discrete candidate catalog active: official aisles=base, branch/connector skeletons=variable
optimization enable_connectors          active for same-side branch connectors
optimization connector_allow_outer_stall_row active for inset U-connector placement
optimization connector_inset_depths     active for connector setback candidate search
optimization connector_throat_length    active for connector-side stall clearance
optimization connector_allow_l_shape_end_stalls active for connector end-stall candidates
optimization maneuver_access_depth      active for Phase 3A access envelope
optimization maneuver_access_coverage_ratio active for Phase 3A access envelope
optimization maneuver_turn_buffer_length active for Phase 3B turn proxy
optimization maneuver_turn_coverage_ratio active for Phase 3B turn proxy
optimization maneuver_l_shape_fallback  active for 90-degree one-sided turn proxy
optimization maneuver_angled_access_depth active for angled validator proxy
optimization maneuver_angled_turn_buffer_length active for angled validator proxy
optimization maneuver_angled_access_coverage_ratio active for angled validator proxy
optimization maneuver_angled_turn_coverage_ratio active for angled validator proxy
optimization operational_junction_clearance_radius active for Phase 5A soft-risk report
optimization operational_entrance_clearance_radius active for Phase 5A soft-risk report
optimization operational_quality_mode   active for Phase 5B score/gate/reject mode
optimization operational_max_risk_score active for Phase 5B risk threshold
optimization operational_max_route_length active for Phase 5C route risk threshold
optimization operational_turnaround_dependency_risk active for Phase 5C turnaround dependency penalty
optimization operational_max_turnaround_dependency_ratio active for Phase 5E route-summary risk threshold
optimization operational_turnaround_dependency_ratio_risk active for Phase 5E route-summary risk score
optimization operational_max_average_route_length active for Phase 5F average route threshold
optimization operational_average_route_length_risk active for Phase 5F average route risk score
optimization operational_max_long_route_ratio active for Phase 5F long-route ratio threshold
optimization operational_long_route_ratio_risk active for Phase 5F long-route ratio risk score
optimization operational_directionality_issue_risk active for Phase 5G per-stall directionality risk
optimization operational_max_directionality_issue_ratio active for Phase 5G directionality issue ratio threshold
optimization operational_directionality_issue_ratio_risk active for Phase 5G directionality ratio risk score
optimization operational_narrow_two_way_issue_risk active for Phase 5H per-stall narrow two-way exposure risk
optimization operational_max_narrow_two_way_stall_ratio active for Phase 5H narrow two-way stall ratio threshold
optimization operational_narrow_two_way_stall_ratio_risk active for Phase 5H narrow two-way ratio risk score
optimization operational_min_passing_bays active for Phase 5J usable passing bay shortage threshold
optimization operational_passing_bay_shortage_risk active for Phase 5J usable passing bay shortage risk score
optimization operational_passing_bay_touch_tolerance active for Phase 5J passing bay-to-aisle association tolerance
optimization operational_min_passing_bay_area active for Phase 5J passing bay geometry area threshold
optimization operational_passing_bay_geometry_issue_risk active for Phase 5J unusable passing bay geometry risk score
optimization operational_max_passing_bay_spacing active for Phase 5K passing bay spacing threshold
optimization operational_passing_bay_spacing_risk active for Phase 5K passing bay spacing risk score
optimization operational_narrow_two_way_meeting_gap_risk active for Phase 5M narrow two-way meeting risk score
optimization operational_narrow_two_way_junction_merge_risk active for Phase 5Q narrow two-way junction merge risk score
optimization operational_pedestrian_conflict_risk available Phase 5R per-issue pedestrian proximity/crossing risk (default 0)
optimization operational_pedestrian_conflict_clearance available metres beyond walkway (default 0 = contact only)
optimization weights.operational_risk active for Phase 5Q soft-risk scoring
optimization weights                    active for Phase 1 scoring
optimization score breakdown            active in JSON report
optimization promote_candidate_layout_preview active as guarded opt-in; official aisle IDs rebuilt from catalog source_id
report aisles / stalls traceability      active in JSON report
report selected_branches                active in JSON report
report selected_connectors              active, includes removed and added stalls
report traffic_graph validation          active in JSON report
report maneuver_validation              active in JSON report
report maneuver_validation rule_counts  active in JSON report
report vehicle_validation               active/not_requested/active_failed with per-stall mode and provenance
report site_constraint_validation       active with definitions, authority, conflicts, and quotas
report engineering_validation           active combined versioned decision with active/advisory/unsupported/failed rules
report operational_quality              active as Phase 5R local, route, summary, directionality, narrow two-way, passing bay, junction-merge, and pedestrian-conflict proxy report
report selected_stall_type_id           active in JSON report
report stall_type_attempts              active in JSON report
report selected_stall_assignment        active in JSON report
report stall_assignment_attempts        active in JSON report
report candidate_snapshot               active in JSON report
report candidate_snapshot conflict_matrix active in JSON report
report candidate_snapshot selection     active as bundle-aware shadow selector report
report candidate connector counts       active in diagnostics
report candidate_network_preview        active as preview-only report
report candidate_network_preview validation active as preview-only report
report connector turnaround suppression active as preview-only report
report candidate shadow branch turnarounds active as preview-only network expansion
report candidate_layout_preview         active as preview-only report
report candidate_layout_preview validation active as preview-only report
report candidate_layout_preview scoring active as preview-only comparison
report candidate_layout_promotion       active in JSON report
report attempt graph rejection           active in JSON report
svg candidate_network_preview overlay   active as debug layer
diagnostics                             active as reporting structure
```

Recommended rule: if a field is accepted but not enforced, the report must say
so. Silent partial support is dangerous for a design tool.

For v0.3, support status also carries rule authority. `advisory` rules may warn
or score; active `project_policy` rules fail the solve when violated; a
`jurisdictional` rule must identify its external profile/source metadata but does
not make OpenParkCAD a compliance certifier. The top-level input `version` does
not activate a rule by itself. See the
[v0.3 vehicle and enforced-constraint contract](v0.3_vehicle_and_constraints.md)
for fail-closed vehicle, exclusion, quota, and report semantics.

Geometry declarations use a separate `priority`: `hard` participates in active
exclusion checks; `advisory`/`soft` is diagnostic, and `draw_only`/`future` is
not enforceable. `authority` answers where a rule came from, while `priority`
answers whether the current solve enforces it. These fields must not be
collapsed into one another.

## 16. Open Questions

These are retained as historical and remaining design questions. Current
decisions and supported behavior take precedence in `current_status.md`, the
runtime diagnostics, and regression tests.

1. Should entrance geometry be defined by `center + width + heading`, by boundary
   edge segment, or should both be supported?

2. Should the first implementation reject curve geometry, or tessellate arcs
   immediately?

3. Should centerline policy expand beyond the current aisle-class
   `allowed`/`forbidden`/`not_applicable` values to support time- or
   maneuver-specific rules?

4. Should the first valid layout require a loop, or is a short dead-end aisle
   acceptable if reverse distance is below a limit?

5. Should stall dimensions be global defaults, or should each stall type carry
   its own dimensions?

6. Should narrow two-way aisles be fully disabled until passing-bay logic exists?

7. Should `drive_over` become a richer enum such as `never`, `empty_only`,
   `maneuver_only`, and `always`, instead of a boolean?

8. How should geometric pedestrian/fire exclusions grow into route continuity,
   destination, width/slope, and emergency-apparatus checks without implying a
   built-in legal profile?

## 17. Current Next Step

The v0.3 parser, machine-readable Schema, vehicle fields, scoped exclusions, and
minimum quotas are implemented. The next input-model work should deepen coverage
without making the accepted vocabulary look more capable than runtime checks:

```text
permissioned survey/coordinate-system input and provenance
general or additional audited vehicle maneuver templates
pedestrian, accessible, and emergency route connectivity/usability
stall-local equipment geometry and access-side behavior
versioned standards profiles with traceable sources
```

New vocabulary should not be added without an explicit support status and a
report-visible failure mode. The release sequence is maintained in
[Roadmap](roadmap.md).
