from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from collections.abc import Callable
from typing import Any

from openparkcad import __version__
from openparkcad.candidate_layout_preview import candidate_layout_preview_report
from openparkcad.candidate_network_preview import candidate_network_preview_report
from openparkcad.candidate_snapshot import candidate_snapshot_report
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.exporter_dxf import write_dxf
from openparkcad.exporter_svg import write_svg
from openparkcad.generator import generate_layout
from openparkcad.layout_search import layout_search_report
from openparkcad.models import site_from_dict
from openparkcad.traffic_graph import traffic_graph_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openparkcad")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve_parser = subparsers.add_parser("solve", help="Generate a parking layout for a site JSON file.")
    solve_parser.add_argument("site", help="Path to site JSON")
    solve_parser.add_argument("--out", default="output/layout.dxf", help="DXF output path")
    solve_parser.add_argument("--preview", default="output/layout.svg", help="SVG preview path")
    solve_parser.add_argument("--report", default="output/report.json", help="JSON report path")
    solve_parser.add_argument(
        "--diagnostics",
        default=None,
        help="Optional independent rejection diagnostics path used only when no official layout is published.",
    )

    import_parser = subparsers.add_parser("import-dxf", help="Convert a support-range DXF into SiteSpec JSON without solving.")
    import_parser.add_argument("dxf", help="Source DXF path")
    import_parser.add_argument("--mapping", required=True, help="Layer and entrance-handle mapping JSON")
    import_parser.add_argument("--defaults", required=True, help="Project defaults JSON (metres)")
    import_parser.add_argument("--out", required=True, help="Output site JSON path")
    import_parser.add_argument("--diagnostics", required=True, help="Import diagnostics JSON path")

    solve_parser.add_argument(
        "--source-coordinates",
        action="store_true",
        help="Write the DXF in recorded source CAD coordinates and units.",
    )

    args = parser.parse_args(argv)
    if args.command == "solve":
        return _solve(args)
    if args.command == "import-dxf":
        return _import_dxf(args)
    raise ValueError(f"Unknown command: {args.command}")


def _import_dxf(args: argparse.Namespace) -> int:
    from openparkcad.cad_import import CadImportError, import_dxf_to_site, source_file_sha256

    dxf_path = Path(args.dxf)
    out_path = Path(args.out)
    diagnostics_path = Path(args.diagnostics)
    try:
        _require_distinct_output_paths([out_path, diagnostics_path, dxf_path])
    except ValueError as exc:
        return _error(str(exc), exit_code=2)
    before_hash = None
    try:
        before_hash = source_file_sha256(dxf_path)
    except OSError as exc:
        return _error(f"could not read DXF {dxf_path}: {exc}")
    try:
        site_json, diagnostics = import_dxf_to_site(dxf_path, args.mapping, args.defaults)
    except FileNotFoundError as exc:
        return _error(str(exc))
    except CadImportError as exc:
        try:
            _write_json(diagnostics_path, exc.diagnostics)
        except Exception as write_exc:
            return _error(f"invalid DXF import: {exc}; could not write diagnostics: {write_exc}")
        after_hash = source_file_sha256(dxf_path)
        if after_hash != before_hash:
            return _error("source DXF changed during a failed import", exit_code=4)
        return _error(f"invalid DXF import: {exc}", exit_code=2)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _error(f"invalid import input: {exc}")
    after_hash = source_file_sha256(dxf_path)
    if after_hash != before_hash:
        return _error("source DXF changed during import", exit_code=4)
    try:
        _write_json(out_path, site_json)
        _write_json(diagnostics_path, diagnostics)
    except Exception as exc:
        return _error(f"could not write import outputs: {exc}", exit_code=4)
    print(f"site: {out_path}")
    print(f"diagnostics: {diagnostics_path}")
    return 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _solve(args: argparse.Namespace) -> int:
    site_path = Path(args.site)
    try:
        data = json.loads(site_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _error(f"site file not found: {site_path}")
    except json.JSONDecodeError as exc:
        return _error(f"invalid JSON in {site_path} at line {exc.lineno}, column {exc.colno}")
    except (OSError, UnicodeError) as exc:
        return _error(f"could not read site file {site_path}: {exc}")

    if not isinstance(data, dict):
        return _error("invalid site input: top-level JSON value must be an object")

    try:
        site = site_from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        return _error(f"invalid site input: {exc}")

    try:
        layout = generate_layout(site)
    except ValueError as exc:
        return _error(f"could not generate layout: {exc}")

    validation_errors = _final_layout_errors(layout)
    if validation_errors:
        diagnostics_error = None
        if args.diagnostics:
            try:
                _write_rejection_diagnostics(
                    layout,
                    Path(args.diagnostics),
                    validation_errors,
                    official_paths=[Path(args.out), Path(args.preview), Path(args.report)],
                )
            except Exception as exc:
                diagnostics_error = f"could not write diagnostics: {exc}"
        message = f"no valid final layout: {'; '.join(validation_errors)}"
        if diagnostics_error:
            message = f"{message}; {diagnostics_error}"
        return _error(message, exit_code=3)

    try:
        _write_output_set(
            layout,
            dxf_path=Path(args.out),
            svg_path=Path(args.preview),
            report_path=Path(args.report),
            restore_source_coordinates=bool(getattr(args, "source_coordinates", False)),
        )
    except Exception as exc:
        return _error(f"could not write outputs: {exc}", exit_code=4)

    print(f"site: {site.name}")
    print(f"stalls: {layout.stall_count}")
    print(f"dxf: {args.out}")
    print(f"preview: {args.preview}")
    print(f"report: {args.report}")
    return 0


def _write_report(layout, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "report_contract_version": "openparkcad-report-0.3",
        "package_version": __version__,
        "site": layout.site.name,
        "stall_count": layout.stall_count,
        "aisle_count": len(layout.aisles),
        "generation_mode": layout.generation_mode,
        "main_entrance_id": layout.main_entrance_id,
        "exit_entrance_id": next(
            (aisle.connected_to_entrance_id for aisle in layout.aisles if aisle.role == "exit" and aisle.connected_to_entrance_id),
            None,
        ),
        "selected_angle_degrees": layout.selected_angle_degrees,
        "selected_heading_degrees": layout.selected_heading_degrees,
        "selected_heading_delta_degrees": layout.selected_heading_delta_degrees,
        "selected_entrance_offset": layout.selected_entrance_offset,
        "selected_branch": {
            "side": layout.selected_branch_side,
            "start_u": layout.selected_branch_start_u,
            "length": layout.selected_branch_length,
        },
        "selected_branches": layout.selected_branches,
        "selected_connectors": layout.selected_connectors,
        "selected_stall_type_id": layout.selected_stall_type_id,
        "selected_stall_assignment": layout.selected_stall_assignment,
        "stall_type_attempts": layout.stall_type_attempts,
        "stall_assignment_attempts": layout.stall_assignment_attempts,
        "score": layout.score,
        "candidate_snapshot": candidate_snapshot_report(layout),
        "candidate_network_preview": candidate_network_preview_report(layout),
        "candidate_layout_preview": candidate_layout_preview_report(layout),
        "candidate_layout_promotion": layout.candidate_layout_promotion,
        "maneuver_validation": layout.maneuver_validation,
        "site_constraint_validation": getattr(layout, "site_constraint_validation", {}),
        "engineering_validation": getattr(layout, "engineering_validation", {}),
        "road_traversal_validation": getattr(layout, "road_traversal_validation", {}),
        "operational_quality": layout.operational_quality,
        "unsupported_phase1_inputs": layout.unsupported_phase1_inputs,
        "aisles": [
            {
                "id": aisle.id,
                "role": aisle.role,
                "directionality": aisle.directionality,
                "connected_to_entrance_id": aisle.connected_to_entrance_id,
                "parent_aisle_id": aisle.parent_aisle_id,
                "connected_aisle_ids": list(aisle.connected_aisle_ids),
            }
            for aisle in layout.aisles
        ],
        "aisle_directionality": (
            layout.graph_validation.get("aisle_directionality")
            if isinstance(layout.graph_validation, dict)
            else None
        ),
        "stalls": [
            {
                "id": stall.id,
                "served_by_aisle_id": stall.served_by_aisle_id,
                "aisle_side": stall.aisle_side,
                "stall_type_id": stall.stall_type_id,
            }
            for stall in layout.stalls
        ],
        "attempts": [
            {
                "entrance_id": attempt.entrance_id,
                "heading_degrees": attempt.angle_degrees,
                "heading_delta_degrees": attempt.heading_delta_degrees,
                "entrance_offset": attempt.entrance_offset,
                "branch_side": attempt.branch_side,
                "branch_start_u": attempt.branch_start_u,
                "branch_length": attempt.branch_length,
                "branch_candidates": attempt.branch_candidates,
                "graph_valid": attempt.graph_valid,
                "graph_errors": attempt.graph_errors,
                "stall_count": attempt.stall_count,
            }
            for attempt in layout.attempts
        ],
        "stall": _stall_spec_report(layout.site.stall),
        "stall_types": [
            _stall_spec_report(stall)
            for stall in (layout.site.stall_candidates or (layout.site.stall,))
        ],
        "stall_assignment": {
            "main": _stall_spec_report(layout.site.main_stall or layout.site.stall),
            "branch": _stall_spec_report(layout.site.branch_stall or layout.site.main_stall or layout.site.stall),
            "connector": _stall_spec_report(layout.site.branch_stall or layout.site.main_stall or layout.site.stall),
        },
        "aisle_width": layout.site.aisle_width,
        "traffic_graph": traffic_graph_report(layout),
        "input_diagnostics": build_input_diagnostics(layout.site, layout),
        "layout_search": layout_search_report(layout),
    }
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _final_layout_errors(layout) -> list[str]:
    errors: list[str] = []
    if layout.stall_count <= 0:
        errors.append("layout contains no parking stalls")

    graph_validation = layout.graph_validation
    if not graph_validation.get("valid", False):
        details = ", ".join(str(item) for item in graph_validation.get("errors", []))
        errors.append(f"traffic graph validation failed{f' ({details})' if details else ''}")

    maneuver_validation = layout.maneuver_validation
    if not maneuver_validation.get("valid", False):
        invalid_count = len(maneuver_validation.get("invalid_stalls", []))
        errors.append(
            f"maneuver validation failed{f' ({invalid_count} invalid stalls)' if invalid_count else ''}"
        )

    site_constraint_validation = getattr(layout, "site_constraint_validation", {"valid": True})
    if not site_constraint_validation.get("valid", False):
        site_errors = site_constraint_validation.get("errors", [])
        errors.append(
            "site constraint validation failed"
            f"{f' ({len(site_errors)} errors)' if isinstance(site_errors, list) and site_errors else ''}"
        )

    engineering_validation = getattr(layout, "engineering_validation", {})
    if engineering_validation and not engineering_validation.get("valid", False):
        failed_rules = engineering_validation.get("rules", {}).get("failed", [])
        errors.append(
            "engineering validation failed"
            f"{f' ({len(failed_rules)} failed rules)' if isinstance(failed_rules, list) and failed_rules else ''}"
        )

    operational_quality = layout.operational_quality
    if not operational_quality.get("valid", False):
        risk_score = operational_quality.get("risk_score")
        errors.append(
            f"operational quality hard rejection{f' (risk score {risk_score:g})' if isinstance(risk_score, int | float) else ''}"
        )

    from openparkcad.road_traversal import road_traversal_publication_error

    road_error = road_traversal_publication_error(layout)
    if road_error:
        errors.append(road_error)
    return errors


def _write_output_set(
    layout,
    dxf_path: Path,
    svg_path: Path,
    report_path: Path,
    *,
    restore_source_coordinates: bool = False,
) -> None:
    def _dxf(current, path: str | Path) -> None:
        write_dxf(current, path, restore_source_coordinates=restore_source_coordinates)

    outputs: list[tuple[Path, Callable[[Any, str | Path], None]]] = [
        (dxf_path, _dxf),
        (svg_path, write_svg),
        (report_path, _write_report),
    ]
    _require_distinct_output_paths([target for target, _ in outputs])

    temporary_paths: dict[Path, Path] = {}
    backup_paths: dict[Path, Path] = {}
    try:
        for target, _ in outputs:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_paths[target] = _temporary_sibling(target, suffix=".tmp")

        for target, writer in outputs:
            writer(layout, temporary_paths[target])

        for target, _ in outputs:
            if target.is_file():
                backup = _temporary_sibling(target, suffix=".bak")
                shutil.copy2(target, backup)
                backup_paths[target] = backup

        committed: list[Path] = []
        try:
            for target, _ in outputs:
                os.replace(temporary_paths[target], target)
                committed.append(target)
        except Exception:
            for target in reversed(committed):
                backup = backup_paths.get(target)
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            raise
    finally:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        for backup in backup_paths.values():
            backup.unlink(missing_ok=True)


def _require_distinct_output_paths(paths: list[Path]) -> None:
    identities = [os.path.normcase(str(path.resolve())) for path in paths]
    if len(set(identities)) != len(identities):
        raise ValueError("DXF, SVG, and report output paths must be different")


def _write_rejection_diagnostics(
    layout,
    path: Path,
    validation_errors: list[str],
    *,
    official_paths: list[Path],
) -> None:
    _require_distinct_output_paths([*official_paths, path])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "diagnostics_contract_version": "openparkcad-rejection-diagnostics-1",
        "official_layout_published": False,
        "errors": validation_errors,
        "road_traversal_validation": getattr(layout, "road_traversal_validation", {}),
        "engineering_validation": getattr(layout, "engineering_validation", {}),
        "graph_validation": getattr(layout, "graph_validation", {}),
        "maneuver_validation": getattr(layout, "maneuver_validation", {}),
        "site_constraint_validation": getattr(layout, "site_constraint_validation", {}),
        "stall_ids": [stall.id for stall in layout.stalls],
        "aisle_ids": [aisle.id for aisle in layout.aisles],
        "layout_identity": (layout.road_traversal_validation or {}).get("layout_identity")
        if isinstance(getattr(layout, "road_traversal_validation", None), dict)
        else None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _temporary_sibling(target: Path, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _error(message: str, exit_code: int = 2) -> int:
    print(f"error: {message}", file=sys.stderr)
    return exit_code


def _stall_spec_report(stall) -> dict[str, object]:
    return {
        "id": stall.id,
        "family": stall.family,
        "width": stall.width,
        "length": stall.length,
        "allowed_angles": list(stall.allowed_angles),
        "classifications": list(stall.classifications),
        "fixed_features": list(stall.fixed_features),
        "drive_over": stall.drive_over,
        "access_sides": list(stall.access_sides),
        "blocked_sides": list(stall.blocked_sides),
    }


if __name__ == "__main__":
    raise SystemExit(main())
