from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fmt_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_layout(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def zone_catalog(layout: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not layout:
        return {}
    return {zone["zone_id"]: zone for zone in layout.get("zones", [])}


def customer_events(events: list[dict]) -> list[dict]:
    return [event for event in events if not event["is_staff"]]


def staff_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event["is_staff"]]


def unique_customers(events: list[dict]) -> set[str]:
    return {event["visitor_id"] for event in customer_events(events)}


def observed_window(events: list[dict], pos_transactions: list[dict]) -> dict[str, str | None]:
    timestamps = [parse_ts(row["timestamp"]) for row in events + pos_transactions]
    if not timestamps:
        return {"start": None, "end": None}
    return {"start": fmt_ts(min(timestamps)), "end": fmt_ts(max(timestamps))}


def data_confidence(session_count: int) -> dict[str, str]:
    if session_count < 20:
        return {
            "data_confidence": "LOW",
            "confidence_reason": "fewer than 20 customer sessions in window",
        }
    return {"data_confidence": "HIGH", "confidence_reason": "20+ customer sessions in window"}


def match_pos_to_visitors(events: list[dict], pos_transactions: list[dict]) -> dict[str, Any]:
    billing_events = [
        event
        for event in customer_events(events)
        if event["event_type"] == "BILLING_QUEUE_JOIN" or event.get("zone_id") == "BILLING_COUNTER"
    ]
    billing_events.sort(key=lambda event: parse_ts(event["timestamp"]), reverse=True)

    matched_visitors: set[str] = set()
    matched_transaction_ids: set[str] = set()
    matches: list[dict] = []

    for transaction in sorted(pos_transactions, key=lambda row: row["timestamp"]):
        txn_ts = parse_ts(transaction["timestamp"])
        window_start = txn_ts - timedelta(minutes=5)
        candidates = [
            event
            for event in billing_events
            if event["visitor_id"] not in matched_visitors
            and window_start <= parse_ts(event["timestamp"]) <= txn_ts
        ]
        if not candidates:
            continue

        chosen = max(candidates, key=lambda event: parse_ts(event["timestamp"]))
        matched_visitors.add(chosen["visitor_id"])
        matched_transaction_ids.add(transaction["transaction_id"])
        matches.append(
            {
                "transaction_id": transaction["transaction_id"],
                "visitor_id": chosen["visitor_id"],
                "transaction_timestamp": transaction["timestamp"],
                "billing_event_timestamp": chosen["timestamp"],
                "basket_value_inr": transaction["basket_value_inr"],
                "match_rule": "same_store_billing_event_within_5_minutes_before_pos",
            }
        )

    billing_visitors = {event["visitor_id"] for event in billing_events}
    return {
        "matched_visitor_ids": matched_visitors,
        "matched_transaction_ids": matched_transaction_ids,
        "purchase_count": len(matches),
        "matches": matches,
        "unmatched_pos_transaction_ids": [
            row["transaction_id"]
            for row in pos_transactions
            if row["transaction_id"] not in matched_transaction_ids
        ],
        "unmatched_billing_visitor_ids": sorted(billing_visitors - matched_visitors),
    }


def compute_metrics(
    events: list[dict],
    pos_transactions: list[dict],
    layout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    customers = unique_customers(events)
    conversion = match_pos_to_visitors(events, pos_transactions)
    zones = zone_catalog(layout)
    dwell_by_zone: dict[str, list[int]] = defaultdict(list)

    latest_queue_depth = 0
    latest_queue_ts: datetime | None = None
    max_queue_depth = 0
    billing_visitors: set[str] = set()
    queue_join_events = 0

    for event in customer_events(events):
        if event["event_type"] == "ZONE_DWELL" and event.get("zone_id"):
            dwell_by_zone[event["zone_id"]].append(int(event["dwell_ms"]))

        if event["event_type"] == "BILLING_QUEUE_JOIN":
            queue_join_events += 1
            billing_visitors.add(event["visitor_id"])
            queue_depth = event.get("metadata", {}).get("queue_depth")
            event_ts = parse_ts(event["timestamp"])
            if queue_depth is not None:
                max_queue_depth = max(max_queue_depth, int(queue_depth))
                if latest_queue_ts is None or event_ts >= latest_queue_ts:
                    latest_queue_ts = event_ts
                    latest_queue_depth = int(queue_depth)

    converted_visitors = conversion["matched_visitor_ids"]
    abandoned = billing_visitors - converted_visitors
    total_pos_sales = round(sum(float(row["basket_value_inr"]) for row in pos_transactions), 2)
    matched_sales = round(sum(float(row["basket_value_inr"]) for row in conversion["matches"]), 2)
    session_count = len(customers)
    has_pos = bool(pos_transactions)
    estimated_billing_intent_count = len(billing_visitors)
    display_conversion_count = conversion["purchase_count"] if has_pos else estimated_billing_intent_count
    display_conversion_rate = round(display_conversion_count / session_count, 4) if session_count else 0.0

    avg_dwell_ms_per_zone = {
        zone_id: round(mean(values), 2) for zone_id, values in sorted(dwell_by_zone.items())
    }
    avg_dwell_detail = [
        {
            "zone_id": zone_id,
            "label": zones.get(zone_id, {}).get("label", zone_id),
            "avg_dwell_ms": value,
            "avg_dwell_seconds": round(value / 1000, 2),
        }
        for zone_id, value in avg_dwell_ms_per_zone.items()
    ]

    confidence = data_confidence(session_count)
    return {
        "window": observed_window(events, pos_transactions),
        "event_counts": {
            "total": len(events),
            "customer": len(customer_events(events)),
            "staff": len(staff_events(events)),
            "low_confidence": len([event for event in events if event["confidence"] < 0.5]),
        },
        "unique_visitors": session_count,
        "staff_visitor_ids": sorted({event["visitor_id"] for event in staff_events(events)}),
        "purchase_count": conversion["purchase_count"],
        "conversion_rate": round(conversion["purchase_count"] / session_count, 4) if session_count else 0.0,
        "conversion_rate_pct": round(conversion["purchase_count"] / session_count * 100, 2)
        if session_count
        else 0.0,
        "conversion": {
            "basis": "pos_confirmed" if has_pos else "estimated_billing_intent",
            "confidence": "CONFIRMED" if has_pos else "ESTIMATED",
            "confirmed_purchase_count": conversion["purchase_count"],
            "estimated_billing_intent_count": estimated_billing_intent_count,
            "display_count": display_conversion_count,
            "display_rate": display_conversion_rate,
            "display_rate_pct": round(display_conversion_rate * 100, 2),
            "match_window_minutes": 5,
            "note": (
                "Conversion is POS-confirmed using billing events within 5 minutes before POS."
                if has_pos
                else "POS is not connected for this scope; conversion shows billing intent estimate."
            ),
        },
        "pos": {
            "transaction_count": len(pos_transactions),
            "matched_transaction_count": len(conversion["matched_transaction_ids"]),
            "unmatched_transaction_count": len(conversion["unmatched_pos_transaction_ids"]),
            "total_sales_inr": total_pos_sales,
            "matched_sales_inr": matched_sales,
            "avg_basket_value_inr": round(total_pos_sales / len(pos_transactions), 2)
            if pos_transactions
            else 0.0,
            "matches": conversion["matches"],
        },
        "avg_dwell_ms_per_zone": avg_dwell_ms_per_zone,
        "avg_dwell_by_zone": avg_dwell_detail,
        "queue_depth": latest_queue_depth,
        "queue": {
            "latest_depth": latest_queue_depth,
            "max_depth": max_queue_depth,
            "join_events": queue_join_events,
            "visitors_joined": len(billing_visitors),
            "abandoned_visitor_count": len(abandoned),
            "unmatched_billing_visitor_ids": conversion["unmatched_billing_visitor_ids"],
        },
        "abandonment_rate": round(len(abandoned) / len(billing_visitors), 4) if billing_visitors else 0.0,
        "abandonment_rate_pct": round(len(abandoned) / len(billing_visitors) * 100, 2)
        if billing_visitors
        else 0.0,
        "converted_visitor_ids": sorted(converted_visitors),
        **confidence,
    }


def compute_funnel(events: list[dict], pos_transactions: list[dict]) -> dict[str, Any]:
    customers = customer_events(events)
    entry_visitors = {event["visitor_id"] for event in customers if event["event_type"] == "ENTRY"}
    all_visitors = {event["visitor_id"] for event in customers}
    zone_visitors = {
        event["visitor_id"]
        for event in customers
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT", "BILLING_QUEUE_JOIN"}
        and event.get("zone_id") != "ENTRY_EXIT"
    }
    billing_visitors = {
        event["visitor_id"]
        for event in customers
        if event["event_type"] == "BILLING_QUEUE_JOIN" or event.get("zone_id") == "BILLING_COUNTER"
    }
    converted_visitors = match_pos_to_visitors(events, pos_transactions)["matched_visitor_ids"]

    entry_count = len(entry_visitors or all_visitors)
    zone_count = min(len(zone_visitors), entry_count) if entry_count else len(zone_visitors)
    billing_count = min(len(billing_visitors), zone_count) if zone_count else len(billing_visitors)
    purchase_count = min(len(converted_visitors), billing_count) if billing_count else len(converted_visitors)

    raw_stages = [
        ("entry", entry_count),
        ("zone_visit", zone_count),
        ("billing_queue", billing_count),
        ("purchase", purchase_count),
    ]

    stages = []
    previous_count: int | None = None
    for stage_name, count in raw_stages:
        if previous_count is None:
            dropoff = 0.0
            conversion_from_previous = 100.0 if count > 0 else 0.0
        elif previous_count == 0:
            dropoff = 0.0
            conversion_from_previous = 0.0
        else:
            dropoff = round((previous_count - count) / previous_count * 100, 2)
            conversion_from_previous = round(count / previous_count * 100, 2)
        stages.append(
            {
                "stage": stage_name,
                "count": count,
                "previous_stage_count": previous_count,
                "dropoff_from_previous_pct": dropoff,
                "conversion_from_previous_pct": conversion_from_previous,
            }
        )
        previous_count = count

    entry_count = stages[0]["count"]
    final_count = stages[-1]["count"]
    return {
        "window": observed_window(events, pos_transactions),
        "stages": stages,
        "overall_conversion_pct": round(final_count / entry_count * 100, 2) if entry_count else 0.0,
        "session_unit": "visitor_id",
        "reentry_policy": "REENTRY events reuse visitor_id and do not increase entry count",
    }


def compute_heatmap(events: list[dict], layout: dict[str, Any]) -> dict[str, Any]:
    customers = customer_events(events)
    visitor_count = len({event["visitor_id"] for event in customers})
    zones = zone_catalog(layout)
    zone_ids = [zone["zone_id"] for zone in layout.get("zones", []) if zone["zone_id"] != "ENTRY_EXIT"]
    visits_by_zone: dict[str, set[str]] = {zone_id: set() for zone_id in zone_ids}
    dwell_by_zone: dict[str, list[int]] = {zone_id: [] for zone_id in zone_ids}

    for event in customers:
        zone_id = event.get("zone_id")
        if not zone_id or zone_id not in visits_by_zone:
            continue
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL", "BILLING_QUEUE_JOIN"}:
            visits_by_zone[zone_id].add(event["visitor_id"])
        if event["event_type"] == "ZONE_DWELL":
            dwell_by_zone[zone_id].append(int(event["dwell_ms"]))

    max_visits = max((len(visitors) for visitors in visits_by_zone.values()), default=0) or 1
    zone_avg_dwell = {
        zone_id: mean(values) if values else 0.0 for zone_id, values in dwell_by_zone.items()
    }
    max_dwell = max(zone_avg_dwell.values(), default=0.0) or 1.0

    heat_zones = []
    for zone_id in zone_ids:
        visit_count = len(visits_by_zone[zone_id])
        avg_dwell = zone_avg_dwell[zone_id]
        visit_score = visit_count / max_visits * 100
        dwell_score = avg_dwell / max_dwell * 100
        heat_zones.append(
            {
                "zone_id": zone_id,
                "label": zones.get(zone_id, {}).get("label", zone_id),
                "sku_zone": zones.get(zone_id, {}).get("sku_zone"),
                "visit_count": visit_count,
                "avg_dwell_ms": round(avg_dwell, 2),
                "avg_dwell_seconds": round(avg_dwell / 1000, 2),
                "normalized_score": round((visit_score + dwell_score) / 2, 2),
            }
        )

    return {
        "window": observed_window(events, []),
        **data_confidence(visitor_count),
        "zones": heat_zones,
    }


def _estimate_baseline_conversion(events: list[dict], pos_transactions: list[dict]) -> dict[str, Any]:
    """
    Attempt a naive rolling baseline from available data.

    We bucket events by calendar day and compute per-day conversion rates.
    If fewer than 2 distinct days exist we cannot form a meaningful baseline
    and return a low-confidence sentinel instead.

    Returns a dict with keys:
        has_baseline: bool
        baseline_conversion_rate: float | None
        days_of_data: int
        note: str
    """
    from collections import defaultdict

    day_visitors: dict[str, set[str]] = defaultdict(set)
    day_purchases: dict[str, set[str]] = defaultdict(set)

    billing_events = [
        e for e in customer_events(events)
        if e["event_type"] in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}
        or e.get("zone_id") == "BILLING_COUNTER"
    ]

    for e in customer_events(events):
        day = parse_ts(e["timestamp"]).strftime("%Y-%m-%d")
        day_visitors[day].add(e["visitor_id"])

    # Match POS to billing events per day (reuse existing logic per-day)
    matched_per_day: dict[str, set[str]] = defaultdict(set)
    for txn in pos_transactions:
        txn_ts = parse_ts(txn["timestamp"])
        day = txn_ts.strftime("%Y-%m-%d")
        window_start = txn_ts - timedelta(minutes=5)
        candidates = [
            e for e in billing_events
            if window_start <= parse_ts(e["timestamp"]) <= txn_ts
        ]
        if candidates:
            chosen = max(candidates, key=lambda e: parse_ts(e["timestamp"]))
            matched_per_day[day].add(chosen["visitor_id"])

    days_of_data = len(day_visitors)

    if days_of_data < 2:
        return {
            "has_baseline": False,
            "baseline_conversion_rate": None,
            "days_of_data": days_of_data,
            "note": (
                f"Only {days_of_data} day(s) of data available; "
                "need >=2 days to form a rolling conversion baseline. "
                "Conversion anomaly detection is in low-confidence mode."
            ),
        }

    # Compute per-day rates, exclude today (current partial day)
    sorted_days = sorted(day_visitors.keys())
    baseline_days = sorted_days[:-1]  # exclude most recent partial day
    rates = []
    for day in baseline_days:
        visitors = len(day_visitors[day])
        purchases = len(matched_per_day.get(day, set()))
        if visitors > 0:
            rates.append(purchases / visitors)

    if not rates:
        return {
            "has_baseline": False,
            "baseline_conversion_rate": None,
            "days_of_data": days_of_data,
            "note": "Baseline days found but no visitor sessions recorded; cannot compute rate.",
        }

    baseline_rate = sum(rates) / len(rates)
    return {
        "has_baseline": True,
        "baseline_conversion_rate": round(baseline_rate, 4),
        "days_of_data": days_of_data,
        "note": f"Baseline from {len(baseline_days)} prior day(s): avg conversion {baseline_rate:.1%}.",
    }


def compute_anomalies(events: list[dict], pos_transactions: list[dict], layout: dict[str, Any]) -> list[dict]:
    if not events:
        return [
            {
                "type": "NO_EVENTS",
                "severity": "INFO",
                "message": "No events have been ingested for this store.",
                "suggested_action": "Run the detection pipeline or replay sample events.",
            }
        ]

    anomalies: list[dict] = []
    metrics = compute_metrics(events, pos_transactions, layout)
    latest_event_ts = max(parse_ts(event["timestamp"]) for event in events)

    # -- Billing queue spike --------------------------------------------------
    if metrics["queue"]["latest_depth"] >= 8:
        severity = "CRITICAL"
    elif metrics["queue"]["latest_depth"] >= 5:
        severity = "WARN"
    else:
        severity = None

    if severity:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": severity,
                "message": f"Latest observed queue depth is {metrics['queue']['latest_depth']}.",
                "suggested_action": "Open another billing counter or assign staff to checkout.",
            }
        )

    # -- Conversion drop (baseline-aware) ------------------------------------
    # Try to build a rolling baseline. If insufficient history exists, fall
    # back gracefully with a low-confidence note instead of silently comparing
    # against a hardcoded 20% threshold that may not fit this store.
    baseline = _estimate_baseline_conversion(events, pos_transactions)
    current_rate = metrics["conversion_rate"]

    if not baseline["has_baseline"]:
        # No baseline available - use static threshold but flag low confidence
        if metrics["unique_visitors"] >= 5 and current_rate < 0.2:
            anomalies.append(
                {
                    "type": "CONVERSION_DROP",
                    "severity": "WARN",
                    "baseline_confidence": "LOW",
                    "baseline_note": baseline["note"],
                    "current_conversion_rate": current_rate,
                    "threshold_used": 0.2,
                    "message": (
                        f"Conversion rate {current_rate:.1%} is below the 20% static threshold. "
                        f"Note: {baseline['note']}"
                    ),
                    "suggested_action": (
                        "Treat as indicative only - no historical baseline available. "
                        "Check staff availability and billing wait times."
                    ),
                }
            )
        elif metrics["unique_visitors"] >= 5:
            # Enough visitors but rate is OK - still note missing baseline
            anomalies.append(
                {
                    "type": "LOW_BASELINE_CONFIDENCE",
                    "severity": "INFO",
                    "baseline_note": baseline["note"],
                    "current_conversion_rate": current_rate,
                    "message": baseline["note"],
                    "suggested_action": (
                        "Ingest more days of data to enable baseline-relative anomaly detection."
                    ),
                }
            )
    else:
        # We have a baseline - flag if current rate drops more than 30% relative
        baseline_rate = baseline["baseline_conversion_rate"]
        drop_threshold = 0.30  # 30% relative drop triggers anomaly
        relative_drop = (baseline_rate - current_rate) / baseline_rate if baseline_rate > 0 else 0.0

        if metrics["unique_visitors"] >= 5 and relative_drop >= drop_threshold:
            anomalies.append(
                {
                    "type": "CONVERSION_DROP",
                    "severity": "WARN",
                    "baseline_confidence": "HIGH",
                    "baseline_note": baseline["note"],
                    "baseline_conversion_rate": baseline_rate,
                    "current_conversion_rate": current_rate,
                    "relative_drop_pct": round(relative_drop * 100, 1),
                    "message": (
                        f"Conversion rate {current_rate:.1%} is {relative_drop:.0%} below "
                        f"the {baseline['days_of_data']}-day baseline of {baseline_rate:.1%}."
                    ),
                    "suggested_action": (
                        "Review billing counter staffing and recent merchandising changes."
                    ),
                }
            )

    # -- Dead zone detection --------------------------------------------------
    product_zone_ids = [
        zone["zone_id"]
        for zone in layout.get("zones", [])
        if zone.get("kind") in {"product_zone", "floor"}
    ]
    recent_cutoff = latest_event_ts - timedelta(minutes=30)
    recent_zone_visits = {
        event["zone_id"]
        for event in customer_events(events)
        if event.get("zone_id") and parse_ts(event["timestamp"]) >= recent_cutoff
    }
    for zone_id in product_zone_ids:
        if zone_id not in recent_zone_visits:
            anomalies.append(
                {
                    "type": "DEAD_ZONE",
                    "severity": "INFO",
                    "zone_id": zone_id,
                    "message": f"No customer visit observed in {zone_id} during the last 30 event-minutes.",
                    "suggested_action": "Review camera coverage first, then check merchandising or staff guidance.",
                }
            )

    # -- Low data confidence --------------------------------------------------
    if metrics["data_confidence"] == "LOW":
        anomalies.append(
            {
                "type": "LOW_DATA_CONFIDENCE",
                "severity": "INFO",
                "message": metrics["confidence_reason"],
                "suggested_action": "Use this response for pipeline validation, not final operational judgement.",
            }
        )

    return anomalies
