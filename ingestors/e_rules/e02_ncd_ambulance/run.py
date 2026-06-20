"""NCD 10.1 — Ambulance Services ingestor (Family E, E.2).

Source: CMS Medicare Coverage Database — NCD 10.1 (Ambulance Services)
  https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34

Records are hand-authored from the CMS NCD 10.1 source page. The CMS MCD site
requires JavaScript and cannot be fetched programmatically.

Fixture: data/raw/e02_ncd_ambulance/records.json

All rows are written with reviewed=0. A human reviewer must verify each row's
criteria_text against the CMS source and then set reviewed=1. The application
serves only reviewed=1 rows.

Human review gate:
  1. Open https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34
  2. Verify each criteria_text field in the fixture against the live CMS text
  3. Run this ingestor to load the rows
  4. For each verified row:
       UPDATE ncd_ambulance SET reviewed=1 WHERE ncd_id='10.1' AND section_id=<id>;
"""
import json
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import record_release, record_error

SOURCE_ID = "e02_ncd_ambulance"
FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / SOURCE_ID / "records.json"
SOURCE_URL = "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34"
EXPECTED_MIN_ROWS = 7

_VALID_CRITERION_TYPES = {
    "medical_necessity", "transport_condition", "facility_standard", "exclusion", "definition"
}
_REQUIRED_CRITERION_TYPES = {
    "medical_necessity", "transport_condition", "facility_standard", "exclusion"
}

_INSERT_SQL = """INSERT OR REPLACE INTO ncd_ambulance
   (ncd_id, section_id, section_title, coverage_indicator, criteria_text,
    criterion_type, effective_date, citation, reviewed, source_id)
   VALUES (?,?,?,?,?,?,?,?,?,?)"""


def main():
    if not FIXTURE_PATH.exists():
        print(f"[{SOURCE_ID}] FIXTURE REQUIRED: place records.json at {FIXTURE_PATH}")
        print(f"  Author criteria_text from: {SOURCE_URL}")
        print("  See data_completion_addendum_v2.md §E.2 key criteria table for required rows.")
        return

    conn = get_conn()
    release_id = None

    try:
        records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("records.json must be a JSON array")

        if len(records) < EXPECTED_MIN_ROWS:
            raise ValueError(f"Expected >= {EXPECTED_MIN_ROWS} rows, got {len(records)}")

        for r in records:
            text = (r.get("criteria_text") or "").strip()
            if not text or len(text) < 20:
                raise ValueError(
                    f"criteria_text too short on section_id={r.get('section_id')} — "
                    f"fill in the actual NCD text from {SOURCE_URL}"
                )
            if text.upper().startswith("STUB"):
                raise ValueError(
                    f"criteria_text is still a stub on section_id={r.get('section_id')} — "
                    f"replace with verbatim text from {SOURCE_URL} before loading"
                )
            if not r.get("citation"):
                raise ValueError(f"citation missing on section_id={r.get('section_id')}")
            ct = r.get("criterion_type")
            if ct and ct not in _VALID_CRITERION_TYPES:
                raise ValueError(f"Unknown criterion_type '{ct}' on section_id={r.get('section_id')}")

        found_types = {r.get("criterion_type") for r in records if r.get("criterion_type")}
        missing_types = _REQUIRED_CRITERION_TYPES - found_types
        if missing_types:
            raise ValueError(
                f"Missing required criterion_type(s): {missing_types}. "
                f"Add rows covering these types before loading."
            )

        db_rows = [
            (
                r["ncd_id"],
                r["section_id"],
                r["section_title"],
                r.get("coverage_indicator", "informational"),
                r["criteria_text"].strip(),
                r.get("criterion_type"),
                r["effective_date"],
                r["citation"],
                0,  # reviewed=0 always — never set programmatically
                SOURCE_ID,
            )
            for r in records
        ]

        conn.executemany(_INSERT_SQL, db_rows)
        conn.commit()

        reviewed_count = conn.execute(
            "SELECT COUNT(*) FROM ncd_ambulance WHERE ncd_id='10.1' AND reviewed=1"
        ).fetchone()[0]

        release_id = record_release(conn, SOURCE_ID, SOURCE_URL, "manual", len(db_rows))
        print(f"[{SOURCE_ID}] done — {len(db_rows)} rows loaded (reviewed=0) (release #{release_id})")
        print(f"[{SOURCE_ID}] {reviewed_count} row(s) currently have reviewed=1")
        if reviewed_count < 6:
            print(f"[{SOURCE_ID}] NEXT: verify criteria_text against {SOURCE_URL}")
            print(f"[{SOURCE_ID}]   then: UPDATE ncd_ambulance SET reviewed=1 WHERE ncd_id='10.1' AND section_id=<id>")

    except Exception as e:
        record_error(conn, SOURCE_ID, release_id, "parse_error", str(e))
        raise


if __name__ == "__main__":
    main()
