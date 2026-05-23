"""Medicare Ground-Ambulance Fee Schedule ingestor (Family C).

Source: CMS Ambulance Fee Schedule annual public use file.
Landing page: cms.gov/medicare/medicare-fee-for-service-payment/ambulancefeeSchedule/

The CMS file is organized by locality/carrier (MAC jurisdictions). This ingestor
aggregates locality-level payment amounts up to the state level using the median,
which is the recommended approach for a negotiation-anchor reference rate (per the
Addendum v1.3) — it avoids reconstructing the Medicare payment formula while still
providing a meaningful state-level figure.

Column names vary by CMS release year. The ingestor uses case-insensitive flexible
matching (see _find_col). Confirm actual column names after downloading the file by
inspecting the header row, then update _COL_CANDIDATES below if needed.

HCPCS in scope: A0425–A0434 (ground-ambulance transport levels + mileage codes).
These are cross-referenced against the codes table (code_type='HCPCS') at runtime
as a sanity check but are not required to be present — the fee schedule is authoritative.

rates stored as INTEGER CENTS (deliberate exception to the project's raw-string convention).
"""
import csv
import statistics
import zipfile
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path
import sqlite3

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "c01_ambulance_fee_schedule"
EFFECTIVE_YEAR = 2025

TARGET_HCPCS = {
    "A0425", "A0426", "A0427", "A0428", "A0429",
    "A0430", "A0431", "A0432", "A0433", "A0434",
}

# Candidate column names for each logical field, in priority order.
# Update after inspecting the downloaded file's header row.
_COL_CANDIDATES = {
    "hcpcs":   ["HCPCS_CODE", "HCPCS", "PROC_CODE", "HCPCS CODE"],
    "state":   ["STATE", "STATE_CD", "STATE_CODE", "ST"],
    "rate":    ["NON_FACILITY_PRICE", "PAYMENT_RATE", "RATE_AMOUNT",
                "FACILITY_PRICE", "AMOUNT", "RATE"],
}


def _build_col_map(header: list[str]) -> dict[str, int]:
    return {h.strip().upper(): i for i, h in enumerate(header)}


def _find_col(cm: dict[str, int], candidates: list[str]) -> int | None:
    for name in candidates:
        idx = cm.get(name.upper())
        if idx is not None:
            return idx
    return None


def _get(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def _parse_dollars(value: str) -> float | None:
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _open_data_file(local_path: Path):
    """Yield (header_list, rows_iterator) from a ZIP → CSV/TSV, handling encoding."""
    suffix = local_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(local_path) as zf:
            csv_names = [n for n in zf.namelist()
                         if n.lower().endswith((".csv", ".txt")) and not n.startswith("__")]
            if not csv_names:
                raise FileNotFoundError(f"No CSV/TXT found in {local_path.name}; found: {zf.namelist()}")
            # prefer the largest file (usually the data file, not a readme)
            csv_name = max(csv_names, key=lambda n: zf.getinfo(n).file_size)
            print(f"[{SOURCE_ID}] reading {csv_name} from ZIP")
            with zf.open(csv_name) as raw:
                reader = csv.reader(TextIOWrapper(raw, encoding="latin-1"))
                header = next(reader)
                rows = list(reader)
        return header, rows
    else:
        # plain CSV/TSV
        with open(local_path, encoding="latin-1", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
        return header, rows


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    header, rows = _open_data_file(local_path)
    cm = _build_col_map(header)

    hcpcs_col = _find_col(cm, _COL_CANDIDATES["hcpcs"])
    state_col  = _find_col(cm, _COL_CANDIDATES["state"])
    rate_col   = _find_col(cm, _COL_CANDIDATES["rate"])

    missing = [k for k, idx in [("hcpcs", hcpcs_col), ("state", state_col), ("rate", rate_col)]
               if idx is None]
    if missing:
        raise ValueError(
            f"[{SOURCE_ID}] Could not find columns for: {missing}. "
            f"Header row: {header[:20]}. "
            f"Update _COL_CANDIDATES in run.py to match the actual file."
        )

    # Aggregate: rates_by[(hcpcs, state)] = [rate_float, ...]
    rates_by: dict[tuple[str, str], list[float]] = defaultdict(list)
    skipped = 0
    for row in rows:
        hcpcs = _get(row, hcpcs_col).upper()
        if hcpcs not in TARGET_HCPCS:
            continue
        state = _get(row, state_col).upper()
        if not state or len(state) > 3:
            skipped += 1
            continue
        rate = _parse_dollars(_get(row, rate_col))
        if rate is None or rate <= 0:
            skipped += 1
            continue
        rates_by[(hcpcs, state)].append(rate)

    if skipped:
        print(f"[{SOURCE_ID}] skipped {skipped} rows (missing/invalid state or rate)")

    db_rows = []
    for (hcpcs, state), rate_list in sorted(rates_by.items()):
        median_cents = round(statistics.median(rate_list) * 100)
        db_rows.append((hcpcs, "state", state, median_cents, EFFECTIVE_YEAR, SOURCE_ID))

    if not db_rows:
        raise ValueError(
            f"[{SOURCE_ID}] 0 rows parsed from {local_path.name} — "
            "check that _COL_CANDIDATES matches the file's column names and "
            "that TARGET_HCPCS codes are present."
        )

    conn.executemany(
        """INSERT OR REPLACE INTO ambulance_fee_schedule
           (hcpcs, geo_level, geo_key, reference_rate, effective_year, source_id)
           VALUES (?,?,?,?,?,?)""",
        db_rows,
    )
    conn.commit()
    print(f"[{SOURCE_ID}] {len(db_rows)} state-level rates loaded "
          f"({len({r[0] for r in db_rows})} HCPCS codes × {len({r[2] for r in db_rows})} states)")
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
