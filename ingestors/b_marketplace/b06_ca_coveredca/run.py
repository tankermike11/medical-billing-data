"""Covered California QHP Individual Product Prices ingestor — b06.

Source: hbex.coveredca.com — 2025 Individual Product Prices XLSX.
Loads unique CA QHP plan metadata into the plans table.

File layout: rows 1–6 are a title/metadata block; row 7 is the header row.
One source row per Plan ID × Rating Area × Age — deduplicated to one plans
row per unique Plan ID. Rating areas and a sample rate (age 21) are stored
in plan_attributes. No SBC URLs are available in this source.
"""
import json
import sqlite3
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import openpyxl

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "b06_ca_coveredca"
PLAN_YEAR = 2025
HEADER_ROW = 7   # 1-indexed row number where column names live
SAMPLE_AGE = "21"


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    wb = openpyxl.load_workbook(BytesIO(local_path.read_bytes()), read_only=True, data_only=True)
    ws = wb.active

    headers: list[str] = []
    plan_rows: dict[str, dict] = {}        # plan_id → metadata dict
    plan_rating_areas: dict[str, set] = defaultdict(set)
    plan_sample_rate: dict[str, float] = {}

    for row_num, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row_num < HEADER_ROW:
            continue
        if row_num == HEADER_ROW:
            headers = [str(v).strip() if v is not None else "" for v in row]
            continue

        if not any(row):
            continue

        def col(name: str) -> str:
            try:
                return str(row[headers.index(name)]).strip() if row[headers.index(name)] is not None else ""
            except (ValueError, IndexError):
                return ""

        plan_id = col("Plan ID*")
        if not plan_id or plan_id == "None":
            continue

        rating_area = col("Rating Area ID*")
        age = col("Age*")
        rate = row[headers.index("Individual Rate*")] if "Individual Rate*" in headers else None

        plan_rating_areas[plan_id].add(rating_area)

        if age == SAMPLE_AGE and rate is not None and plan_id not in plan_sample_rate:
            try:
                plan_sample_rate[plan_id] = float(rate)
            except (TypeError, ValueError):
                pass

        if plan_id not in plan_rows:
            plan_rows[plan_id] = {
                "plan_id":    plan_id,
                "plan_name":  col("Full Plan Name"),
                "issuer_id":  col("HOIS ID"),
                "issuer_name": col("Applicant"),
                "metal_level": col("Metal Level"),
                "plan_type":  col("Network"),
                "benefit_design": col("Benefit Design"),
            }

    wb.close()

    plans_to_insert = []
    attrs_to_insert = []

    for plan_id, meta in plan_rows.items():
        extra = json.dumps({
            "benefit_design": meta["benefit_design"],
            "rating_areas": sorted(plan_rating_areas[plan_id]),
        })
        plans_to_insert.append((
            plan_id,
            meta["plan_name"],
            meta["issuer_id"],
            meta["issuer_name"],
            "CA", None, None,
            meta["plan_type"],
            meta["metal_level"],
            PLAN_YEAR,
            "Individual", 0, 0,
            extra,
            SOURCE_ID, source_url, retrieved_at,
        ))

        if plan_id in plan_sample_rate:
            attrs_to_insert.append((
                plan_id, PLAN_YEAR,
                "individual_rate_age21",
                str(plan_sample_rate[plan_id]),
                SOURCE_ID, retrieved_at,
            ))

        attrs_to_insert.append((
            plan_id, PLAN_YEAR,
            "rating_areas",
            ",".join(sorted(plan_rating_areas[plan_id])),
            SOURCE_ID, retrieved_at,
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO plans
           (plan_id, plan_name, issuer_id, issuer_name,
            state, county_fips, county_name, plan_type, metal_level, plan_year,
            market_coverage, dental_only, national_network,
            extra, source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        plans_to_insert,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO plan_attributes
           (plan_id, plan_year, attribute_name, attribute_value, source_id, retrieved_at)
           VALUES (?,?,?,?,?,?)""",
        attrs_to_insert,
    )
    conn.commit()
    return len(plans_to_insert)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
