"""NCD 10.1 — Ambulance Services ingestor (Family E, E.2).

Source: Medicare Benefit Policy Manual, Chapter 10 — Ambulance Services (Pub. 100-02)
  canonical_url: https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c10.pdf

Downloads the PDF, extracts section text with pdfplumber, and writes one row per
section with reviewed=0. The application serves only reviewed=1 rows — a human
reviewer must verify each row's criteria_text against the PDF before approving.

Human review gate (after running this ingestor):
  1. Open the PDF at the canonical_url above
  2. Verify criteria_text for each row against the corresponding section
  3. Approve: UPDATE ncd_ambulance SET reviewed=1 WHERE ncd_id='10.1' AND section_id=<id>;
  4. Ensure all 4 required criterion_type groups are covered and reviewed
"""
import re
import sqlite3
from pathlib import Path

import pdfplumber

from ingestors.core.pipeline import run_ingestor

SOURCE_ID = "e02_ncd_ambulance"
NCD_ID = "10.1"
EFFECTIVE_DATE = "2002-10-04"  # NCD 10.1 original effective date

_VALID_CRITERION_TYPES = {
    "medical_necessity", "transport_condition", "facility_standard", "exclusion", "definition"
}
_REQUIRED_CRITERION_TYPES = {
    "medical_necessity", "transport_condition", "facility_standard", "exclusion"
}

# Keywords used to assign criterion_type to each section
_CRITERION_KEYWORDS: dict[str, list[str]] = {
    "medical_necessity": [
        "medical necessity", "medically necessary", "preclud", "endanger",
        "immediate ambulance", "required immediate", "contraindicated",
    ],
    "facility_standard": [
        "nearest appropriate", "nearest facility", "appropriate facilit",
        "nearest hosp", "capable of providing", "equipped to provide",
    ],
    "transport_condition": [
        "loaded mileage", "unloaded mileage", "a0425",
        "als1", "als2", "als emergency", "bls",
        "advanced life support", "basic life support",
        "origin", "destination", "modifier",
        "base rate", "service level",
    ],
    "exclusion": [
        "not covered", "non-covered", "excluded", "coverage is not",
        "does not cover", "will not be covered", "noncovered",
    ],
    "definition": ["defined as", "means ", "refers to", "is defined", "the term"],
}


def _infer_criterion_type(text: str) -> str | None:
    t = text.lower()
    for ctype, keywords in _CRITERION_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return ctype
    return None


def _infer_coverage_indicator(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["not covered", "non-covered", "excluded", "noncovered", "does not cover"]):
        return "non_covered"
    if any(w in t for w in ["covered when", "covered if", "may be covered", "payable if", "provided that"]):
        return "conditional"
    if any(w in t for w in ["is covered", "are covered", "coverage is provided", "will be covered"]):
        return "covered"
    return "informational"


def _extract_pdf_text(local_path: Path) -> str:
    """Return full text of all pages joined with newlines."""
    pages = []
    with pdfplumber.open(local_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def _parse_sections(full_text: str, source_url: str) -> list[dict]:
    """
    Split PDF text into sections on boundaries matching 'Chapter 10' section
    numbering: 10, 10.1, 10.1.1, 10.1.2, etc.

    Each section becomes one row in ncd_ambulance.
    """
    # Section header pattern: number at start of line, dash/em-dash, title
    # Matches: "10.1 - General" or "10.1.1 – Emergency Ambulance" or "10.1\nGeneral"
    sec_re = re.compile(
        r"(?:^|\n)\s*(10(?:\.\d+)+)\s*[-–—]?\s*([A-Za-z][^\n]{3,120})",
        re.MULTILINE,
    )
    matches = list(sec_re.finditer(full_text))

    rows: list[dict] = []

    if not matches:
        # Fallback: store the entire text as one informational row
        print(f"[{SOURCE_ID}] WARNING: no section headers found — storing full text as single draft row")
        rows.append({
            "section_id": "10.1.0",
            "section_title": "Chapter 10 Ambulance Services (full text — section parse failed)",
            "coverage_indicator": "informational",
            "criteria_text": full_text[:5000],
            "criterion_type": None,
            "citation": f"CMS Pub. 100-02, Ch. 10 — {source_url}",
        })
        return rows

    for idx, m in enumerate(matches):
        section_id = m.group(1).strip()
        title = m.group(2).strip().rstrip(".")

        body_start = m.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else body_start + 4000
        body = full_text[body_start:body_end].strip()

        # Collapse excessive whitespace from PDF extraction
        body = re.sub(r"\n{3,}", "\n\n", body)
        body = re.sub(r"[ \t]{2,}", " ", body)

        if len(body) < 20:
            continue

        rows.append({
            "section_id": section_id,
            "section_title": title,
            "coverage_indicator": _infer_coverage_indicator(body),
            "criteria_text": body[:5000],
            "criterion_type": _infer_criterion_type(body),
            "citation": f"CMS Pub. 100-02, Ch. 10, §{section_id} — {source_url}",
        })

    return rows


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    source_url = source["canonical_url"]

    print(f"[{SOURCE_ID}] extracting text from PDF ({local_path.stat().st_size // 1024} KB)")
    full_text = _extract_pdf_text(local_path)
    print(f"[{SOURCE_ID}] extracted {len(full_text):,} characters")

    rows = _parse_sections(full_text, source_url)
    if not rows:
        raise ValueError("0 sections extracted from PDF — check _parse_sections()")

    found_types = {r["criterion_type"] for r in rows if r["criterion_type"]}
    missing = _REQUIRED_CRITERION_TYPES - found_types
    if missing:
        print(f"[{SOURCE_ID}] WARNING: criterion types not auto-detected: {missing}")
        print(f"[{SOURCE_ID}]   Assign these manually during human review (UPDATE criterion_type)")

    db_rows = [
        (NCD_ID, r["section_id"], r["section_title"], r["coverage_indicator"],
         r["criteria_text"], r["criterion_type"], EFFECTIVE_DATE, r["citation"],
         0,  # reviewed=0 always — never set programmatically
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
    print(f"[{SOURCE_ID}] NEXT: verify rows against {source_url} then SET reviewed=1")
    return len(db_rows)


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
