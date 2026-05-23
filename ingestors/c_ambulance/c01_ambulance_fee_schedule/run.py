"""Medicare Ground-Ambulance Fee Schedule ingestor (Family C).

Source: American Ambulance Association reformatted CMS CY2025 AFS PUF.
  canonical_url: ambulance.org/wp-content/uploads/2025/01/2025.AmbulanceFeeSchedule.StateLocalHeadings.Revised.xlsx
  Raw CMS landing page: cms.gov/medicare/payment/fee-schedules/ambulance/ambulance-fee-schedule-public-use-files

File structure (confirmed 2026-05-23):
  Row 1  — header: CONTRACTOR/CARRIER | LOCALITY | State/Local Headings |
            HCPCS | RVU | GPCI | BASE RATE | URBAN BASE RATE / URBAN MILEAGE |
            RURAL BASE RATE / RURAL MILEAGE | RURAL BASE RATE / LOWEST QUARTILE |
            RURAL GROUND MILES 1-17*
  String row — CONTRACTOR/CARRIER is a region name (e.g. "Northern California");
               all other cells are None. Marks the start of a new carrier region.
  Data rows  — CONTRACTOR/CARRIER is a numeric carrier code; LOCALITY is a number;
               State/Local Headings is a locality name (or None for continuation rows).

State extraction: derived from the carrier region name via REGION_TO_STATE, not
from a data column. "Northern California" and "Southern California" both map to CA
and are merged when aggregating to state level.

Rate used: URBAN BASE RATE / URBAN MILEAGE (col 7). Falls back to BASE RATE (col 6)
when the urban rate is 'n/a'. We aggregate locality rates up to the state level using
the median, which gives a robust negotiation-anchor reference without reconstructing
the full Medicare payment formula.

Reference rates stored as INTEGER CENTS (deliberate exception to the project's
raw-string convention — required for arithmetic in the advocacy app).
"""
import statistics
import zipfile
import csv
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path
import sqlite3

import openpyxl

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "c01_ambulance_fee_schedule"
EFFECTIVE_YEAR = 2025

TARGET_HCPCS = {
    "A0425", "A0426", "A0427", "A0428", "A0429",
    "A0430", "A0431", "A0432", "A0433", "A0434",
}

# Maps carrier region names (as they appear in the AAA file) to 2-letter state codes.
# Northern/Southern California both map to CA and are merged at aggregation time.
REGION_TO_STATE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "Northern California": "CA", "Southern California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Puerto Rico/U.S. Virgin Islands": "PR",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


def _parse_rate(value) -> float | None:
    if value is None or str(value).strip().lower() in ("n/a", "", "-"):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _open_data_file(local_path: Path):
    """Return (header_list, rows_list) supporting ZIP/XLSX/CSV."""
    suffix = local_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(local_path) as zf:
            csv_names = [n for n in zf.namelist()
                         if n.lower().endswith((".csv", ".txt")) and not n.startswith("__")]
            if not csv_names:
                raise FileNotFoundError(f"No CSV/TXT in {local_path.name}: {zf.namelist()}")
            csv_name = max(csv_names, key=lambda n: zf.getinfo(n).file_size)
            print(f"[{SOURCE_ID}] reading {csv_name} from ZIP")
            with zf.open(csv_name) as raw:
                reader = csv.reader(TextIOWrapper(raw, encoding="latin-1"))
                header = next(reader)
                rows = list(reader)
        return header, rows
    elif suffix in (".xlsx", ".xls"):
        wb = openpyxl.load_workbook(local_path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header = [str(c or "").strip() for c in next(rows_iter)]
        rows = list(rows_iter)   # keep raw values (not stringified) for type-based region detection
        wb.close()
        return header, rows
    else:
        with open(local_path, encoding="latin-1", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
        return header, rows


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    header, rows = _open_data_file(local_path)

    # Locate columns by name (case-insensitive)
    header_upper = [str(h or "").strip().upper() for h in header]
    def col(name: str) -> int | None:
        try:
            return header_upper.index(name.upper())
        except ValueError:
            return None

    hcpcs_col       = col("HCPCS")
    urban_rate_col  = col("URBAN BASE RATE / URBAN MILEAGE")
    base_rate_col   = col("BASE RATE")
    carrier_col     = col("CONTRACTOR/CARRIER")   # 0

    missing = [n for n, i in [("HCPCS", hcpcs_col), ("URBAN BASE RATE / URBAN MILEAGE", urban_rate_col),
                               ("BASE RATE", base_rate_col), ("CONTRACTOR/CARRIER", carrier_col)]
               if i is None]
    if missing:
        raise ValueError(f"[{SOURCE_ID}] Missing columns: {missing}. Header: {header}")

    # Aggregate rates by (hcpcs, state)
    rates_by: dict[tuple[str, str], list[float]] = defaultdict(list)
    current_state: str | None = None
    skipped_region = 0
    skipped_rate = 0

    for row in rows:
        carrier_val = row[carrier_col]

        # Region header row: CONTRACTOR/CARRIER is a string, all else None
        if isinstance(carrier_val, str):
            region = carrier_val.strip()
            current_state = REGION_TO_STATE.get(region)
            if current_state is None:
                print(f"[{SOURCE_ID}] WARNING: unknown region '{region}' — add to REGION_TO_STATE")
            continue

        # Skip rows without a resolved state
        if current_state is None:
            skipped_region += 1
            continue

        hcpcs = str(row[hcpcs_col] or "").strip().upper() if hcpcs_col < len(row) else ""
        if not hcpcs or hcpcs not in TARGET_HCPCS:
            continue

        # Prefer urban rate; fall back to base rate
        rate = _parse_rate(row[urban_rate_col] if urban_rate_col < len(row) else None)
        if rate is None:
            rate = _parse_rate(row[base_rate_col] if base_rate_col < len(row) else None)
        if rate is None or rate <= 0:
            skipped_rate += 1
            continue

        rates_by[(hcpcs, current_state)].append(rate)

    if skipped_region:
        print(f"[{SOURCE_ID}] skipped {skipped_region} rows with unresolved state")
    if skipped_rate:
        print(f"[{SOURCE_ID}] skipped {skipped_rate} rows with missing/zero rate")

    db_rows = [
        (hcpcs, "state", state, round(statistics.median(rate_list) * 100), EFFECTIVE_YEAR, SOURCE_ID)
        for (hcpcs, state), rate_list in sorted(rates_by.items())
    ]

    if not db_rows:
        raise ValueError(
            f"[{SOURCE_ID}] 0 rows parsed — check REGION_TO_STATE mapping and TARGET_HCPCS set."
        )

    conn.executemany(
        """INSERT OR REPLACE INTO ambulance_fee_schedule
           (hcpcs, geo_level, geo_key, reference_rate, effective_year, source_id)
           VALUES (?,?,?,?,?,?)""",
        db_rows,
    )
    conn.commit()

    states = {r[2] for r in db_rows}
    codes  = {r[0] for r in db_rows}
    print(f"[{SOURCE_ID}] {len(db_rows)} rows loaded "
          f"({len(codes)} HCPCS codes × {len(states)} states/territories)")
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
