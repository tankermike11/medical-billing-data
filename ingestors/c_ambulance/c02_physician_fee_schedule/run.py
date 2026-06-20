"""Medicare Physician Fee Schedule national payment amount ingestor (Family C).

Source: CMS CY2025 PFS National Payment Amount file (PFREV25A / PFALL25.txt)
  canonical_url in source_registry.yaml: cms.gov/files/zip/pfrev25a.zip
  Landing page: cms.gov/medicare/payment/fee-schedules/physician/national-payment-amount-file

File structure (confirmed CY2025, PFALL25.txt inside pfrev25a.zip):
  Comma-delimited, quoted fields, NO header row.
  Positional columns:
    [0]  year          (e.g. "2025")
    [1]  carrier       (MAC contractor number, e.g. "01112")
    [2]  locality      (2-digit locality code)
    [3]  hcpcs         (HCPCS/CPT code)
    [4]  modifier      (space-padded 2 chars; strip to get modifier or "")
    [5]  non_fac_price (non-facility allowed amount, format "0000031.56")
    [6]  fac_price     (facility allowed amount, format "0000027.89")
    [7]  non_fac_na    (" "=applicable, "1"=not applicable)
    [8]  fac_na        (" "=applicable, "1"=not applicable)
    [9]  status_code   ("A"=active, "R"=restricted, "T"=injectable)
    [10-15] other columns (not used)

  ~995K rows: 55 carriers × many localities × 7,481 HCPCS codes.
  Status codes present in CY2025 file: A, R, T (no deleted/D rows).

Aggregation: national MEDIAN across all carrier×locality rows per (hcpcs, modifier).
  Rates of 0.00 are excluded from the median (indicates N/A for that setting).
  Stored as INTEGER CENTS. NULL where no non-zero rate exists for that setting.

No AMA CPT descriptions ingested — same carve-out as a04_cpt_handling.
"""
import csv
import statistics
import zipfile
from collections import defaultdict
from io import TextIOWrapper
from pathlib import Path
import sqlite3

from ingestors.core.pipeline import run_ingestor

SOURCE_ID = "c02_physician_fee_schedule"
EFFECTIVE_YEAR = 2025

# Positional column indices in PFALL file (confirmed CY2025)
COL_HCPCS    = 3
COL_MOD      = 4
COL_NON_FAC  = 5
COL_FAC      = 6
COL_STATUS   = 9
MIN_COLS     = 10


def _parse_price(value: str) -> float | None:
    s = value.strip()
    if not s:
        return None
    try:
        f = float(s)
        return f if f > 0.0 else None
    except ValueError:
        return None


def _load_rows(local_path: Path) -> list[list[str]]:
    with zipfile.ZipFile(local_path) as zf:
        txt_files = [n for n in zf.namelist() if n.upper().endswith(".TXT")]
        if not txt_files:
            raise FileNotFoundError(
                f"[{SOURCE_ID}] No .txt file in {local_path.name}. Contents: {zf.namelist()}"
            )
        data_file = max(txt_files, key=lambda n: zf.getinfo(n).file_size)
        print(f"[{SOURCE_ID}] reading {data_file} from ZIP")
        with zf.open(data_file) as raw:
            reader = csv.reader(TextIOWrapper(raw, encoding="latin-1"))
            return list(reader)


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    rows = _load_rows(local_path)
    print(f"[{SOURCE_ID}] {len(rows):,} raw rows loaded")

    # Accumulate non-facility and facility prices per (hcpcs, modifier)
    non_fac_by: dict[tuple[str, str], list[float]] = defaultdict(list)
    fac_by:     dict[tuple[str, str], list[float]] = defaultdict(list)
    status_by:  dict[tuple[str, str], str]         = {}

    skipped_short = 0
    for row in rows:
        if len(row) < MIN_COLS:
            skipped_short += 1
            continue

        hcpcs    = row[COL_HCPCS].strip().upper()
        modifier = row[COL_MOD].strip()
        status   = row[COL_STATUS].strip().upper()

        if not hcpcs:
            skipped_short += 1
            continue

        key = (hcpcs, modifier)
        # Last status seen is fine; all rows for a given code have the same status
        status_by[key] = status

        nf = _parse_price(row[COL_NON_FAC])
        if nf is not None:
            non_fac_by[key].append(nf)

        fa = _parse_price(row[COL_FAC])
        if fa is not None:
            fac_by[key].append(fa)

    if skipped_short:
        print(f"[{SOURCE_ID}] skipped {skipped_short:,} short/empty rows")

    all_keys = set(non_fac_by) | set(fac_by)
    db_rows = []
    for key in sorted(all_keys):
        hcpcs, modifier = key
        nf_list = non_fac_by.get(key, [])
        fa_list = fac_by.get(key, [])
        non_fac_cents = round(statistics.median(nf_list) * 100) if nf_list else None
        fac_cents     = round(statistics.median(fa_list) * 100) if fa_list else None
        status        = status_by.get(key)
        db_rows.append((hcpcs, modifier, non_fac_cents, fac_cents, status, EFFECTIVE_YEAR, SOURCE_ID))

    if not db_rows:
        raise ValueError(f"[{SOURCE_ID}] 0 rows to insert — check column indices or file format.")

    BATCH = 5_000
    for i in range(0, len(db_rows), BATCH):
        conn.executemany(
            """INSERT OR REPLACE INTO physician_fee_schedule
               (hcpcs, modifier, non_fac_rate, fac_rate, status_code, effective_year, source_id)
               VALUES (?,?,?,?,?,?,?)""",
            db_rows[i : i + BATCH],
        )
        conn.commit()

    codes = len({r[0] for r in db_rows})
    print(f"[{SOURCE_ID}] {len(db_rows):,} rows loaded "
          f"({codes:,} unique HCPCS/CPT codes, national median rates)")
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
