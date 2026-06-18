from __future__ import annotations

from pathlib import Path
from typing import Any

from app.analytics import load_layout


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def load_store_layouts(layout_paths: list[Path]) -> dict[str, dict[str, Any]]:
    stores: dict[str, dict[str, Any]] = {}
    for path in unique_paths(layout_paths):
        if not path.exists():
            continue
        layout = load_layout(path)
        store_id = layout.get("store_id")
        if not store_id:
            continue
        stores[store_id] = {
            "layout_path": str(path),
            "layout": layout,
            "profile": build_store_profile(layout, str(path)),
        }
    return stores


def build_camera_catalog(layout: dict[str, Any]) -> list[dict[str, Any]]:
    zones_by_id = {zone["zone_id"]: zone for zone in layout.get("zones", [])}
    cameras: list[dict[str, Any]] = []

    for camera in layout.get("cameras", []):
        zone_ids = sorted(camera.get("zones_normalized", {}).keys())
        zones = []
        for zone_id in zone_ids:
            zone = zones_by_id.get(zone_id, {})
            zones.append(
                {
                    "zone_id": zone_id,
                    "label": zone.get("label", zone_id),
                    "kind": zone.get("kind", "unknown"),
                    "sku_zone": zone.get("sku_zone"),
                }
            )

        cameras.append(
            {
                "camera_id": camera.get("camera_id"),
                "role": camera.get("role", "unknown"),
                "description": camera.get("description"),
                "zone_ids": zone_ids,
                "zones": zones,
                "zone_count": len(zone_ids),
                "has_entry_line": bool(camera.get("entry_line_normalized")),
            }
        )

    return cameras


def build_store_profile(
    layout: dict[str, Any],
    layout_path: str | None = None,
    *,
    event_count: int | None = None,
    pos_transaction_count: int | None = None,
) -> dict[str, Any]:
    cameras = build_camera_catalog(layout)
    zones = [
        {
            "zone_id": zone.get("zone_id"),
            "label": zone.get("label", zone.get("zone_id")),
            "kind": zone.get("kind", "unknown"),
            "camera_ids": zone.get("camera_ids", []),
            "sku_zone": zone.get("sku_zone"),
        }
        for zone in layout.get("zones", [])
    ]

    profile = {
        "store_id": layout.get("store_id"),
        "store_name": layout.get("store_name"),
        "timezone": layout.get("timezone"),
        "open_hours": layout.get("open_hours"),
        "layout_path": layout_path,
        "camera_count": len(cameras),
        "zone_count": len(zones),
        "cameras": cameras,
        "zones": zones,
    }
    if event_count is not None:
        profile["event_count"] = event_count
    if pos_transaction_count is not None:
        profile["pos_transaction_count"] = pos_transaction_count
    return profile
