"""HCPCS Modifier ingestor.

Modifiers are in the same Excel sheet as procedure codes, distinguished by RECID:
  RECID=3 — alphanumeric procedure codes  (handled by a03_hcpcs)
  RECID=7 — modifier codes (2-character)  (handled here)

As of April 2020 CMS publishes HCPCS as quarterly-only files; there is no separate
annual file. This ingestor re-uses the ZIP already downloaded by a03_hcpcs.
"""
import re
import zipfile
from io import BytesIO
from pathlib import Path
import sqlite3

import openpyxl

from ingestors.core.db import get_conn
from ingestors.core.pipeline import now_utc, record_release, load_registry
from ingestors.core.http import _sha256

SOURCE_ID = "a09_modifiers"
CODE_TYPE = "Modifier"
MODIFIER_RECID = "7"

HCPCS_RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "a03_hcpcs"
XLSX_PATTERN = re.compile(r"hcpc\d{4}.*\.xlsx?$", re.IGNORECASE)


def _find_zip() -> Path | None:
    if not HCPCS_RAW_DIR.exists():
        return None
    for p in HCPCS_RAW_DIR.iterdir():
        if p.suffix.lower() == ".zip":
            return p
    return None


def _find_xlsx(zf: zipfile.ZipFile) -> str | None:
    for name in zf.namelist():
        if XLSX_PATTERN.search(name):
            return name
    return None


def main():
    zip_path = _find_zip()
    if not zip_path:
        print(f"[{SOURCE_ID}] HCPCS ZIP not found at {HCPCS_RAW_DIR} — run a03_hcpcs first.")
        return

    with zipfile.ZipFile(zip_path) as zf:
        xlsx_name = _find_xlsx(zf)
        if not xlsx_name:
            print(f"[{SOURCE_ID}] no HCPCS Excel file found in ZIP")
            return
        xlsx_bytes = zf.read(xlsx_name)

    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)

    # Build column index from header row
    headers = [str(h).strip().upper() if h is not None else "" for h in next(rows_iter, [])]
    def idx(*keys):
        for k in keys:
            if k in headers:
                return headers.index(k)
        return None

    hcpc_i  = idx("HCPC", "HCPCS CD", "HCPCS_CD")
    recid_i = idx("RECID")
    desc_i  = idx("LONG DESCRIPTION", "LONG_DESCRIPTION", "DESCRIPTION")

    if hcpc_i is None or recid_i is None:
        print(f"[{SOURCE_ID}] required columns (HCPC, RECID) not found — headers: {headers[:10]}")
        wb.close()
        return

    records = []
    for row in rows_iter:
        if recid_i >= len(row) or str(row[recid_i]) != MODIFIER_RECID:
            continue
        code = str(row[hcpc_i]).strip() if row[hcpc_i] is not None else ""
        if not code:
            continue
        desc = str(row[desc_i]).strip() if desc_i is not None and desc_i < len(row) and row[desc_i] else ""
        records.append((CODE_TYPE, code, desc, "", 0))

    wb.close()

    if not records:
        print(f"[{SOURCE_ID}] no RECID={MODIFIER_RECID} rows found — check file format")
        return

    conn = get_conn()
    retrieved_at = now_utc()
    file_hash = _sha256(zip_path)
    source_url = load_registry()[SOURCE_ID]["canonical_url"]

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(r[0], r[1], r[2], r[3], r[4], SOURCE_ID, source_url, retrieved_at)
         for r in records],
    )
    conn.commit()
    record_release(conn, SOURCE_ID, source_url, file_hash, len(records))
    print(f"[{SOURCE_ID}] done — {len(records)} modifiers")


if __name__ == "__main__":
    main()
