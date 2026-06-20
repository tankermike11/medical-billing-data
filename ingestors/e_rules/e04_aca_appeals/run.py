"""Federal ACA / commercial appeals framework ingestor (Family E, E.4).

Source: hand-authored fixture from 45 CFR §147.136, 29 CFR §2560.503-1,
        and DOL/EBSA guidance.
Fixture: data/raw/e04_aca_appeals/records.json (4 rows)

No automated download. Re-run after editing records.json.

ERISA routing note: when plan_funding_type = self_funded_erisa and the member asks
about state external review, the application must surface erisa_preemption_note on
erisa_L2 and escalate to human review (load-bearing edge case per addendum §E.4).
"""
import json
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import record_release, record_error

SOURCE_ID = "e04_aca_appeals"
FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw" / SOURCE_ID / "records.json"
SOURCE_URL = "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-B/part-147"
EXPECTED_ROWS = 4

_INSERT_SQL = """INSERT OR REPLACE INTO commercial_appeal_levels
   (level_id, level_number, level_name, applicable_plan_types,
    filing_deadline_days, filing_deadline_basis,
    decision_deadline_days, decision_deadline_basis,
    iro_applicable, erisa_preemption_note, citation,
    deadline_reviewed_by, source_id)
   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def main():
    if not FIXTURE_PATH.exists():
        print(f"[{SOURCE_ID}] FIXTURE REQUIRED: place records.json at {FIXTURE_PATH}")
        print("  See data_completion_addendum_v2.md §E.4 for the required 4-row structure.")
        return

    conn = get_conn()
    release_id = None

    try:
        records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("records.json must be a JSON array")

        if len(records) != EXPECTED_ROWS:
            raise ValueError(f"Expected {EXPECTED_ROWS} rows, got {len(records)}")

        for level_id in ("aca_L1", "erisa_L1"):
            row = next((r for r in records if r.get("level_id") == level_id), None)
            if row is None:
                raise ValueError(f"Missing required row: {level_id}")
            if row.get("filing_deadline_days") is None:
                raise ValueError(f"filing_deadline_days must not be NULL on {level_id}")

        erisa_l2 = next((r for r in records if r.get("level_id") == "erisa_L2"), None)
        if not erisa_l2:
            raise ValueError("Missing required row: erisa_L2")
        if not erisa_l2.get("erisa_preemption_note"):
            raise ValueError("erisa_preemption_note must be non-null on erisa_L2")

        null_citations = [r.get("level_id", "?") for r in records if not r.get("citation")]
        if null_citations:
            raise ValueError(f"citation NULL on rows: {null_citations}")

        null_reviewed = [r.get("level_id") for r in records if not r.get("deadline_reviewed_by")]
        if null_reviewed:
            print(f"[{SOURCE_ID}] WARNING: deadline_reviewed_by NULL on: {null_reviewed}")

        db_rows = [
            (
                r["level_id"],
                r["level_number"],
                r["level_name"],
                json.dumps(r["applicable_plan_types"])
                    if isinstance(r["applicable_plan_types"], list)
                    else r["applicable_plan_types"],
                r.get("filing_deadline_days"),
                r["filing_deadline_basis"],
                r.get("decision_deadline_days"),
                r.get("decision_deadline_basis"),
                r["iro_applicable"],
                r.get("erisa_preemption_note"),
                r["citation"],
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
