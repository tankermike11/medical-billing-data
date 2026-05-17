"""QHP Plan Attributes PUF 2025 ingestor.

Source: CMS/CCIIO — Plan Attributes Public Use File.
Contains plan-level deductible, MOOP, HSA eligibility, and other attributes.

Key columns (representative; CMS column names vary slightly by plan year):
  PlanId             — HIOS ID
  PlanType
  MetalLevel
  IsHSAEligible
  TEHBDedInnTier1Individual   — in-network tier-1 individual deductible
  TEHBDedInnTier1Family
  TEHBDedOutOfNetIndividual
  TEHBDedOutOfNetFamily
  MEHBInnTier1IndividualMOOP  — in-network individual MOOP
  MEHBInnTier1FamilyMOOP

All monetary values stored as strings (may be "$1,500" or "1500" — normalized downstream).
"""
import csv
import zipfile
from io import TextIOWrapper
from pathlib import Path
import sqlite3
import json

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "b02_plan_attributes_puf"
PLAN_YEAR = 2025

ATTRIBUTE_COLS = [
    "IsHSAEligible",
    "TEHBDedInnTier1Individual",
    "TEHBDedInnTier1Family",
    "TEHBDedOutOfNetIndividual",
    "TEHBDedOutOfNetFamily",
    "MEHBInnTier1IndividualMOOP",
    "MEHBInnTier1FamilyMOOP",
    "FormularyURL",
    "PlanBrochure",
    "SummaryBenefitsCoverageURL",
]


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        csv_name = next(
            (n for n in zf.namelist() if n.lower().endswith(".csv")),
            None,
        )
        if not csv_name:
            raise FileNotFoundError("No CSV in Plan Attributes PUF ZIP")
        with zf.open(csv_name) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig"))
            all_rows = list(reader)

    attribute_rows = []
    for row in all_rows:
        plan_id = row.get("PlanId", row.get("StandardComponentId", "")).strip()
        if not plan_id:
            continue
        for col in ATTRIBUTE_COLS:
            val = row.get(col, "").strip()
            if val:
                attribute_rows.append((plan_id, PLAN_YEAR, col, val, SOURCE_ID, retrieved_at))
        # plan_attributes links by plan_id; no stub inserts into plans needed

    conn.executemany(
        """INSERT OR REPLACE INTO plan_attributes
           (plan_id, plan_year, attribute_name, attribute_value, source_id, retrieved_at)
           VALUES (?,?,?,?,?,?)""",
        attribute_rows,
    )
    conn.commit()
    return len(attribute_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
