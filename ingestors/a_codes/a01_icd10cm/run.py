"""ICD-10-CM FY2025 ingestor.

Source: CMS annual ZIP containing the flat order file:
  icd10cm_order_YYYY.txt  (fixed-width, FY2025 = icd10cm_order_2025.txt)

Fixed-width format per CMS spec:
  cols  1-5 : order number
  col   6   : space
  cols  7-13: ICD-10-CM code (7 chars, left-justified, space-padded)
  col  14   : space
  col  15   : 0=header/category, 1=valid billable code
  col  16   : space
  cols 17-76: short description (60 chars)
  col  77   : space
  cols 78+  : long description
"""
import re
import zipfile
from pathlib import Path
import sqlite3

from ingestors.core.pipeline import run_ingestor, now_utc
from ingestors.core.db import get_conn

SOURCE_ID = "a01_icd10cm"
CODE_TYPE = "ICD10CM"
ORDER_FILE_PATTERN = re.compile(r"icd10cm_order_\d{4}\.txt", re.IGNORECASE)


def _find_order_file(zf: zipfile.ZipFile) -> str | None:
    for name in zf.namelist():
        if ORDER_FILE_PATTERN.search(name):
            return name
    return None


def _parse_order_line(line: str) -> dict | None:
    if len(line) < 16:
        return None
    code = line[6:13].strip()
    if not code:
        return None
    is_header = line[14].strip() == "0"
    short_desc = line[16:76].strip() if len(line) > 16 else ""
    long_desc = line[77:].strip() if len(line) > 77 else ""
    return {
        "code": code,
        "is_header": is_header,
        "short_description": short_desc,
        "description": long_desc or short_desc,
    }


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        order_name = _find_order_file(zf)
        if not order_name:
            raise FileNotFoundError("Could not find icd10cm_order_*.txt inside ZIP")

        with zf.open(order_name) as fh:
            lines = fh.read().decode("latin-1").splitlines()

    rows = []
    for line in lines:
        parsed = _parse_order_line(line)
        if parsed:
            rows.append(parsed)

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?, ?,?,?)""",
        [
            (CODE_TYPE, r["code"], r["description"], r["short_description"],
             1 if r["is_header"] else 0, SOURCE_ID, source_url, retrieved_at)
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
