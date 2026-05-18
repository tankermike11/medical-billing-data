"""Healthcare.gov Marketplace API ingestor (b05_marketplace_api).

Requires a free API key from https://developer.cms.gov/marketplace-api/
Set MARKETPLACE_API_KEY before running.

Two-step per ZIP:
  1. GET /counties/by/zip/{zip}  — resolves county FIPS and state
  2. POST /plans/search           — returns plans for that county

Only works for states on the federal marketplace (FFE/SBE-FP).
State-based exchanges (CA, NY, MA, etc.) return 400 and are skipped.

Writes to: plans, plan_benefits, plan_materials.
"""
import json
import os

from ingestors.core.db import get_conn
from ingestors.core.http import fetch_json, post_json
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "b05_marketplace_api"
PLAN_YEAR = 2026
BASE_URL = "https://marketplace.api.healthcare.gov/api/v1"

# All FFE states (use healthcare.gov, not a state-based exchange)
SAMPLE_ZIPS = [
    "77001",  # Houston, TX
    "33101",  # Miami, FL
    "43201",  # Columbus, OH
    "85001",  # Phoenix, AZ
    "28201",  # Charlotte, NC
]


def main():
    api_key = os.environ.get("MARKETPLACE_API_KEY", "")
    if not api_key:
        print(f"[{SOURCE_ID}] MARKETPLACE_API_KEY not set — skipping.")
        return

    conn = get_conn()
    retrieved_at = now_utc()
    total_plans = 0

    for zipcode in SAMPLE_ZIPS:
        # Step 1: resolve county FIPS
        try:
            county_data = fetch_json(
                f"{BASE_URL}/counties/by/zip/{zipcode}?apikey={api_key}&year={PLAN_YEAR}"
            )
        except Exception as e:
            print(f"[{SOURCE_ID}] FIPS lookup failed for {zipcode}: {e}")
            continue

        counties = county_data.get("counties", [])
        if not counties:
            print(f"[{SOURCE_ID}] No county found for ZIP {zipcode} — skipping.")
            continue
        county = counties[0]
        fips = county["fips"]
        state = county["state"]
        county_name = county["name"]

        # Step 2: fetch plans
        search_url = f"{BASE_URL}/plans/search?apikey={api_key}"
        body = {
            "year": PLAN_YEAR,
            "market": "Individual",
            "place": {"countyfips": fips, "state": state, "zipcode": zipcode},
            "limit": 100,
        }
        try:
            data = post_json(search_url, body)
        except Exception as e:
            print(f"[{SOURCE_ID}] ZIP {zipcode} ({state}) plans/search failed: {e}")
            continue

        plans = data.get("plans", [])
        print(f"[{SOURCE_ID}] ZIP {zipcode} ({county_name}, {state}) — {len(plans)} plans")

        for p in plans:
            plan_id = p.get("id", "")
            if not plan_id:
                continue

            extra = {
                "premium": p.get("premium"),
                "deductibles": p.get("deductibles"),
                "moops": p.get("moops"),
                "hsa_eligible": p.get("hsa_eligible"),
                "design_type": p.get("design_type"),
                "quality_rating": p.get("quality_rating"),
            }

            cur = conn.execute(
                """INSERT OR IGNORE INTO plans
                   (plan_id, plan_name, issuer_id, issuer_name, state,
                    county_fips, county_name, plan_type, metal_level,
                    plan_year, market_coverage, dental_only, national_network,
                    extra, source_id, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    plan_id,
                    p.get("name", ""),
                    p.get("issuer", {}).get("id", ""),
                    p.get("issuer", {}).get("name", ""),
                    state,
                    fips,
                    county_name,
                    p.get("type", ""),
                    p.get("metal_level", ""),
                    PLAN_YEAR,
                    "Individual",
                    0,
                    1 if p.get("has_national_network") else 0,
                    json.dumps(extra),
                    SOURCE_ID,
                    search_url,
                    retrieved_at,
                ),
            )
            plan_inserted = cur.rowcount > 0
            total_plans += 1

            if plan_inserted:
                _insert_benefits(conn, plan_id, p.get("benefits", []), retrieved_at)
                _insert_materials(conn, plan_id, p, retrieved_at)

        conn.commit()

    record_release(conn, SOURCE_ID, BASE_URL, "api_live", total_plans)
    print(f"[{SOURCE_ID}] done — {total_plans} plan records processed")


def _insert_benefits(conn, plan_id, benefits, retrieved_at):
    for b in benefits:
        cost_sharings = b.get("cost_sharings") or []
        inn = next((cs for cs in cost_sharings if "in-network" in cs.get("network_tier", "").lower()), {})
        conn.execute(
            """INSERT INTO plan_benefits
               (plan_id, plan_year, benefit_name, covered,
                copay_inn_tier1, coinsurance_inn_tier1,
                copay_outn, coinsurance_outn, is_excluded,
                source_id, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan_id, PLAN_YEAR,
                b.get("name", ""),
                "Yes" if b.get("covered") else "No",
                str(inn.get("copay_amount", "")) if inn else "",
                str(inn.get("coinsurance_rate", "")) if inn else "",
                "", "",
                1 if b.get("excluded") else 0,
                SOURCE_ID, retrieved_at,
            ),
        )


def _insert_materials(conn, plan_id, plan, retrieved_at):
    for material_type, url_key in [
        ("benefits_url", "benefits_url"),
        ("brochure_url", "brochure_url"),
        ("formulary_url", "formulary_url"),
        ("network_url", "network_url"),
    ]:
        url_val = plan.get(url_key, "")
        if url_val:
            conn.execute(
                """INSERT OR IGNORE INTO plan_materials
                   (plan_id, plan_year, material_type, url, source_id, retrieved_at)
                   VALUES (?,?,?,?,?,?)""",
                (plan_id, PLAN_YEAR, material_type, url_val, SOURCE_ID, retrieved_at),
            )


if __name__ == "__main__":
    main()
