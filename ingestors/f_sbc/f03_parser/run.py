"""SBC PDF Parser — Family F, Step 3.

Extracts structured fields from downloaded SBC PDFs using pdfplumber (native).
No LLM is used. Low-confidence extractions are flagged for manual review.

ACA mandates a standardized SBC template. Key sections we extract:
  - Deductible (individual, family; in-network, out-of-network)
  - Out-of-Pocket Maximum (individual, family; in-network, out-of-network)
  - Copays for common services (primary care, specialist, ER, urgent care)
  - Coinsurance
  - Prior authorization requirements

Extraction uses regex patterns against the text of each page.
Confidence is assigned based on whether the pattern matched in the expected
location (known SBC section headers) vs. a fallback generic match.
"""
import re
import sqlite3
from pathlib import Path

import pdfplumber

from ingestors.core.db import get_conn
from ingestors.core.pipeline import now_utc, record_release

SOURCE_ID = "f03_parser"
PLAN_YEAR = 2025
CONFIDENCE_HIGH = 0.9
CONFIDENCE_MEDIUM = 0.6
CONFIDENCE_LOW = 0.3
REVIEW_THRESHOLD = 0.5


# ─── Field extraction patterns ───────────────────────────────────────────────

CURRENCY_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?|No [Cc]harge|Not [Cc]overed|\d+%")

FIELD_PATTERNS: dict[str, list[re.Pattern]] = {
    # Deductible: anchored to "In-network: $X Individual / $Y Family" near "deductible"
    "deductible_individual_inn": [
        re.compile(r"[Dd]eductible.{0,100}[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual", re.DOTALL),
        re.compile(r"[Dd]eductible.{0,200}(\$[\d,]+)\s+[Ii]ndividual", re.DOTALL),
    ],
    "deductible_family_inn": [
        # capture the second value: "Individual / $Y"
        re.compile(r"[Dd]eductible.{0,100}[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+)", re.DOTALL),
        re.compile(r"[Dd]eductible.{0,200}[Ii]ndividual\s*/\s*(\$[\d,]+)", re.DOTALL),
    ],
    # OOP max: PDF extraction puts "In-network: $X Individual / $Y" BEFORE "out-of-pocket" on
    # the same line ("In-network: $6,300 Individual / $12,600 The out-of-pocket limit is...")
    # so we look for "In-network: $X Individual" and require "out-of-pocket" within 200 chars AFTER.
    # The deductible "In-network: $X Individual" line does NOT have "out-of-pocket" nearby.
    "oop_max_individual_inn": [
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual.{0,200}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual.{0,300}[Oo]ut.of.[Pp]ocket", re.DOTALL),
    ],
    "oop_max_family_inn": [
        re.compile(r"[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+).{0,200}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+).{0,300}[Oo]ut.of.[Pp]ocket", re.DOTALL),
    ],
    # Copays: DOTALL with limited window so amount can be on a following line
    "copay_primary_care": [
        re.compile(r"[Pp]rimary [Cc]are [Vv]isit.{0,300}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"[Pp]rimary [Cc]are.{0,300}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
    ],
    "copay_specialist": [
        re.compile(r"[Ss]pecialist [Vv]isit.{0,300}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"[Ss]pecialist.{0,300}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
    ],
    "copay_er": [
        re.compile(r"Emergency [Rr]oom.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"Emergency [Mm]edical.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
    ],
    "copay_urgent_care": [
        re.compile(r"[Uu]rgent [Cc]are.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
    ],
    "copay_generic_drug": [
        re.compile(r"[Gg]eneric [Dd]rug.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"[Pp]referred [Gg]eneric.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"[Gg]eneric.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
    ],
}

SECTION_HEADERS = [
    "Important Questions", "Common Medical Events", "If you visit",
    "Deductible", "Out-of-Pocket", "Summary of Benefits",
]


def _extract_text(pdf_path: Path) -> tuple[str, int]:
    with pdfplumber.open(pdf_path) as pdf:
        pages = len(pdf.pages)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return text, pages


def _normalize_text(text: str) -> str:
    """Insert spaces at word boundaries lost during PDF character extraction."""
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = re.sub(r'([a-zA-Z])(\$)', r'\1 \2', text)
    text = re.sub(r'(\d)([A-Z])', r'\1 \2', text)
    return text


def _find_field(text: str, field_name: str) -> tuple[str | None, float]:
    patterns = FIELD_PATTERNS.get(field_name, [])
    for i, pat in enumerate(patterns):
        m = pat.search(text)
        if m:
            value = m.group(1).strip()
            confidence = CONFIDENCE_HIGH if i == 0 else CONFIDENCE_MEDIUM
            return value, confidence
    return None, 0.0


def _has_sbc_markers(text: str, min_count: int = 3) -> bool:
    found = sum(1 for h in SECTION_HEADERS if h in text)
    return found >= min_count


def parse_sbc(pdf_path: Path) -> dict[str, tuple[str | None, float]]:
    try:
        text, pages = _extract_text(pdf_path)
    except Exception as e:
        return {"_error": (str(e), 0.0)}

    if not text.strip():
        return {"_error": ("Empty PDF text — may need OCR", 0.0)}

    if not _has_sbc_markers(text):
        # Some PDFs lose inter-word spacing; try normalizing before giving up
        normalized = _normalize_text(text)
        if _has_sbc_markers(normalized, min_count=2):
            text = normalized
        else:
            return {"_error": ("Document does not appear to be a standard SBC template", CONFIDENCE_LOW)}

    results = {}
    for field in FIELD_PATTERNS:
        value, confidence = _find_field(text, field)
        results[field] = (value, confidence)
    results["_page_count"] = (str(pages), 1.0)
    return results


def main():
    conn = get_conn()
    retrieved_at = now_utc()

    pending = conn.execute(
        """SELECT sd.id, sd.plan_id, sd.local_path, sd.url
           FROM sbc_documents sd
           LEFT JOIN sbc_fields sf ON sf.sbc_document_id = sd.id
           WHERE sd.plan_year=? AND sf.id IS NULL AND sd.local_path IS NOT NULL
           ORDER BY sd.id""",
        (PLAN_YEAR,),
    ).fetchall()

    if not pending:
        print(f"[{SOURCE_ID}] No unprocessed SBC documents. Run f02_downloader first.")
        return

    print(f"[{SOURCE_ID}] Parsing {len(pending)} SBC PDFs...")
    parsed = 0
    flagged_for_review = 0
    field_rows = []

    for row in pending:
        doc_id = row["id"]
        plan_id = row["plan_id"]
        pdf_path = Path(row["local_path"])

        if not pdf_path.exists():
            print(f"[{SOURCE_ID}]   MISSING: {pdf_path}")
            continue

        fields = parse_sbc(pdf_path)

        if "_error" in fields:
            err_val, err_conf = fields["_error"]
            field_rows.append((doc_id, "_error", err_val, err_conf, "native", None))
            flagged_for_review += 1
            continue

        for field_name, (value, confidence) in fields.items():
            field_rows.append((doc_id, field_name, value, confidence, "native", None))
            if confidence < REVIEW_THRESHOLD and value is not None:
                flagged_for_review += 1

        avg_confidence = sum(c for _, c in fields.values() if c > 0) / max(len(fields), 1)
        conn.execute(
            "UPDATE sbc_documents SET extraction_method='native', extraction_confidence=? WHERE id=?",
            (round(avg_confidence, 3), doc_id),
        )
        parsed += 1

        if parsed % 50 == 0:
            print(f"[{SOURCE_ID}]   {parsed}/{len(pending)} parsed")

    conn.executemany(
        """INSERT INTO sbc_fields
           (sbc_document_id, field_name, field_value, confidence, extraction_method, page_number)
           VALUES (?,?,?,?,?,?)""",
        field_rows,
    )
    conn.commit()

    record_release(conn, SOURCE_ID, "sbc_documents", "pdfplumber_native", parsed,
                   notes=f"{flagged_for_review} fields/docs flagged for review")
    print(f"[{SOURCE_ID}] done — {parsed} SBCs parsed, {flagged_for_review} flagged for review")


if __name__ == "__main__":
    main()
