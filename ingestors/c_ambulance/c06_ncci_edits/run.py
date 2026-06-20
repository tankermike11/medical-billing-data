"""NCCI edits ingestor — PTP + MUE (Family C, C.6).

Sources (same ZIP, two source_ids):
  c06_ncci_ptp — Procedure-to-Procedure edits (physician + outpatient/ASC tables)
  c06_ncci_mue — Medically Unlikely Edits

Downloads the quarterly CMS NCCI ZIP from c06_ncci_ptp's canonical_url (both
source_ids share the same ZIP). Parses PTP and MUE files with dynamic header
detection (CMS changes column names between release years). Records two separate
dataset_releases entries, one per source_id.

IMPORTANT: update canonical_url in source_registry.yaml each quarter (Jan, Apr, Jul, Oct).
The schema drift guard will FAIL FAST if CMS changes column names — update
_PRIOR_HEADERS_PATH and the column alias maps before re-running after a surprise drift.

Acceptance gate fixture:
  check_ncci_conflict('A0427', 'A0429', '2026-01-01') — verify against CMS source
  before wiring this into a test. The expected result changes with each quarterly release.
"""
import csv
import json
import re
import sqlite3
import zipfile
from io import TextIOWrapper
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.http import download, _sha256
from ingestors.core.pipeline import load_registry, record_release, record_error, now_utc

SOURCE_PTP = "c06_ncci_ptp"
SOURCE_MUE = "c06_ncci_mue"

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "c06_ncci_edits"
_PRIOR_HEADERS_PATH = RAW_DIR / "prior_headers.json"

# Column name candidates for PTP files (lower-cased)
_PTP_COLS: dict[str, list[str]] = {
    "col1":       ["col 1", "column 1", "column one", "col1", "column_1",
                   "col 1 hcpcs", "column 1 hcpcs/cpt", "hcpcs/cpt code col 1"],
    "col2":       ["col 2", "column 2", "column two", "col2", "column_2",
                   "col 2 hcpcs", "column 2 hcpcs/cpt", "hcpcs/cpt code col 2"],
    "modifier":   ["modifier indicator", "modifier_indicator", "mod indicator",
                   "modif indicator", "modifier ind"],
    "eff_date":   ["effective date", "effective_date", "eff date", "effdate",
                   "date effective"],
    "del_date":   ["deletion date", "deletion_date", "del date", "deldate",
                   "termination date"],
}

# Column name candidates for MUE files (lower-cased)
_MUE_COLS: dict[str, list[str]] = {
    "hcpcs":      ["hcpcs/cpt code", "hcpcs code", "hcpcs_code", "code", "cpt code"],
    "mue_value":  ["mue values", "mue value", "mue_value", "mue", "mai values"],
    "mai":        ["mai", "mue adjudication indicator", "adjudication indicator",
                   "mue_adjudication_indicator"],
    "eff_date":   ["effective date", "effective_date", "eff date"],
    "del_date":   ["deletion date", "deletion_date", "del date", "termination date"],
}

_MAI_NORM = {
    "1": "claim_line",
    "2": "date_of_service",
    "3": "per_patient_per_day",
    "claim line": "claim_line",
    "date of service": "date_of_service",
    "per patient per day": "per_patient_per_day",
}


def _resolve_col(header_lower: list[str], candidates: list[str]) -> int | None:
    for c in candidates:
        try:
            return header_lower.index(c)
        except ValueError:
            pass
    for i, h in enumerate(header_lower):
        for c in candidates:
            if c in h:
                return i
    return None


def _norm_date(raw: str | None) -> str | None:
    if not raw or not raw.strip() or raw.strip() in ("0", "00000000", "99991231"):
        return None
    s = raw.strip()
    # YYYYMMDD → YYYY-MM-DD
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    return s


def _infer_quarter(path: Path) -> str:
    """Extract quarter string from filename or directory, e.g. '2026Q2'."""
    text = path.name + " " + str(path.parent)
    m = re.search(r"(20\d{2})[-_. ]?[Qq]?(\d)", text)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.search(r"(january|april|july|october)[- _]?(20\d{2})", text, re.IGNORECASE)
    if m:
        month_q = {"january": "Q1", "april": "Q2", "july": "Q3", "october": "Q4"}
        return f"{m.group(2)}{month_q[m.group(1).lower()]}"
    return "UNKNOWN"


def _check_schema_drift(label: str, current_headers: list[str]) -> None:
    """Fail fast if column names changed since last run."""
    if not _PRIOR_HEADERS_PATH.exists():
        return  # First run — no baseline to compare against
    prior = json.loads(_PRIOR_HEADERS_PATH.read_text())
    prior_set = set(prior.get(label, []))
    curr_set = set(current_headers)
    added = curr_set - prior_set
    removed = prior_set - curr_set
    if added or removed:
        raise ValueError(
            f"[c06_ncci_edits] SCHEMA DRIFT in {label}: "
            f"added={sorted(added)}, removed={sorted(removed)}. "
            f"Update column alias maps in run.py before re-running."
        )


def _save_headers(headers_by_label: dict[str, list[str]]) -> None:
    _PRIOR_HEADERS_PATH.write_text(json.dumps(headers_by_label, indent=2))


def _open_csv_files(zip_path: Path) -> dict[str, tuple[list[str], list]]:
    """Return {label: (header, rows)} for each CSV found in the ZIP."""
    result: dict[str, tuple[list[str], list]] = {}
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv") and not n.startswith("__")]
        for name in csv_names:
            label = Path(name).stem.lower()
            with zf.open(name) as raw:
                reader = csv.reader(TextIOWrapper(raw, encoding="utf-8-sig", errors="replace"))
                header = next(reader, None)
                if header is None:
                    continue
                rows = list(reader)
            result[label] = (header, rows)
            print(f"[c06_ncci_edits] found {name}: {len(rows)} rows, {len(header)} cols")
    return result


def _is_ptp_file(label: str, header_lower: list[str]) -> bool:
    ptp_signals = ["col 1", "column 1", "col1", "column one"]
    return any(any(sig in h for sig in ptp_signals) for h in header_lower)


def _is_mue_file(label: str, header_lower: list[str]) -> bool:
    mue_signals = ["mue value", "mue values", "mue_value", "mai"]
    return any(any(sig in h for sig in mue_signals) for h in header_lower)


def _parse_ptp(label: str, header: list[str], rows: list, quarter: str, edit_type: str) -> list[tuple]:
    header_lower = [h.strip().lower() for h in header]
    _check_schema_drift(label, header_lower)

    cols = {f: _resolve_col(header_lower, cands) for f, cands in _PTP_COLS.items()}
    missing = [f for f in ("col1", "col2", "modifier") if cols.get(f) is None]
    if missing:
        raise ValueError(f"[c06_ncci_edits] {label}: cannot resolve PTP columns {missing}. Header: {header}")

    db_rows: list[tuple] = []
    for row in rows:
        def cell(f: str) -> str | None:
            i = cols.get(f)
            return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else None

        col1 = cell("col1") or ""
        col2 = cell("col2") or ""
        if not col1 or not col2:
            continue

        eff = _norm_date(cell("eff_date")) or "1900-01-01"
        edit_id = f"{col1}_{col2}_{eff}"

        try:
            mod_ind = int(cell("modifier") or "9")
        except (ValueError, TypeError):
            mod_ind = 9

        db_rows.append((
            edit_id, col1, col2, mod_ind,
            eff, _norm_date(cell("del_date")),
            edit_type, quarter, SOURCE_PTP,
        ))

    return db_rows


def _parse_mue(label: str, header: list[str], rows: list, quarter: str) -> list[tuple]:
    header_lower = [h.strip().lower() for h in header]
    _check_schema_drift(label, header_lower)

    cols = {f: _resolve_col(header_lower, cands) for f, cands in _MUE_COLS.items()}
    missing = [f for f in ("hcpcs", "mue_value") if cols.get(f) is None]
    if missing:
        raise ValueError(f"[c06_ncci_edits] {label}: cannot resolve MUE columns {missing}. Header: {header}")

    db_rows: list[tuple] = []
    for row in rows:
        def cell(f: str) -> str | None:
            i = cols.get(f)
            return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else None

        hcpcs = cell("hcpcs") or ""
        if not hcpcs:
            continue

        eff = _norm_date(cell("eff_date")) or "1900-01-01"
        mue_id = f"{hcpcs}_{eff}"

        try:
            mue_val = int(cell("mue_value") or "0")
        except (ValueError, TypeError):
            mue_val = 0

        mai_raw = (cell("mai") or "").strip().lower()
        mai = _MAI_NORM.get(mai_raw, mai_raw or "claim_line")

        db_rows.append((
            mue_id, hcpcs, mue_val, mai,
            eff, _norm_date(cell("del_date")),
            quarter, SOURCE_MUE,
        ))

    return db_rows


def main():
    registry = load_registry()
    if SOURCE_PTP not in registry:
        raise KeyError(f"source_id '{SOURCE_PTP}' not found in source_registry.yaml")

    zip_url = registry[SOURCE_PTP]["canonical_url"]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    zip_dest = RAW_DIR / Path(zip_url).name

    print(f"[c06_ncci_edits] fetching {zip_url}")
    zip_path, file_hash = download(zip_url, zip_dest)

    conn = get_conn()

    # Hash-check against last successful PTP release
    last = conn.execute(
        "SELECT file_hash FROM dataset_releases WHERE source_id=? AND status='success' ORDER BY id DESC LIMIT 1",
        (SOURCE_PTP,),
    ).fetchone()
    if last and last["file_hash"] == file_hash:
        print("[c06_ncci_edits] unchanged (hash match) — skipping parse")
        return

    quarter = _infer_quarter(zip_path)
    print(f"[c06_ncci_edits] quarter: {quarter}")

    csv_files = _open_csv_files(zip_path)
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in ZIP: {zip_path}")

    ptp_rows: list[tuple] = []
    mue_rows: list[tuple] = []
    headers_seen: dict[str, list[str]] = {}

    for label, (header, rows) in csv_files.items():
        header_lower = [h.strip().lower() for h in header]
        headers_seen[label] = header_lower

        if _is_ptp_file(label, header_lower):
            edit_type = "outpatient_asc" if any(
                w in label for w in ("hosp", "outpt", "asc", "facility")
            ) else "physician"
            parsed = _parse_ptp(label, header, rows, quarter, edit_type)
            ptp_rows.extend(parsed)
            print(f"[c06_ncci_edits] {label}: {len(parsed)} PTP rows ({edit_type})")
        elif _is_mue_file(label, header_lower):
            parsed = _parse_mue(label, header, rows, quarter)
            mue_rows.extend(parsed)
            print(f"[c06_ncci_edits] {label}: {len(parsed)} MUE rows")
        else:
            print(f"[c06_ncci_edits] {label}: unrecognised file type — skipping")

    if len(ptp_rows) < 100_000:
        raise ValueError(
            f"[c06_ncci_edits] Only {len(ptp_rows)} PTP rows — expected >= 100,000. "
            f"Check ZIP contents and _is_ptp_file() detection."
        )
    if len(mue_rows) < 5_000:
        raise ValueError(
            f"[c06_ncci_edits] Only {len(mue_rows)} MUE rows — expected >= 5,000. "
            f"Check ZIP contents and _is_mue_file() detection."
        )

    # Bulk insert PTP (5,000 rows per transaction)
    batch_size = 5000
    for i in range(0, len(ptp_rows), batch_size):
        conn.executemany(
            """INSERT OR REPLACE INTO ncci_ptp_edits
               (edit_id, column_one_code, column_two_code, modifier_indicator,
                effective_date, deletion_date, edit_type, quarter, source_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ptp_rows[i : i + batch_size],
        )
        conn.commit()

    # Bulk insert MUE (1,000 rows per transaction)
    for i in range(0, len(mue_rows), 1000):
        conn.executemany(
            """INSERT OR REPLACE INTO ncci_mue
               (mue_id, hcpcs_code, mue_value, mue_adjudication_indicator,
                effective_date, deletion_date, quarter, source_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            mue_rows[i : i + 1000],
        )
        conn.commit()

    release_ptp = record_release(conn, SOURCE_PTP, zip_url, file_hash, len(ptp_rows),
                                  notes=f"quarter={quarter}")
    release_mue = record_release(conn, SOURCE_MUE, zip_url, file_hash, len(mue_rows),
                                  notes=f"quarter={quarter}")

    # Save header snapshot for schema drift detection on next run
    _save_headers(headers_seen)

    print(f"[c06_ncci_edits] done — {len(ptp_rows)} PTP rows (release #{release_ptp}), "
          f"{len(mue_rows)} MUE rows (release #{release_mue})")


if __name__ == "__main__":
    main()
