from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    rows.sort(key=lambda row: (row["timestamp"], row["camera_id"], row["visitor_id"]))
    return rows


def chunked(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [rows[idx : idx + batch_size] for idx in range(0, len(rows), batch_size)]


def realtime_sleep_seconds(previous_batch: list[dict[str, Any]], current_batch: list[dict[str, Any]], speed: float) -> float:
    if not previous_batch or not current_batch or speed <= 0:
        return 0.0
    prev_ts = parse_ts(previous_batch[-1]["timestamp"])
    curr_ts = parse_ts(current_batch[0]["timestamp"])
    delta = (curr_ts - prev_ts).total_seconds()
    return max(0.0, delta / speed)


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        detail = body
        try:
            detail = json.loads(body)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def new_session_id(prefix: str = "REPLAY") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8].upper()}"


def create_backend_session(
    api_base: str,
    session_id: str,
    store_id: str,
    source_label: str,
) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "store_id": store_id,
        "source_type": "event_replay",
        "source_label": source_label,
        "status": "replaying",
        "metadata": {"created_by": "pipeline.replay"},
    }
    return http_json("POST", f"{api_base.rstrip('/')}/sessions", payload=payload)


def replay(
    events_path: Path,
    api_base: str,
    store_id: str,
    session_id: str | None,
    create_session: bool,
    batch_size: int,
    realtime: bool,
    speed: float,
    print_metrics: bool,
    dry_run: bool,
) -> dict[str, Any]:
    events = load_events(events_path)
    if session_id:
        for event in events:
            event.setdefault("session_id", session_id)
    if create_session:
        if not session_id:
            session_id = new_session_id()
            for event in events:
                event["session_id"] = session_id
        if not dry_run:
            create_backend_session(api_base, session_id, store_id, events_path.name)

    batches = chunked(events, batch_size)

    accepted_total = 0
    inserted_total = 0
    duplicates_total = 0
    validation_errors_total = 0
    sent_batches = 0
    previous_batch: list[dict[str, Any]] = []

    for index, batch in enumerate(batches, start=1):
        if realtime and previous_batch:
            sleep_seconds = realtime_sleep_seconds(previous_batch, batch, speed)
            if sleep_seconds > 0:
                time.sleep(min(sleep_seconds, 3.0))

        first_ts = batch[0]["timestamp"]
        last_ts = batch[-1]["timestamp"]

        if dry_run:
            print(f"[dry-run] batch={index}/{len(batches)} events={len(batch)} ts={first_ts}..{last_ts}")
            accepted_total += len(batch)
            inserted_total += len(batch)
            sent_batches += 1
        else:
            ingest_url = f"{api_base.rstrip('/')}/events/ingest"
            payload = {"events": batch}
            if session_id:
                payload["session_id"] = session_id
            response = http_json("POST", ingest_url, payload=payload)
            accepted = int(response.get("accepted", 0))
            inserted = int(response.get("inserted", 0))
            duplicates = int(response.get("duplicates", 0))
            validation_errors = len(response.get("validation_errors", []))
            accepted_total += accepted
            inserted_total += inserted
            duplicates_total += duplicates
            validation_errors_total += validation_errors
            sent_batches += 1
            print(
                f"batch={index}/{len(batches)} events={len(batch)} accepted={accepted} "
                f"inserted={inserted} duplicates={duplicates} validation_errors={validation_errors} "
                f"ts={first_ts}..{last_ts}"
            )

            if print_metrics:
                metrics_url = f"{api_base.rstrip('/')}/stores/{store_id}/metrics"
                if session_id:
                    metrics_url += f"?session_id={session_id}"
                metrics = http_json("GET", metrics_url)
                print(
                    f"  metrics visitors={metrics.get('unique_visitors')} "
                    f"conversion={metrics.get('conversion_rate_pct', 0)}% "
                    f"queue={metrics.get('queue_depth')}"
                )

        previous_batch = batch

    return {
        "session_id": session_id,
        "events_total": len(events),
        "batches_sent": sent_batches,
        "accepted_total": accepted_total,
        "inserted_total": inserted_total,
        "duplicates_total": duplicates_total,
        "validation_errors_total": validation_errors_total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay generated JSONL events into /events/ingest.")
    parser.add_argument("--events", type=Path, default=Path("data/events_phase5.jsonl"))
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--session-id", default=None)
    parser.add_argument(
        "--create-session",
        action="store_true",
        help="Create a backend session and attach replayed events to it.",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--speed", type=float, default=8.0, help="Realtime speedup. 8 means 8x faster than wall clock.")
    parser.add_argument("--print-metrics", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > 500:
        raise ValueError("batch-size must be in [1, 500]")
    if args.speed <= 0:
        raise ValueError("speed must be > 0")
    if not args.events.exists():
        raise FileNotFoundError(f"events file not found: {args.events}")

    result = replay(
        events_path=args.events,
        api_base=args.api,
        store_id=args.store_id,
        session_id=args.session_id,
        create_session=args.create_session,
        batch_size=args.batch_size,
        realtime=args.realtime,
        speed=args.speed,
        print_metrics=args.print_metrics,
        dry_run=args.dry_run,
    )
    print("summary", json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
