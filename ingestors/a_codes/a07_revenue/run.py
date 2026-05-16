"""UB-04 Revenue Code ingestor.

NUBC (authoritative source) is behind a membership paywall.
CMS does not publish a standalone machine-readable revenue code file.

This ingestor reads from a manually prepared CSV placed at:
  data/raw/a07_revenue/revenue_codes.csv

Expected CSV columns (case-insensitive):
  code_or_range          — revenue code (zero-padded to 4 digits)
  normalized_category    — short category label  → stored as short_description
  patient_facing_explanation — plain-language description → stored as description
  common_claim_context   — claim context note    ┐
  mvp_use_case           — advocacy use-case note ├─ stored as JSON in extra
  priority               — High / Medium / Low   ┘

Legacy single-column CSVs with just code,description are also accepted.
"""
import csv
import json
import sqlite3
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import now_utc, record_release, load_registry

SOURCE_ID = "a07_revenue"
CODE_TYPE = "RevenueCode"
MANUAL_CSV = Path(__file__).resolve().parents[3] / "data" / "raw" / "a07_revenue" / "revenue_codes.csv"


def main():
    if not MANUAL_CSV.exists():
        print(f"[{SOURCE_ID}] MANUAL STEP REQUIRED: place revenue_codes.csv at {MANUAL_CSV}")
        print("  See module docstring for format and sources.")
        return

    conn = get_conn()
    retrieved_at = now_utc()

    with open(MANUAL_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            norm = {k.strip().lower(): v.strip() for k, v in row.items()}
            code = norm.get("code_or_range", norm.get("code", "")).strip().zfill(4)
            if not code or code == "0000":
                continue
            description = norm.get(
                "patient_facing_explanation",
                norm.get("description", norm.get("desc", "")),
            )
            short_desc = norm.get("normalized_category", "")
            extra = json.dumps({
                k: norm[k]
                for k in ("common_claim_context", "mvp_use_case", "priority")
                if norm.get(k)
            }) if any(norm.get(k) for k in ("common_claim_context", "mvp_use_case", "priority")) else None
            rows.append((CODE_TYPE, code, description, short_desc, 0, extra,
                         SOURCE_ID, str(MANUAL_CSV), retrieved_at))

    conn.executemany(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header, extra,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    record_release(conn, SOURCE_ID, str(MANUAL_CSV), "manual_import", len(rows))
    print(f"[{SOURCE_ID}] done — {len(rows)} revenue codes loaded from manual CSV")


if __name__ == "__main__":
    main()
