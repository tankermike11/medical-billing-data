"""HCPCS Level II 2025 ingestor.

Source: CMS annual ZIP containing an Excel file (HCPC2025_ANWEB_v*.xlsx or similar).
Key columns (case-insensitive match):
  HCPCS CD            — the HCPCS code (A0000–V9999)
  LONG DESCRIPTION    — full description
  SHORT DESCRIPTION   — abbreviated description (28 chars)
  PRICING INDICATOR CODE
  COVERAGE CODE
  APPLICABLE MEDICARE PART

Modifiers are in a separate sheet ("Modifiers" or similar) and are also extracted
here but primarily handled by a09_modifiers which re-uses this same ZIP.
"""
import re
import zipfile
from io import BytesIO
from pathlib import Path
import sqlite3

import openpyxl

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "a03_hcpcs"
CODE_TYPE = "HCPCS"
XLSX_PATTERN = re.compile(r"hcpc\d{4}_an.*\.xlsx?$", re.IGNORECASE)


def _find_xlsx(zf: zipfile.ZipFile) -> str | None:
    for name in zf.namelist():
        if XLSX_PATTERN.search(name):
            return name
    for name in zf.namelist():
        if name.lower().endswith((".xlsx", ".xls")) and "hcpc" in name.lower():
            return name
    return None


def _col_map(headers: list[str]) -> dict[str, int]:
    m = {}
    for i, h in enumerate(headers):
        key = h.strip().upper() if h else ""
        m[key] = i
    return m


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        xlsx_name = _find_xlsx(zf)
        if not xlsx_name:
            raise FileNotFoundError("Could not find HCPCS Excel file inside ZIP")
        xlsx_bytes = zf.read(xlsx_name)

    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter)
    cm = _col_map([str(h) if h is not None else "" for h in header_row])

    def col(row, *keys):
        for k in keys:
            if k in cm and cm[k] < len(row) and row[cm[k]] is not None:
                return str(row[cm[k]]).strip()
        return ""

    import json
    rows = []
    for row in rows_iter:
        code = col(row, "HCPC", "HCPCS CD", "HCPCS_CD", "CODE")
        if not code:
            continue
        long_desc = col(row, "LONG DESCRIPTION", "LONG_DESCRIPTION", "DESCRIPTION")
        short_desc = col(row, "SHORT DESCRIPTION", "SHORT_DESCRIPTION")
        extra = json.dumps({
            "pricing_indicator": col(row, "MULT_PI", "PRICING INDICATOR CODE", "PRICING_INDICATOR_CODE"),
            "coverage_code": col(row, "COV", "COVERAGE CODE", "COVERAGE_CODE"),
            "medicare_part": col(row, "TOS1", "APPLICABLE MEDICARE PART", "APPLICABLE_MEDICARE_PART"),
        })
        rows.append((CODE_TYPE, code, long_desc, short_desc, 0, extra, SOURCE_ID, source_url, retrieved_at))

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header, extra,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    wb.close()
    return len(rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
