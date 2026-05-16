"""NDC Directory ingestor.

Source: FDA weekly bulk download.
URL: https://download.fda.gov/ndc/ndc_database_file.zip

ZIP contains pipe-delimited flat files:
  product.txt — one row per drug product
  package.txt — one row per NDC package code

product.txt key columns:
  PRODUCTNDC, PROPRIETARYNAME, NONPROPRIETARYNAME, DOSAGEFORMNAME,
  ROUTENAME, SUBSTANCENAME, DEASCHEDULE, MARKETINGCATEGORYNAME, LABELERNAME

We store the canonical NDC (product-level) from PRODUCTNDC and use
NONPROPRIETARYNAME as the description.
"""
import csv
import zipfile
from io import TextIOWrapper
from pathlib import Path
import sqlite3
import json

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "a11_ndc"
CODE_TYPE = "NDC"


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    retrieved_at = now_utc()
    source_url = source["canonical_url"]

    with zipfile.ZipFile(local_path) as zf:
        product_name = next((n for n in zf.namelist() if "product" in n.lower() and n.endswith(".txt")), None)
        if not product_name:
            raise FileNotFoundError("product.txt not found in NDC ZIP")

        with zf.open(product_name) as raw:
            reader = csv.DictReader(TextIOWrapper(raw, encoding="latin-1"), delimiter="\t")
            rows = []
            for row in reader:
                ndc = row.get("PRODUCTNDC", "").strip()
                if not ndc:
                    continue
                proprietary = row.get("PROPRIETARYNAME", "").strip()
                nonproprietary = row.get("NONPROPRIETARYNAME", "").strip()
                description = nonproprietary or proprietary
                extra = json.dumps({
                    "proprietary_name": proprietary,
                    "dosage_form": row.get("DOSAGEFORMNAME", "").strip(),
                    "route": row.get("ROUTENAME", "").strip(),
                    "labeler": row.get("LABELERNAME", "").strip(),
                    "marketing_category": row.get("MARKETINGCATEGORYNAME", "").strip(),
                    "dea_schedule": row.get("DEASCHEDULE", "").strip(),
                })
                rows.append((CODE_TYPE, ndc, description, proprietary, 0, extra,
                              SOURCE_ID, source_url, retrieved_at))

    # NDC is large (~100k+ rows); use executemany in chunks
    chunk_size = 5000
    total = 0
    for i in range(0, len(rows), chunk_size):
        conn.executemany(
            """INSERT OR REPLACE INTO codes
               (code_type, code, description, short_description, is_header, extra,
                source_id, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            rows[i : i + chunk_size],
        )
        conn.commit()
        total += len(rows[i : i + chunk_size])

    return total


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
