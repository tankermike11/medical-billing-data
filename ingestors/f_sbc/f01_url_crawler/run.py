"""SBC URL Crawler — Family F, Step 1.

Reads SBC URLs from the plan_materials table (populated by b01_qhp_landscape)
and validates each URL by making a HEAD request. Records the HTTP status and
content-type back into plan_materials so the downloader (f02) can skip bad URLs.

This step does NOT download the PDFs — that is f02_downloader.
"""
import sqlite3
import time
from pathlib import Path

import httpx

from ingestors.core.db import get_conn
from ingestors.core.http import UA, RATE_LIMIT_DELAY, _ssl_ctx
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "f01_url_crawler"
PLAN_YEAR = 2025
REQUEST_TIMEOUT = 20
MAX_URLS = None  # set to an int (e.g. 50) to cap during pilot testing


def _check_url(client: httpx.Client, url: str) -> tuple[int, str]:
    try:
        r = client.head(url, follow_redirects=True, timeout=REQUEST_TIMEOUT)
        return r.status_code, r.headers.get("content-type", "")
    except Exception as e:
        return 0, str(e)[:120]


def main():
    conn = get_conn()

    urls = conn.execute(
        """SELECT id, plan_id, url FROM plan_materials
           WHERE material_type='sbc' AND plan_year=? AND (http_status IS NULL OR http_status=0)
           ORDER BY id""",
        (PLAN_YEAR,),
    ).fetchall()

    if not urls:
        print(f"[{SOURCE_ID}] No pending SBC URLs found. Run b01_qhp_landscape first.")
        return

    if MAX_URLS:
        urls = urls[:MAX_URLS]

    print(f"[{SOURCE_ID}] Checking {len(urls)} SBC URLs...")
    checked = 0
    ok = 0

    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, verify=_ssl_ctx) as client:
        for row in urls:
            status, ctype = _check_url(client, row["url"])
            conn.execute(
                "UPDATE plan_materials SET http_status=?, content_type=? WHERE id=?",
                (status, ctype, row["id"]),
            )
            conn.commit()
            checked += 1
            if status == 200:
                ok += 1
            if checked % 100 == 0:
                print(f"[{SOURCE_ID}]   {checked}/{len(urls)} checked — {ok} OK so far")
            time.sleep(RATE_LIMIT_DELAY)

    record_release(conn, SOURCE_ID, "plan_materials", "url_check", checked,
                   notes=f"{ok}/{checked} URLs returned HTTP 200")
    print(f"[{SOURCE_ID}] done — {ok}/{checked} SBC URLs are reachable")


if __name__ == "__main__":
    main()
