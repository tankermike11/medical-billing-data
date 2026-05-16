"""RARC (Remittance Advice Remark Codes) ingestor.

Same approach as CARC: scrape WPC HTML table.
RARC codes follow the pattern: M[0-9]+ or N[0-9]+ (e.g., M1, N1, N650).
"""
import re
import hashlib
import sqlite3
from pathlib import Path

from ingestors.a_codes.a05_carc.run import _TableParser
from ingestors.core.db import get_conn
from ingestors.core.http import fetch_text
from ingestors.core.pipeline import run_ingestor, now_utc, record_release, load_registry

SOURCE_ID = "a06_rarc"
CODE_TYPE = "RARC"


def _parse_rarc_html(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)

    records = []
    for row in parser.rows:
        if not row or not row[0].strip():
            continue
        code = row[0].strip()
        if not re.match(r"^[A-Z]+\d+$", code, re.IGNORECASE):
            continue
        description = row[1].strip() if len(row) > 1 else ""
        start_date = row[2].strip() if len(row) > 2 else ""
        stop_date = row[3].strip() if len(row) > 3 else None
        records.append({
            "code": code,
            "description": description,
            "effective_date": start_date,
            "expiration_date": stop_date,
        })
    return records


def main():
    registry = load_registry()
    source = registry[SOURCE_ID]
    url = source["canonical_url"]
    conn = get_conn()

    print(f"[{SOURCE_ID}] fetching HTML from {url}")
    html = fetch_text(url)
    file_hash = hashlib.sha256(html.encode()).hexdigest()

    records = _parse_rarc_html(html)
    if not records:
        raise ValueError("No RARC records parsed — page structure may have changed")

    retrieved_at = now_utc()
    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, effective_date, expiration_date,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        [
            (CODE_TYPE, r["code"], r["description"], r["effective_date"],
             r["expiration_date"], SOURCE_ID, url, retrieved_at)
            for r in records
        ],
    )
    conn.commit()
    record_release(conn, SOURCE_ID, url, file_hash, len(records))
    print(f"[{SOURCE_ID}] done — {len(records)} RARC codes")


if __name__ == "__main__":
    main()
