# scripts/import_pos.py
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import StoreDatabase


IST = timezone(timedelta(hours=5, minutes=30), name="IST")
UTC = timezone.utc

# Map raw store codes from CSV to canonical store_id used in the project
STORE_ID_MAP: dict[str, str] = {
    "ST1008": "STORE_BLR_002",
    "ST1076": "STORE_BLR_002",
    "store_1076": "STORE_BLR_002",
    "STORE_BLR_002": "STORE_BLR_002",
}

DATE_TIME_FORMATS = (
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def normalise_store_id(raw: str) -> str:
    return STORE_ID_MAP.get(raw.strip(), raw.strip())


def parse_datetime_ist(date_str: str, time_str: str) -> str:
    """Parse date+time strings in either DD-MM-YYYY or DD-04-YYYY format, return UTC ISO string."""
    date_str = date_str.strip()
    time_str = time_str.strip()
    # Try DD-MM-YYYY HH:MM:SS first, then 10-04-2026 style (same format, different separator)
    for fmt in ("%d-%m-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            local_dt = datetime.strptime(
                f"{date_str} {time_str}", f"{fmt} %H:%M:%S"
            ).replace(tzinfo=IST)
            return local_dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date/time: '{date_str}' '{time_str}'")


def parse_timezone_offset(value: str | None) -> timezone:
    raw = (value or "+05:30").strip()
    if raw.upper() in {"UTC", "Z"}:
        return UTC
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", raw)
    if not match:
        raise ValueError(f"Cannot parse timezone offset: '{raw}'. Use +05:30 or UTC.")
    sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        offset = -offset
    return timezone(offset, name=raw)


def parse_money(value: str | int | float | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", value.strip())
    return float(cleaned or 0)


def parse_datetime_with_timezone(
    *,
    timestamp_value: str | None = None,
    date_value: str | None = None,
    time_value: str | None = None,
    source_tz: timezone = IST,
) -> str:
    if timestamp_value:
        raw = timestamp_value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError(f"Cannot parse timestamp: '{raw}'")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=source_tz)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    combined = f"{(date_value or '').strip()} {(time_value or '').strip()}".strip()
    for fmt in DATE_TIME_FORMATS:
        try:
            return (
                datetime.strptime(combined, fmt)
                .replace(tzinfo=source_tz)
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date/time: '{date_value}' '{time_value}'")


def parse_mapped_csv(
    path: Path,
    *,
    transaction_id_column: str,
    amount_column: str,
    store_id_column: str | None = None,
    timestamp_column: str | None = None,
    date_column: str | None = None,
    time_column: str | None = None,
    timezone_offset: str | None = "+05:30",
    default_store_id: str = "STORE_BLR_002",
) -> list[dict]:
    """
    Generic POS CSV parser used by the dashboard's advanced mapping workflow.

    The default Purplle parser remains the zero-config path. This fallback lets
    operators connect a different POS export without code changes.
    """
    source_tz = parse_timezone_offset(timezone_offset)
    grouped: dict[str, dict] = {}
    totals: dict[str, float] = defaultdict(float)

    if not timestamp_column and not (date_column and time_column):
        raise ValueError("Provide either timestamp_column or both date_column and time_column")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        required = {transaction_id_column, amount_column}
        if timestamp_column:
            required.add(timestamp_column)
        else:
            required.update({date_column or "", time_column or ""})
        if store_id_column:
            required.add(store_id_column)
        missing = sorted(column for column in required if column and column not in fieldnames)
        if missing:
            raise ValueError(f"CSV is missing mapped column(s): {', '.join(missing)}")

        for row in reader:
            txn_key = (row.get(transaction_id_column) or "").strip()
            if not txn_key:
                continue
            raw_store_id = (row.get(store_id_column or "") or default_store_id).strip() or default_store_id
            store_id = normalise_store_id(raw_store_id)
            timestamp = parse_datetime_with_timezone(
                timestamp_value=row.get(timestamp_column or "") if timestamp_column else None,
                date_value=row.get(date_column or "") if date_column else None,
                time_value=row.get(time_column or "") if time_column else None,
                source_tz=source_tz,
            )
            totals[txn_key] += parse_money(row.get(amount_column))
            if txn_key not in grouped:
                grouped[txn_key] = {
                    "transaction_id": txn_key,
                    "store_id": store_id,
                    "timestamp": timestamp,
                    "metadata": {
                        "source": "mapped_pos_csv",
                        "raw_store_id": raw_store_id,
                        "mapping": {
                            "transaction_id_column": transaction_id_column,
                            "amount_column": amount_column,
                            "timestamp_column": timestamp_column,
                            "date_column": date_column,
                            "time_column": time_column,
                            "store_id_column": store_id_column,
                            "timezone_offset": timezone_offset,
                        },
                    },
                }

    transactions = [
        {**base, "basket_value_inr": round(totals[key], 2)}
        for key, base in grouped.items()
    ]
    transactions.sort(key=lambda t: t["timestamp"])
    return transactions


def parse_purplle_csv(path: Path) -> list[dict]:
    """
    Handles two POS CSV formats:

    Format A (old Brigade Road real data):
      invoice_number, order_id, order_date, order_time, store_id,
      customer_number, salesperson_id, NMV, ...

    Format B (new sample_transactions):
      order_id, order_date, order_time, store_id,
      product_id, brand_name, total_amount
    """
    grouped: dict[str, dict] = {}
    totals: dict[str, float] = defaultdict(float)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []

        has_invoice = "invoice_number" in fieldnames
        has_nmv = "NMV" in fieldnames
        has_total_amount = "total_amount" in fieldnames

        for row in reader:
            # --- Determine transaction key ---
            if has_invoice:
                txn_key = (row.get("invoice_number") or "").strip()
            else:
                # Format B: group by store_id + order_date + order_time
                # (multiple line items share the same timestamp = one transaction)
                raw_store = (row.get("store_id") or "").strip()
                txn_key = f"{raw_store}_{row.get('order_date','').strip()}_{row.get('order_time','').strip()}"

            if not txn_key or txn_key.startswith("_"):
                continue

            # --- Parse timestamp ---
            timestamp = parse_datetime_ist(
                row.get("order_date", ""),
                row.get("order_time", ""),
            )

            # --- Normalise store_id ---
            raw_store_id = (row.get("store_id") or "STORE_BLR_002").strip()
            store_id = normalise_store_id(raw_store_id)

            # --- Amount ---
            if has_nmv:
                amount = float(row.get("NMV") or 0)
            elif has_total_amount:
                amount = float(row.get("total_amount") or 0)
            else:
                amount = 0.0

            totals[txn_key] += amount

            if txn_key not in grouped:
                meta: dict = {"source": "purplle_pos_csv", "raw_store_id": raw_store_id}
                if has_invoice:
                    meta["order_id"] = row.get("order_id", "").strip()
                    meta["customer_number"] = row.get("customer_number", "").strip()
                    meta["salesperson_id"] = row.get("salesperson_id", "").strip()
                else:
                    meta["order_id"] = row.get("order_id", "").strip()
                    meta["product_ids"] = []
                    meta["brands"] = []

                grouped[txn_key] = {
                    "transaction_id": txn_key,
                    "store_id": store_id,
                    "timestamp": timestamp,
                    "metadata": meta,
                }

            # Accumulate line-item detail for Format B
            if not has_invoice:
                grouped[txn_key]["metadata"]["product_ids"].append(
                    row.get("product_id", "").strip()
                )
                grouped[txn_key]["metadata"]["brands"].append(
                    row.get("brand_name", "").strip()
                )

    transactions = []
    for key, base in grouped.items():
        transactions.append({**base, "basket_value_inr": round(totals[key], 2)})

    transactions.sort(key=lambda t: t["timestamp"])
    return transactions


def main() -> int:
    parser = argparse.ArgumentParser(description="Import POS transactions into SQLite.")
    parser.add_argument("--csv", type=Path, required=True, help="Path to POS CSV file")
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "data" / "store_intelligence.sqlite3",
    )
    parser.add_argument(
        "--store-id",
        type=str,
        default=None,
        help="Override store_id for all rows (e.g. STORE_BLR_002)",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}", file=sys.stderr)
        return 1

    transactions = parse_purplle_csv(args.csv)

    if args.store_id:
        for t in transactions:
            t["store_id"] = args.store_id

    db = StoreDatabase(args.db)
    db.initialize()
    count = db.upsert_pos_transactions(transactions)
    print(f"Imported {count} POS transactions into {args.db}")
    print(f"Store IDs found: {sorted({t['store_id'] for t in transactions})}")
    print(f"Date range: {transactions[0]['timestamp']} -> {transactions[-1]['timestamp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
