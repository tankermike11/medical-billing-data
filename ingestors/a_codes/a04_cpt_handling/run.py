"""CPT detection-only handler.

CPT is AMA-licensed. This module does NOT download or store CPT descriptors.
It writes a single sentinel record confirming detection-only mode is active,
which downstream callers can use to return descriptor_source=unavailable_unlicensed.

No network call is made.
"""
import sqlite3
from pathlib import Path

from ingestors.core.db import get_conn
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "a04_cpt_handling"
CPT_PATTERN = r"^\d{5}$"  # 5-digit numeric code — detection regex for use in PHI service


def main():
    conn = get_conn()
    retrieved_at = now_utc()
    conn.execute(
        """INSERT OR REPLACE INTO codes
           (code_type, code, description, short_description, is_header, source_id, retrieved_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "CPT_SENTINEL",
            "_detection_only",
            f"CPT codes are AMA-licensed. Detection regex: {CPT_PATTERN}. descriptor_source=unavailable_unlicensed.",
            "CPT detection-only mode",
            0,
            SOURCE_ID,
            retrieved_at,
        ),
    )
    conn.commit()
    record_release(conn, SOURCE_ID, "", "no_download", 1, "success", "detection-only sentinel written")
    print(f"[{SOURCE_ID}] CPT detection-only sentinel written. No descriptors ingested.")


if __name__ == "__main__":
    main()
