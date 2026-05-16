"""Place of Service (POS) code ingestor.

Source: CMS PDF — "Place of Service Code Set" (updated May 2, 2024).
CMS no longer publishes this as Excel; the PDF is the current authoritative file.
URL: https://www.cms.gov/medicare/medicare-fee-for-service-payment/physicianfeesched/downloads/website-pos-database.pdf

The PDF contains a table with columns:
  Place of Service Code | Place of Service Name | Place of Service Description | Facility/Non-Facility APC
"""
import re
import sqlite3
from pathlib import Path

import pdfplumber

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "a08_pos"
CODE_TYPE = "POS"

_CODE_RE = re.compile(r"^\d{2}$")


def _parse_pos_pdf(pdf_path: Path) -> list[tuple]:
    records = []
    seen = set()

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 2:
                    continue
                code = str(row[0]).strip() if row[0] else ""
                if not _CODE_RE.match(code) or code in seen:
                    continue
                seen.add(code)
                name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                desc = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                records.append((CODE_TYPE, code, desc or name, name, 0))

    return records


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    records = _parse_pos_pdf(local_path)
    if not records:
        raise ValueError("No POS codes parsed from PDF — table structure may have changed")

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(r[0], r[1], r[2], r[3], r[4], SOURCE_ID, source_url, retrieved_at)
         for r in records],
    )
    conn.commit()
    return len(records)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
