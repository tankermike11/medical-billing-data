"""SBC PDF Downloader — Family F, Step 2.

Downloads SBC PDFs for all plan_materials rows where:
  - material_type = 'sbc'
  - http_status = 200
  - content_type contains 'pdf'
  - not yet downloaded (no sbc_documents record exists)

Raw PDFs are stored at: data/raw/f_sbc/<plan_id>_<plan_year>.pdf
Records are written to the sbc_documents table.

Run f01_url_crawler before this step.
"""
import hashlib
import sqlite3
import time
from pathlib import Path

import httpx

from ingestors.core.db import get_conn
from ingestors.core.http import UA, RATE_LIMIT_DELAY, _ssl_ctx
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "f02_downloader"
PLAN_YEAR = 2025
SBC_RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "f_sbc"
REQUEST_TIMEOUT = 60
MAX_DOWNLOADS = None  # set to int to cap during pilot (e.g. 50)


def _safe_filename(plan_id: str, year: int) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in plan_id)
    return f"{safe}_{year}.pdf"


def main():
    conn = get_conn()
    SBC_RAW_DIR.mkdir(parents=True, exist_ok=True)

    pending = conn.execute(
        """SELECT pm.id, pm.plan_id, pm.url
           FROM plan_materials pm
           LEFT JOIN sbc_documents sd ON sd.url = pm.url AND sd.plan_year = pm.plan_year
           WHERE pm.material_type='sbc'
             AND pm.plan_year=?
             AND pm.http_status=200
             AND (pm.content_type LIKE '%pdf%' OR pm.content_type = '')
             AND sd.id IS NULL
           ORDER BY pm.id""",
        (PLAN_YEAR,),
    ).fetchall()

    if not pending:
        print(f"[{SOURCE_ID}] No pending SBC downloads. Run f01_url_crawler first.")
        return

    if MAX_DOWNLOADS:
        pending = pending[:MAX_DOWNLOADS]

    print(f"[{SOURCE_ID}] Downloading {len(pending)} SBCs...")
    downloaded = 0
    failed = 0
    retrieved_at = now_utc()

    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=REQUEST_TIMEOUT, verify=_ssl_ctx) as client:
        for row in pending:
            plan_id = row["plan_id"]
            url = row["url"]
            dest = SBC_RAW_DIR / _safe_filename(plan_id, PLAN_YEAR)

            try:
                if dest.exists():
                    file_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
                else:
                    r = client.get(url)
                    r.raise_for_status()
                    if b"%PDF" not in r.content[:10] and "pdf" not in r.headers.get("content-type", "").lower():
                        raise ValueError(f"Response is not a PDF (content-type: {r.headers.get('content-type')})")
                    dest.write_bytes(r.content)
                    file_hash = hashlib.sha256(r.content).hexdigest()

                import pdfplumber
                with pdfplumber.open(dest) as pdf:
                    page_count = len(pdf.pages)

                conn.execute(
                    """INSERT OR IGNORE INTO sbc_documents
                       (plan_id, plan_year, url, file_hash, local_path, page_count,
                        source_id, retrieved_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (plan_id, PLAN_YEAR, url, file_hash, str(dest), page_count, SOURCE_ID, retrieved_at),
                )
                conn.commit()
                downloaded += 1

            except Exception as e:
                print(f"[{SOURCE_ID}]   FAIL {plan_id}: {e}")
                failed += 1

            if (downloaded + failed) % 50 == 0:
                print(f"[{SOURCE_ID}]   {downloaded} downloaded, {failed} failed")
            time.sleep(RATE_LIMIT_DELAY)

    record_release(conn, SOURCE_ID, "plan_materials", "pdf_download", downloaded,
                   notes=f"{failed} failed downloads")
    print(f"[{SOURCE_ID}] done — {downloaded} PDFs downloaded, {failed} failed")


if __name__ == "__main__":
    main()
