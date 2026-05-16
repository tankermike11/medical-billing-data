"""Healthcare.gov Marketplace API ingestor.

Requires a free API key from https://developer.cms.gov/marketplace-api/
Set the environment variable MARKETPLACE_API_KEY before running.

This pilot ingestor fetches plan data for a sample of ZIP codes to supplement
the static PUF files with live data. It writes to the same plans table.

Base URL: https://marketplace.api.healthcare.gov/api/v1/
Key endpoint: GET /plans/search?apikey=KEY&zipcode=XXXXX&year=2025
"""
import os
import sqlite3
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.http import fetch_json
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "b05_marketplace_api"
PLAN_YEAR = 2025
BASE_URL = "https://marketplace.api.healthcare.gov/api/v1"

SAMPLE_ZIPS = ["10001", "90210", "60601", "77001", "30301"]


def main():
    api_key = os.environ.get("MARKETPLACE_API_KEY", "")
    if not api_key:
        print(f"[{SOURCE_ID}] MARKETPLACE_API_KEY not set. Register at https://developer.cms.gov/marketplace-api/")
        print(f"[{SOURCE_ID}] Skipping — set the env var and re-run.")
        return

    conn = get_conn()
    retrieved_at = now_utc()
    total = 0

    for zipcode in SAMPLE_ZIPS:
        url = f"{BASE_URL}/plans/search?apikey={api_key}&zipcode={zipcode}&year={PLAN_YEAR}&limit=100"
        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"[{SOURCE_ID}] ZIP {zipcode} failed: {e}")
            continue

        plans = data.get("plans", [])
        for p in plans:
            plan_id = p.get("id", "")
            if not plan_id:
                continue
            conn.execute(
                """INSERT OR IGNORE INTO plans
                   (plan_id, plan_name, issuer_id, issuer_name, state, plan_type,
                    metal_level, plan_year, market_coverage, dental_only,
                    source_id, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    plan_id,
                    p.get("name", ""),
                    p.get("issuer", {}).get("id", ""),
                    p.get("issuer", {}).get("name", ""),
                    p.get("state", ""),
                    p.get("type", ""),
                    p.get("metal_level", ""),
                    PLAN_YEAR,
                    "Individual",
                    0,
                    SOURCE_ID,
                    url,
                    retrieved_at,
                ),
            )
            total += 1

        conn.commit()
        print(f"[{SOURCE_ID}] ZIP {zipcode} — {len(plans)} plans")

    record_release(conn, SOURCE_ID, BASE_URL, "api_live", total)
    print(f"[{SOURCE_ID}] done — {total} plan records from API sample")


if __name__ == "__main__":
    main()
