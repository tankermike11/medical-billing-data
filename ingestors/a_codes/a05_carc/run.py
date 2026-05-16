"""CARC (Claim Adjustment Reason Codes) ingestor.

Source: WPC (Washington Publishing Company) HTML table.
WPC publishes no stable direct-download URL; we scrape the HTML table.
URL: https://wpc-edi.com/reference/codelists/healthcare/claim-adjustment-reason-codes/

The page renders a table with columns: Code | Description | Start Date | Stop Date
"""
import hashlib
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.http import fetch_text
from ingestors.core.pipeline import run_ingestor, now_utc, record_release, load_registry

SOURCE_ID = "a05_carc"
CODE_TYPE = "CARC"


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_table = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_text = ""
        self.rows: list[list[str]] = []
        self._header_skipped = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
        if self._in_table and tag in ("td", "th"):
            self._in_cell = True
            self._current_text = ""
        if self._in_table and tag == "tr":
            self._current_row = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        if self._in_table and tag in ("td", "th"):
            self._current_row.append(self._current_text.strip())
            self._in_cell = False
        if self._in_table and tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)

    def handle_data(self, data):
        if self._in_cell:
            self._current_text += data


def _parse_carc_html(html: str, code_type: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)

    records = []
    for row in parser.rows:
        if not row or not row[0].strip():
            continue
        code = row[0].strip()
        if not re.match(r"^\d+[A-Z]?$", code):
            continue  # skip header rows
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

    records = _parse_carc_html(html, CODE_TYPE)
    if not records:
        raise ValueError("No CARC records parsed — page structure may have changed")

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
    print(f"[{SOURCE_ID}] done — {len(records)} CARC codes")


if __name__ == "__main__":
    main()
