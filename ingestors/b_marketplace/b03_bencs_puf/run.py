"""QHP Benefits & Cost Sharing PUF 2025 ingestor.

Source: CMS/CCIIO — Benefits and Cost Sharing Public Use File.
Contains one row per benefit per plan, with copay and coinsurance details.

Key columns:
  PlanId / StandardComponentId
  BenefitName
  CopayInnTier1
  CoinsInnTier1
  CopayOutofNet
  CoinsOutofNet
  IsEHBorExcluded  — whether benefit is Essential Health Benefit or excluded

Large file: may be 200-500 MB unzipped. Processed row by row.
"""
import csv
import zipfile
from io import TextIOWrapper
from pathlib import Path
import sqlite3

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "b03_bencs_puf"
PLAN_YEAR = 2025


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise FileNotFoundError("No CSV in BenCS PUF ZIP")
        csv_name = csv_names[0]

        total = 0
        with zf.open(csv_name) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding="utf-8-sig"))
            chunk = []
            for row in reader:
                plan_id = row.get("PlanId", row.get("StandardComponentId", "")).strip()
                benefit = row.get("BenefitName", "").strip()
                if not plan_id or not benefit:
                    continue
                is_excluded = row.get("IsEHBorExcluded", "").strip().upper() in ("EXCLUDED", "NOT EHB", "0")
                chunk.append((
                    plan_id, PLAN_YEAR, benefit,
                    row.get("IsCovered", "").strip(),
                    row.get("CopayInnTier1", "").strip(),
                    row.get("CoinsInnTier1", "").strip(),
                    row.get("CopayOutofNet", "").strip(),
                    row.get("CoinsOutofNet", "").strip(),
                    1 if is_excluded else 0,
                    SOURCE_ID, retrieved_at,
                ))
                if len(chunk) >= 5000:
                    conn.executemany(
                        """INSERT OR REPLACE INTO plan_benefits
                           (plan_id, plan_year, benefit_name, covered,
                            copay_inn_tier1, coinsurance_inn_tier1,
                            copay_outn, coinsurance_outn,
                            is_excluded, source_id, retrieved_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        chunk,
                    )
                    conn.commit()
                    total += len(chunk)
                    chunk = []
            if chunk:
                conn.executemany(
                    """INSERT OR REPLACE INTO plan_benefits
                       (plan_id, plan_year, benefit_name, covered,
                        copay_inn_tier1, coinsurance_inn_tier1,
                        copay_outn, coinsurance_outn,
                        is_excluded, source_id, retrieved_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    chunk,
                )
                conn.commit()
                total += len(chunk)

    return total


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
