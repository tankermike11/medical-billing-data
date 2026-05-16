"""MS-DRG FY2025 ingestor.

Source: CMS ICD-10 MS-DRG Definitions Manual V42 ZIP.
Parses appendix_A.txt which contains the full DRG list in tabular format:

  DRG MDC MS Description
  001     P  Heart Transplant or Implant of Heart Assist System with MCC
  020 01  P  Intracranial Vascular Procedures with Principal Diagnosis Hemorrhage with MCC

Columns: DRG (3-digit) | MDC (2-digit, optional) | Type P/M | Description
"""
import re
import zipfile
from pathlib import Path
import sqlite3
import json

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "a10_ms_drg"
CODE_TYPE = "MSDRG"

_APPENDIX_A = re.compile(r"appendix_a.*\.txt$", re.IGNORECASE)
_DATA_LINE   = re.compile(r"^(\d{3})\s+(\d{2})?\s*([PM])\s{2,}(.+)$")


def _find_appendix(zf: zipfile.ZipFile) -> str | None:
    return next((n for n in zf.namelist() if _APPENDIX_A.search(n)), None)


def _parse_appendix(text: str) -> list[dict]:
    records = []
    for line in text.splitlines():
        m = _DATA_LINE.match(line)
        if not m:
            continue
        drg_num, mdc, drg_type, description = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        records.append({
            "code": drg_num,
            "description": description,
            "extra": json.dumps({
                "mdc": mdc or "",
                "type": "surgical" if drg_type == "P" else "medical",
            }),
        })
    return records


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        appendix_name = _find_appendix(zf)
        if not appendix_name:
            raise FileNotFoundError(
                f"appendix_A.txt not found in ZIP. Contents: {zf.namelist()}"
            )
        with zf.open(appendix_name) as fh:
            text = fh.read().decode("latin-1")

    records = _parse_appendix(text)
    if not records:
        raise ValueError("No DRG records parsed from appendix_A.txt — format may have changed")

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, extra, source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?)""",
        [(CODE_TYPE, r["code"], r["description"], r["extra"],
          SOURCE_ID, source_url, retrieved_at)
         for r in records],
    )
    conn.commit()
    return len(records)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
