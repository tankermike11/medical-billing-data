"""SADP (Stand-Alone Dental Plan) Landscape 2025 ingestor.

Source: data.healthcare.gov — Individual Market Dental (Excel).
Same file structure as b01_qhp_landscape: row 0 is a metadata summary,
row 1 is column headers, row 2+ is data (one row per plan-county).

Plans are stored in the plans table with dental_only=1.
"""
import zipfile
from io import BytesIO
from pathlib import Path
import sqlite3

import openpyxl

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "b04_sadp_puf"
PLAN_YEAR = 2025


def _build_col_map(header_row: tuple) -> dict[str, int]:
    return {
        (str(v).strip().lower().replace(" ", "_").replace("(", "").replace(")", "").rstrip("_") if v else ""): i
        for i, v in enumerate(header_row)
    }


def _get(row: tuple, cm: dict, *keys: str) -> str:
    for k in keys:
        norm = k.lower().replace(" ", "_").replace("(", "").replace(")", "").rstrip("_")
        i = cm.get(norm)
        if i is not None and i < len(row) and row[i] is not None:
            return str(row[i]).strip()
    return ""


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        xlsx_name = next((n for n in zf.namelist() if n.lower().endswith(".xlsx")), None)
        if not xlsx_name:
            raise FileNotFoundError("No .xlsx file found in SADP ZIP")
        xlsx_bytes = zf.read(xlsx_name)

    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    next(rows_iter)           # skip row 0 — metadata summary
    header_row = next(rows_iter)   # row 1 — column headers
    cm = _build_col_map(header_row)

    seen_plans: set[str] = set()
    plans_to_insert = []
    materials_to_insert = []

    for row in rows_iter:
        plan_id = _get(row, cm, "Plan ID (Standard Component)", "Plan_ID", "StandardComponentID")
        if not plan_id:
            continue

        state       = _get(row, cm, "State Code", "State")
        county      = _get(row, cm, "County Name", "CountyName")
        fips        = _get(row, cm, "FIPS County Code", "CountyFIPSCode")
        plan_name   = _get(row, cm, "Plan Marketing Name", "PlanMarketingName")
        metal       = _get(row, cm, "Metal Level", "MetalLevel")
        plan_type   = _get(row, cm, "Plan Type", "PlanType")
        issuer_name = _get(row, cm, "Issuer Name", "IssuerName")
        issuer_id   = _get(row, cm, "HIOS Issuer ID", "Issuer_ID", "IssuerID")

        if plan_id not in seen_plans:
            seen_plans.add(plan_id)
            plans_to_insert.append((
                plan_id, plan_name, issuer_id, issuer_name,
                state, fips, county, plan_type, metal, PLAN_YEAR,
                "Individual", 1, 0,
                SOURCE_ID, source_url, retrieved_at,
            ))

            for mat_type, col_name in [
                ("sbc",      "Summary of Benefits URL"),
                ("brochure", "Plan Brochure URL"),
                ("network",  "Network URL"),
            ]:
                url = _get(row, cm, col_name)
                if url:
                    materials_to_insert.append((plan_id, PLAN_YEAR, mat_type, url, SOURCE_ID, retrieved_at))

    wb.close()

    conn.executemany(
        """INSERT OR REPLACE INTO plans
           (plan_id, plan_name, issuer_id, issuer_name,
            state, county_fips, county_name, plan_type, metal_level, plan_year,
            market_coverage, dental_only, national_network,
            source_id, source_url, retrieved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        plans_to_insert,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO plan_materials
           (plan_id, plan_year, material_type, url, source_id, retrieved_at)
           VALUES (?,?,?,?,?,?)""",
        materials_to_insert,
    )
    conn.commit()
    return len(plans_to_insert)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
