from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.run_all import STORE_CONFIGS
from scripts.compare_tracker_backends import compare_backend


DEFAULT_VIDEO_DIRS = {
    "STORE_BLR_002": Path(r"D:\code\purplletech\Store 1-20260602T101818Z-3-001ec38db8\Store 1"),
    "STORE_BLR_003": Path(r"D:\code\purplletech\Store 2-20260602T101819Z-3-001099f208\Store 2"),
}


def score_result(row: dict[str, Any]) -> float:
    frames = max(1, int(row.get("frames_sampled") or 0))
    detections = int(row.get("detections_total") or 0)
    unique_tracks = int(row.get("unique_track_count") or 0)
    assignment_rate = float(row.get("tracker_id_assignment_rate") or 0.0)
    predicted_rate = float(row.get("predicted_track_samples") or 0) / frames
    avg_tracks = float(row.get("avg_tracks_per_sampled_frame") or 0.0)

    score = 0.0
    score += min(35.0, assignment_rate * 35.0)
    score += min(25.0, detections * 2.0)
    score += min(20.0, avg_tracks * 10.0)
    score += min(10.0, unique_tracks * 3.0)
    score -= min(20.0, predicted_rate * 20.0)
    if row.get("backend_active") == "centroid":
        score -= 4.0
    return round(max(0.0, min(100.0, score)), 2)


def recommendation_for(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [{**row, "quality_score": score_result(row)} for row in results]
    best = max(
        scored,
        key=lambda row: (
            row["quality_score"],
            row.get("tracker_id_assignment_rate") or 0,
            row.get("avg_tracks_per_sampled_frame") or 0,
            -int(row.get("unique_track_count") or 0),
        ),
    ) if scored else {}
    auto = next((row for row in scored if row.get("backend_requested") == "auto"), None)
    return {
        "recommended_backend": best.get("backend_active"),
        "recommended_from_requested": best.get("backend_requested"),
        "recommended_score": best.get("quality_score", 0),
        "auto_backend": auto.get("backend_active") if auto else None,
        "auto_score": auto.get("quality_score", 0) if auto else 0,
        "auto_matches_recommendation": bool(auto and best and auto.get("backend_active") == best.get("backend_active")),
        "scored_results": scored,
    }


def camera_jobs(store_ids: list[str], video_dirs: dict[str, Path], camera_id: str | None = None) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for store_id in store_ids:
        config = STORE_CONFIGS[store_id]
        video_dir = video_dirs[store_id]
        for camera in config["cameras"]:
            if camera_id and camera["camera_id"] != camera_id:
                continue
            video_path = video_dir / camera["filename"]
            jobs.append(
                {
                    "store_id": store_id,
                    "camera_id": camera["camera_id"],
                    "role": camera["role"],
                    "video_path": video_path,
                    "video_exists": video_path.exists(),
                }
            )
    return jobs


def build_report(
    *,
    store_ids: list[str],
    video_dirs: dict[str, Path],
    backends: list[str],
    max_seconds: float,
    model_path: Path,
    camera_id: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for job in camera_jobs(store_ids, video_dirs, camera_id=camera_id):
        if not job["video_exists"]:
            rows.append({**job, "status": "missing_video", "results": [], "recommendation": {}})
            continue
        results = [
            compare_backend(
                video_path=job["video_path"],
                store_id=job["store_id"],
                camera_id=job["camera_id"],
                role=job["role"],
                backend=backend,
                model_path=model_path,
                max_seconds=max_seconds,
            )
            for backend in backends
        ]
        rows.append(
            {
                **job,
                "video_path": str(job["video_path"]),
                "status": "evaluated",
                "results": results,
                "recommendation": recommendation_for(results),
            }
        )

    evaluated = [row for row in rows if row["status"] == "evaluated"]
    auto_matches = sum(1 for row in evaluated if row["recommendation"].get("auto_matches_recommendation"))
    return {
        "report_type": "tracker_profile_evaluation",
        "store_ids": store_ids,
        "backends": backends,
        "max_seconds": max_seconds,
        "camera_filter": camera_id,
        "model_path": str(model_path),
        "camera_count": len(rows),
        "evaluated_camera_count": len(evaluated),
        "missing_camera_count": len(rows) - len(evaluated),
        "auto_profile_match_rate": round(auto_matches / len(evaluated), 3) if evaluated else 0.0,
        "cameras": rows,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Tracker Profile Evaluation",
        "",
        f"- Stores: `{', '.join(report['store_ids'])}`",
        f"- Backends: `{', '.join(report['backends'])}`",
        f"- Max seconds per camera/backend: `{report['max_seconds']}`",
        f"- Cameras evaluated: {report['evaluated_camera_count']}/{report['camera_count']}",
        f"- Auto profile match rate: {report['auto_profile_match_rate']}",
        "",
        "## Camera Recommendations",
        "",
    ]
    for row in report["cameras"]:
        lines.append(f"### {row['store_id']} {row['camera_id']} ({row['role']})")
        if row["status"] != "evaluated":
            lines.extend([f"- Status: `{row['status']}`", f"- Video: `{row['video_path']}`", ""])
            continue
        rec = row["recommendation"]
        lines.extend(
            [
                f"- Auto backend: `{rec['auto_backend']}`",
                f"- Recommended backend: `{rec['recommended_backend']}`",
                f"- Auto matches recommendation: `{rec['auto_matches_recommendation']}`",
                f"- Recommended score: {rec['recommended_score']}",
                "",
                "| Requested | Active | Score | Detections | IDs | Unique Tracks | Avg Tracks/Frame | Assignment Rate |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for result in rec["scored_results"]:
            lines.append(
                "| {backend_requested} | {backend_active} | {quality_score} | {detections_total} | {assigned_ids_total} | {unique_track_count} | {avg_tracks_per_sampled_frame} | {tracker_id_assignment_rate} |".format(
                    **result
                )
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate tracker profiles across mapped store cameras.")
    parser.add_argument("--store-id", choices=["STORE_BLR_002", "STORE_BLR_003", "both"], default="STORE_BLR_002")
    parser.add_argument("--camera-id", default=None, help="Optional single camera filter, such as CAM_3.")
    parser.add_argument("--store1-video-dir", type=Path, default=DEFAULT_VIDEO_DIRS["STORE_BLR_002"])
    parser.add_argument("--store2-video-dir", type=Path, default=DEFAULT_VIDEO_DIRS["STORE_BLR_003"])
    parser.add_argument("--backends", nargs="+", default=["auto", "bytetrack", "botsort", "centroid"])
    parser.add_argument("--max-seconds", type=float, default=5.0)
    parser.add_argument("--model-path", type=Path, default=PROJECT_ROOT / "yolov8s.pt")
    parser.add_argument("--report-json", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_profile_evaluation.json")
    parser.add_argument("--report-md", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_profile_evaluation.md")
    args = parser.parse_args()

    store_ids = ["STORE_BLR_002", "STORE_BLR_003"] if args.store_id == "both" else [args.store_id]
    video_dirs = {
        "STORE_BLR_002": args.store1_video_dir,
        "STORE_BLR_003": args.store2_video_dir,
    }
    report = build_report(
        store_ids=store_ids,
        video_dirs=video_dirs,
        backends=args.backends,
        max_seconds=args.max_seconds,
        model_path=args.model_path,
        camera_id=args.camera_id,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_markdown(report, args.report_md)
    print(json.dumps({
        "report_json": str(args.report_json),
        "report_md": str(args.report_md),
        "evaluated_camera_count": report["evaluated_camera_count"],
        "auto_profile_match_rate": report["auto_profile_match_rate"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
