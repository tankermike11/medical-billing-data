"""NCD 10.1 — Ambulance Services ingestor (Family E, E.2).

Source: CMS Medicare Coverage Database (NCD 10.1)
  Landing page / canonical_url: https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34

Fetches the NCD 10.1 HTML page and extracts criteria as draft rows (reviewed=0).
All rows must be human-reviewed before the application serves them — the app
queries WHERE reviewed=1 only.

Human review gate (after running this ingestor):
  1. Inspect: SELECT * FROM ncd_ambulance WHERE reviewed=0;
  2. Verify criteria_text against https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34
  3. Approve: UPDATE ncd_ambulance SET reviewed=1 WHERE ncd_id='10.1' AND section_id=<id>;
  4. Ensure all 7 criterion types are covered (see addendum §E.2 key criteria table).
"""
import html as html_mod
import re
import sqlite3
from pathlib import Path

from ingestors.core.pipeline import run_ingestor

SOURCE_ID = "e02_ncd_ambulance"
NCD_ID = "10.1"
EFFECTIVE_DATE = "2002-10-04"  # NCD 10.1 original effective date

_CRITERION_KEYWORDS: dict[str, list[str]] = {
    "medical_necessity": [
        "medical necessity", "medically necessary", "preclud", "endanger",
        "immediate ambulance", "required immediate",
    ],
    "facility_standard": [
        "nearest appropriate", "nearest facility", "appropriate facility",
        "capable of providing",
    ],
    "transport_condition": [
        "loaded mileage", "unloaded mileage", "a0425",
        "als1", "als2", "als emergency", "bls",
        "advanced life support", "basic life support",
        "origin", "destination", "modifier",
    ],
    "exclusion": [
        "not covered", "non-covered", "excluded", "coverage is not",
        "does not cover", "will not be covered",
    ],
    "definition": ["defined as", "means ", "refers to", "is defined"],
}


def _infer_criterion_type(text: str) -> str | None:
    t = text.lower()
    for ctype, keywords in _CRITERION_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return ctype
    return None


def _infer_coverage_indicator(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["not covered", "non-covered", "excluded", "does not cover"]):
        return "non_covered"
    if any(w in t for w in ["covered when", "covered if", "may be covered", "payable if"]):
        return "conditional"
    if any(w in t for w in ["is covered", "are covered", "coverage is provided", "will be covered"]):
        return "covered"
    return "informational"


def _clean_html(html: str) -> str:
    """Strip scripts, styles, tags; decode entities; collapse whitespace."""
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_mod.unescape(html)
    return re.sub(r"\s+", " ", html).strip()


def _parse_ncd_html(html: str, source_url: str) -> list[dict]:
    """
    Extract NCD 10.1 section text from the CMS HTML page.

    CMS NCD pages number sections as "10.1", "10.1.1", "10.1.2", etc.
    We find each section heading and capture the following text as its body.
    """
    text = _clean_html(html)

    # Match section numbers like 10.1, 10.1.1, 10.1.2 followed by a title
    sec_re = re.compile(r"\b(10\.1(?:\.\d+)*)\b\s*[-–—.]?\s*([A-Z][^.]{4,120}\.?)")
    matches = list(sec_re.finditer(text))

    rows: list[dict] = []

    if not matches:
        print(f"[{SOURCE_ID}] WARNING: no section headers found in HTML — storing full text as single draft row")
        body = text[:5000]
        rows.append({
            "ncd_id": NCD_ID,
            "section_id": "10.1.0",
            "section_title": "NCD 10.1 Ambulance Services (full text — parser needs update)",
            "coverage_indicator": "informational",
            "criteria_text": body,
            "criterion_type": None,
            "effective_date": EFFECTIVE_DATE,
            "citation": f"CMS NCD 10.1 — {source_url}",
        })
        return rows

    for idx, m in enumerate(matches):
        section_id = m.group(1)
        title = m.group(2).strip().rstrip(".")
        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else body_start + 3000
        body = text[body_start:body_end].strip()

        if len(body) < 20:
            continue

        rows.append({
            "ncd_id": NCD_ID,
            "section_id": section_id,
            "section_title": title,
            "coverage_indicator": _infer_coverage_indicator(body),
            "criteria_text": body[:5000],
            "criterion_type": _infer_criterion_type(body),
            "effective_date": EFFECTIVE_DATE,
            "citation": f"CMS NCD {section_id} — {source_url}",
        })

    return rows


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    source_url = source["canonical_url"]
    html = local_path.read_text(encoding="utf-8", errors="replace")
    rows = _parse_ncd_html(html, source_url)

    if not rows:
        raise ValueError("0 rows extracted from NCD HTML — check raw file and update _parse_ncd_html()")

    found_types = {r["criterion_type"] for r in rows if r["criterion_type"]}
    required_types = {"medical_necessity", "facility_standard", "transport_condition", "exclusion"}
    missing = required_types - found_types
    if missing:
        print(f"[{SOURCE_ID}] WARNING: criterion types not auto-detected: {missing}")
        print(f"[{SOURCE_ID}]   Assign these manually during human review (UPDATE criterion_type)")

    db_rows = [
        (r["ncd_id"], r["section_id"], r["section_title"], r["coverage_indicator"],
         r["criteria_text"], r["criterion_type"], r["effective_date"], r["citation"],
         0,  # reviewed=0 — human review gate, never set programmatically
         SOURCE_ID)
        for r in rows
    ]

    conn.executemany(
        """INSERT OR REPLACE INTO ncd_ambulance
           (ncd_id, section_id, section_title, coverage_indicator, criteria_text,
            criterion_type, effective_date, citation, reviewed, source_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        db_rows,
    )
    conn.commit()

    print(f"[{SOURCE_ID}] {len(db_rows)} draft rows written (reviewed=0)")
    print(f"[{SOURCE_ID}] NEXT: review against {source_url} then SET reviewed=1")
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
