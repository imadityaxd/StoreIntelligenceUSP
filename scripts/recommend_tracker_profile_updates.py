from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.tracker_profiles import DEFAULT_PROFILE_PATH, load_tracker_profiles, normalize_profile_backend


def effective_backend(profiles: dict[str, Any], store_id: str, camera_id: str, role: str | None) -> str:
    role_key = (role or "zone").strip().lower().replace(" ", "_").replace("staff_area", "staff-area")
    default_backend = (profiles.get("default_profile") or {}).get("tracker_backend", "bytetrack")
    role_backend = ((profiles.get("role_profiles") or {}).get(role_key) or {}).get("tracker_backend", default_backend)
    camera_backend = (((profiles.get("camera_profiles") or {}).get(store_id) or {}).get(camera_id) or {}).get("tracker_backend")
    return normalize_profile_backend(camera_backend or role_backend or default_backend)


def recommendation_rows(
    report: dict[str, Any],
    profiles: dict[str, Any],
    *,
    min_seconds: float,
    min_score_margin: float,
    min_assignment_rate: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    report_seconds = float(report.get("max_seconds") or 0)
    enough_duration = report_seconds >= min_seconds
    for camera in report.get("cameras", []):
        store_id = camera.get("store_id")
        camera_id = camera.get("camera_id")
        role = camera.get("role")
        current_backend = effective_backend(profiles, store_id, camera_id, role)
        rec = camera.get("recommendation") or {}
        scored = rec.get("scored_results") or []
        best_backend = normalize_profile_backend(rec.get("recommended_backend"))
        best_score = float(rec.get("recommended_score") or 0)
        auto_score = float(rec.get("auto_score") or 0)
        score_margin = round(best_score - auto_score, 3)
        best_row = next((row for row in scored if row.get("backend_active") == best_backend), {})
        assignment_rate = float(best_row.get("tracker_id_assignment_rate") or 0)
        should_update = (
            camera.get("status") == "evaluated"
            and enough_duration
            and bool(best_backend)
            and best_backend != current_backend
            and score_margin >= min_score_margin
            and assignment_rate >= min_assignment_rate
        )
        reasons: list[str] = []
        if camera.get("status") != "evaluated":
            reasons.append("camera_not_evaluated")
        if not enough_duration:
            reasons.append(f"duration_below_{min_seconds}s")
        if best_backend == current_backend:
            reasons.append("current_backend_already_recommended")
        if score_margin < min_score_margin:
            reasons.append("score_margin_too_small")
        if assignment_rate < min_assignment_rate:
            reasons.append("assignment_rate_too_low")

        rows.append(
            {
                "store_id": store_id,
                "camera_id": camera_id,
                "role": role,
                "current_backend": current_backend,
                "recommended_backend": best_backend,
                "auto_backend": rec.get("auto_backend"),
                "auto_matches_recommendation": bool(rec.get("auto_matches_recommendation")),
                "recommended_score": best_score,
                "auto_score": auto_score,
                "score_margin": score_margin,
                "assignment_rate": assignment_rate,
                "action": "update_tracker_backend" if should_update else "keep",
                "reasons": reasons,
            }
        )
    return rows


def apply_recommendations(profiles: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    updated = deepcopy(profiles)
    camera_profiles = updated.setdefault("camera_profiles", {})
    for row in rows:
        if row.get("action") != "update_tracker_backend":
            continue
        store_id = row["store_id"]
        camera_id = row["camera_id"]
        store_profiles = camera_profiles.setdefault(store_id, {})
        camera_profile = store_profiles.setdefault(camera_id, {})
        camera_profile["tracker_backend"] = row["recommended_backend"]
        camera_profile.setdefault("profile_name", f"{store_id}_{camera_id}".lower())
    return updated


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# Tracker Profile Update Recommendations",
        "",
        f"- Evaluation report: `{payload['evaluation_report']}`",
        f"- Profile file: `{payload['profile_path']}`",
        f"- Applied: `{payload['applied']}`",
        f"- Updates recommended: {payload['update_count']}",
        "",
        "| Store | Camera | Role | Current | Recommended | Action | Margin | Assignment | Reasons |",
        "|---|---|---|---|---|---|---:|---:|---|",
    ]
    for row in payload["recommendations"]:
        reasons = ", ".join(row["reasons"]) if row["reasons"] else "-"
        lines.append(
            f"| {row['store_id']} | {row['camera_id']} | {row['role']} | {row['current_backend']} | {row['recommended_backend']} | {row['action']} | {row['score_margin']} | {row['assignment_rate']} | {reasons} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_payload(
    *,
    evaluation_report_path: Path,
    profile_path: Path,
    output_profile_path: Path | None,
    min_seconds: float,
    min_score_margin: float,
    min_assignment_rate: float,
    apply: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report = json.loads(evaluation_report_path.read_text(encoding="utf-8"))
    profiles = load_tracker_profiles(profile_path)
    rows = recommendation_rows(
        report,
        profiles,
        min_seconds=min_seconds,
        min_score_margin=min_score_margin,
        min_assignment_rate=min_assignment_rate,
    )
    updated_profiles = apply_recommendations(profiles, rows)
    target_profile = output_profile_path or profile_path
    payload = {
        "evaluation_report": str(evaluation_report_path),
        "profile_path": str(profile_path),
        "output_profile_path": str(target_profile),
        "applied": apply,
        "min_seconds": min_seconds,
        "min_score_margin": min_score_margin,
        "min_assignment_rate": min_assignment_rate,
        "update_count": sum(1 for row in rows if row["action"] == "update_tracker_backend"),
        "recommendations": rows,
    }
    return payload, updated_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend safe tracker profile updates from an evaluation report.")
    parser.add_argument("--evaluation-report", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_profile_evaluation.json")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--output-profiles", type=Path, default=None, help="Optional output path for updated profiles. Defaults to --profiles when --apply is used.")
    parser.add_argument("--report-json", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_profile_update_recommendations.json")
    parser.add_argument("--report-md", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_profile_update_recommendations.md")
    parser.add_argument("--min-seconds", type=float, default=5.0)
    parser.add_argument("--min-score-margin", type=float, default=3.0)
    parser.add_argument("--min-assignment-rate", type=float, default=0.75)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    payload, updated_profiles = build_payload(
        evaluation_report_path=args.evaluation_report,
        profile_path=args.profiles,
        output_profile_path=args.output_profiles,
        min_seconds=args.min_seconds,
        min_score_margin=args.min_score_margin,
        min_assignment_rate=args.min_assignment_rate,
        apply=args.apply,
    )
    if args.apply and payload["update_count"]:
        target_profile = Path(payload["output_profile_path"])
        target_profile.parent.mkdir(parents=True, exist_ok=True)
        target_profile.write_text(json.dumps(updated_profiles, indent=2), encoding="utf-8")

    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload, args.report_md)
    print(json.dumps({
        "report_json": str(args.report_json),
        "report_md": str(args.report_md),
        "updates_recommended": payload["update_count"],
        "applied": payload["applied"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
