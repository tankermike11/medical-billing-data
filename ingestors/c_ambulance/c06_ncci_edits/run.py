"""NCCI PTP + MUE edits ingestor — manual import (Family C, C.6).

Sources:
  c06_ncci_ptp — Practitioner PTP (Column 1/Column 2) edits (AMA-licensed)
  c06_ncci_mue — Practitioner Services MUE (Medically Unlikely Edits)

PTP files require clicking through an AMA license agreement and cannot be automated.
MUE files are publicly available without a license gate.

Expected files under data/raw/c06_ncci/:
  PTP:  ccipra-v*-f{1-4}/ccipra-v*-f{1-4}.TXT   (4 tab-delimited files)
  MUE:  practitionerservicesmuetable-*/MCR_MUE_PractitionerServices_Eff_*.csv

To update for a new quarter:
  1. Extract new PTP ZIPs into data/raw/c06_ncci/ (replace old folders)
  2. Replace the MUE CSV folder with the new quarter's download
  3. Update QUARTER below to match the new quarter
  4. Delete the old dataset_releases rows for both source_ids, then re-run

Acceptance gate fixture:
  check_ncci_conflict('A0427', 'A0429', '2026-01-01') — verify against CMS source
  before wiring into a test.
"""
import csv
import re
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import record_release

SOURCE_PTP = "c06_ncci_ptp"
SOURCE_MUE = "c06_ncci_mue"
QUARTER = "2026Q3"

PTP_LANDING_URL = (
    "https://www.cms.gov/medicare/coding-billing/"
    "national-correct-coding-initiative-ncci-edits/"
    "medicare-ncci-procedure-procedure-ptp-edits"
)
MUE_LANDING_URL = (
    "https://www.cms.gov/medicare/coding-billing/"
    "national-correct-coding-initiative-ncci-edits/"
    "medicare-ncci-medically-unlikely-edits-mues"
)

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "c06_ncci"
PTP_HEADER_ROWS = 6
PTP_BATCH_SIZE = 5_000
MUE_BATCH_SIZE = 1_000
PTP_MIN_ROWS = 100_000
MUE_MIN_ROWS = 5_000

_MAI_NORM = {
    "1": "claim_line",
    "2": "date_of_service",
    "3": "per_patient_per_day",
}


def _norm_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if s in ("", "*", "0", "00000000", "99991231"):
        return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return s


def _date_from_filename(path: Path) -> str:
    """Extract YYYY-MM-DD from filenames like ..._Eff_07-01-2026.csv."""
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})", path.name)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    return "1900-01-01"


def _norm_mai(raw: str) -> str:
    leading = raw.strip()[:1]
    return _MAI_NORM.get(leading, raw.strip())


# ── PTP ──────────────────────────────────────────────────────────────────────

def _parse_ptp_file(path: Path) -> list[tuple]:
    rows: list[tuple] = []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for _ in range(PTP_HEADER_ROWS):
            next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            col1 = parts[0].strip()
            col2 = parts[1].strip()
            if not col1 or not col2:
                continue
            eff = _norm_date(parts[3]) or "1900-01-01"
            del_date = _norm_date(parts[4])
            try:
                mod_ind = int(parts[5].strip())
            except (ValueError, IndexError):
                mod_ind = 9
            edit_type = parts[6].strip() if len(parts) > 6 else ""
            edit_id = f"{col1}_{col2}_{eff}"
            rows.append((edit_id, col1, col2, mod_ind, eff, del_date, edit_type, QUARTER, SOURCE_PTP))
    return rows


def _ingest_ptp(conn) -> int:
    txt_files = sorted(p for p in RAW_DIR.rglob("*") if p.suffix.lower() == ".txt")
    if not txt_files:
        raise FileNotFoundError(
            f"No TXT files found under {RAW_DIR}. "
            "Extract the 4 practitioner PTP ZIPs into data/raw/c06_ncci/ and re-run."
        )

    all_rows: list[tuple] = []
    for path in txt_files:
        parsed = _parse_ptp_file(path)
        print(f"[{SOURCE_PTP}] {path.name}: {len(parsed):,} rows")
        all_rows.extend(parsed)

    print(f"[{SOURCE_PTP}] total: {len(all_rows):,} PTP rows across {len(txt_files)} files")

    if len(all_rows) < PTP_MIN_ROWS:
        raise ValueError(
            f"[{SOURCE_PTP}] Only {len(all_rows):,} rows — expected >= {PTP_MIN_ROWS:,}. "
            "Ensure all 4 TXT files are present."
        )

    for i in range(0, len(all_rows), PTP_BATCH_SIZE):
        conn.executemany(
            """INSERT OR REPLACE INTO ncci_ptp_edits
               (edit_id, column_one_code, column_two_code, modifier_indicator,
                effective_date, deletion_date, edit_type, quarter, source_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            all_rows[i : i + PTP_BATCH_SIZE],
        )
        conn.commit()

    release_id = record_release(
        conn, SOURCE_PTP, PTP_LANDING_URL, "manual", len(all_rows),
        notes=f"quarter={QUARTER}, files={len(txt_files)}",
    )
    print(f"[{SOURCE_PTP}] done — release #{release_id}")
    return len(all_rows)


# ── MUE ──────────────────────────────────────────────────────────────────────

def _find_mue_csv() -> Path:
    csv_files = [
        p for p in RAW_DIR.rglob("*.csv")
        if "mue" in p.parent.name.lower() or "mue" in p.name.lower()
    ]
    if not csv_files:
        raise FileNotFoundError(
            f"No MUE CSV found under {RAW_DIR}. "
            "Download the Practitioner Services MUE ZIP from the CMS MUE page "
            "and extract it into data/raw/c06_ncci/."
        )
    if len(csv_files) > 1:
        print(f"[{SOURCE_MUE}] multiple MUE CSVs found — using most recently modified: {csv_files[0].name}")
        csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return csv_files[0]


def _parse_mue_csv(path: Path) -> list[tuple]:
    eff_date = _date_from_filename(path)
    rows: list[tuple] = []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # AMA copyright block (single quoted multi-line cell)
        next(reader, None)  # column header row (HCPCS/\nCPT Code, ...)
        for parts in reader:
            if len(parts) < 3:
                continue
            hcpcs = parts[0].strip()
            if not hcpcs:
                continue
            try:
                mue_val = int(parts[1].strip())
            except (ValueError, IndexError):
                continue
            mai = _norm_mai(parts[2]) if len(parts) > 2 else "claim_line"
            mue_id = f"{hcpcs}_{eff_date}"
            rows.append((mue_id, hcpcs, mue_val, mai, eff_date, None, QUARTER, SOURCE_MUE))
    return rows


def _ingest_mue(conn) -> int:
    csv_path = _find_mue_csv()
    print(f"[{SOURCE_MUE}] parsing {csv_path.name}")

    rows = _parse_mue_csv(csv_path)
    print(f"[{SOURCE_MUE}] {len(rows):,} MUE rows")

    if len(rows) < MUE_MIN_ROWS:
        raise ValueError(
            f"[{SOURCE_MUE}] Only {len(rows):,} rows — expected >= {MUE_MIN_ROWS:,}. "
            "Check the MUE CSV file."
        )

    for i in range(0, len(rows), MUE_BATCH_SIZE):
        conn.executemany(
            """INSERT OR REPLACE INTO ncci_mue
               (mue_id, hcpcs_code, mue_value, mue_adjudication_indicator,
                effective_date, deletion_date, quarter, source_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows[i : i + MUE_BATCH_SIZE],
        )
        conn.commit()

    release_id = record_release(
        conn, SOURCE_MUE, MUE_LANDING_URL, "manual", len(rows),
        notes=f"quarter={QUARTER}, effective={_date_from_filename(csv_path)}",
    )
    print(f"[{SOURCE_MUE}] done — release #{release_id}")
    return len(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    conn = get_conn()
    ptp_count = _ingest_ptp(conn)
    mue_count = _ingest_mue(conn)
    print(f"\n[c06_ncci_edits] complete — {ptp_count:,} PTP rows, {mue_count:,} MUE rows")


if __name__ == "__main__":
    main()
