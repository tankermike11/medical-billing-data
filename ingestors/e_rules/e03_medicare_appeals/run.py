"""Medicare 5-level appeals framework ingestor (Family E, E.3).

Source: hand-authored fixture from 42 CFR §405.900–1140, 42 CFR §423.1966,
        and CMS Pub. 100-04 Ch. 29.
Fixture: data/raw/e03_medicare_appeals/records.json (6 rows)

No automated download. Re-run after editing records.json (e.g. after counsel
populates deadline_reviewed_by).

Deadline constraint: filing_deadline_days is served by the application only when
deadline_reviewed_by IS NOT NULL. When NULL, the UI shows "verify on denial notice."
"""
import json
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import record_release, record_error

SOURCE_ID = "e03_medicare_appeals"
FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / SOURCE_ID / "records.json"
SOURCE_URL = "https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-405"
EXPECTED_ROWS = 6

_INSERT_SQL = """INSERT OR REPLACE INTO medicare_appeal_levels
   (level_id, level_number, level_name, decision_maker, filing_deadline_days,
    filing_deadline_basis, min_amount_in_controversy, submission_address_notes,
    citation, plan_type, deadline_reviewed_by, source_id)
   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""


def main():
    if not FIXTURE_PATH.exists():
        print(f"[{SOURCE_ID}] FIXTURE REQUIRED: place records.json at {FIXTURE_PATH}")
        print("  See data_completion_addendum_v2.md §E.3 for the required 6-row structure.")
        return

    conn = get_conn()
    release_id = None

    try:
        records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("records.json must be a JSON array")

        if len(records) != EXPECTED_ROWS:
            raise ValueError(f"Expected {EXPECTED_ROWS} rows, got {len(records)}")

        non_ma = [r for r in records if r.get("level_id") != "medicare_MA"]
        null_deadlines = [r["level_id"] for r in non_ma if r.get("filing_deadline_days") is None]
        if null_deadlines:
            raise ValueError(f"filing_deadline_days NULL on non-MA rows: {null_deadlines}")

        null_citations = [r.get("level_id", "?") for r in records if not r.get("citation")]
        if null_citations:
            raise ValueError(f"citation NULL on rows: {null_citations}")

        null_reviewed = [r.get("level_id") for r in records if not r.get("deadline_reviewed_by")]
        if null_reviewed:
            print(f"[{SOURCE_ID}] WARNING: deadline_reviewed_by NULL on: {null_reviewed}")
            print(f"[{SOURCE_ID}]   UI will show 'verify on denial notice' for these rows")

        db_rows = [
            (
                r["level_id"],
                r["level_number"],
                r["level_name"],
                r["decision_maker"],
                r.get("filing_deadline_days"),
                r["filing_deadline_basis"],
                r.get("min_amount_in_controversy"),
                r.get("submission_address_notes"),
                r["citation"],
                r["plan_type"],
                r.get("deadline_reviewed_by"),
                SOURCE_ID,
            )
            for r in records
        ]

        conn.executemany(_INSERT_SQL, db_rows)
        conn.commit()

        release_id = record_release(conn, SOURCE_ID, SOURCE_URL, "manual", len(db_rows))
        print(f"[{SOURCE_ID}] done — {len(db_rows)} appeal levels loaded (release #{release_id})")

    except Exception as e:
        record_error(conn, SOURCE_ID, release_id, "parse_error", str(e))
        raise


if __name__ == "__main__":
    main()
