"""Backfill the sources table from source_registry.yaml.

Run this after all ingestors have loaded data so the new c01 and e01 sources
are captured alongside the existing A/B/F sources. Safe to re-run (INSERT OR REPLACE).

Usage:
  source venv/Scripts/activate
  python backfill_sources.py
"""
from ingestors.core.db import get_conn
from ingestors.core.pipeline import load_registry

DATA_TABLES = [
    "codes",
    "plans",
    "plan_attributes",
    "plan_benefits",
    "plan_materials",
    "sbc_documents",
    "ambulance_fee_schedule",
    "nsa_rules",
]


def main():
    conn = get_conn()
    registry = load_registry()

    rows = [
        (
            s["source_id"],
            s["publisher"],
            s.get("canonical_url") or None,
            s.get("license"),
            s.get("refresh_cadence"),
            None,
        )
        for s in registry.values()
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO sources
           (source_id, publisher, canonical_url, license, refresh_cadence, notes)
           VALUES (?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    print(f"[backfill_sources] {len(rows)} sources upserted into sources table")

    # Scan every data table for source_ids that have no sources row.
    found_orphans = False
    for table in DATA_TABLES:
        try:
            orphans = conn.execute(
                f"SELECT DISTINCT source_id FROM {table} "
                "WHERE source_id NOT IN (SELECT source_id FROM sources)"
            ).fetchall()
            for o in orphans:
                print(f"WARNING: source_id '{o[0]}' appears in {table} "
                      "but has no sources row — add to source_registry.yaml")
                found_orphans = True
        except Exception as e:
            print(f"[backfill_sources] skipping {table}: {e}")

    if not found_orphans:
        print("[backfill_sources] OK — all source_ids resolve to a sources row")


if __name__ == "__main__":
    main()
