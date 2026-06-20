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
    # Deductible individual in-network.
    # Format 1 (most common): "In-network: $X Individual" near "deductible"
    # Format 2 (AZ BCBS): "$X/individual and $Y/family" after "deductible?" label
    # Format 3 (AL Blue): "$X / individual or $Y / family"
    # Format 4 (AL Blue Self-Only): "$X / Self-Only or $Y / Family"
    # Format 5+ (FL Blue, Quartz, WI/TX, MI, MT, IL): see below
    "deductible_individual_inn": [
        re.compile(r"[Dd]eductible.{0,100}[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual", re.DOTALL),
        # AZ BCBS: value after "deductible" keyword (no "?" anchor — ? appears after value in linearized tables)
        re.compile(r"[Dd]eductible.{0,600}(\$[\d,]+)/[Ii]ndividual\s+and", re.DOTALL),
        # AL Blue: "$X / individual or $Y / family" (family may be cut off by adjacent column text)
        re.compile(r"(\$[\d,]+)\s*/\s*[Ii]ndividual\s+or\s+\$[\d,]+\s*/", re.IGNORECASE),
        # AL Blue Self-Only variant
        re.compile(r"(\$[\d,]+)\s*/\s*[Ss]elf.Only\s+or\s+\$[\d,]+\s*/", re.IGNORECASE),
        re.compile(r"[Dd]eductible.{0,200}(\$[\d,]+)\s+[Ii]ndividual", re.DOTALL),
        # FL Blue: "In-Network: $X Per Person/$Y" — value embedded in question row
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)\s+Per\s+Person/\$[\d,]+.{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}[Ii]n.network:\s*(\$[\d,]+)\s+Per\s+Person", re.DOTALL | re.IGNORECASE),
        # WI/TX/NH: "$X/person or $Y/family for In-Network"
        re.compile(r"(\$[\d,]+)/[Pp]erson\s+or\s+\$[\d,]+/[Ff]amily.{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}(\$[\d,]+)/[Pp]erson\s+or\s+\$[\d,]+/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # MI/FL: "$X person / $Y family" or "$X person/ $Y family"
        re.compile(r"(\$[\d,]+)\s+person\s*/\s*\$[\d,]+\s+family.{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}(\$[\d,]+)\s+person\s*/\s*\$[\d,]+\s+family", re.DOTALL | re.IGNORECASE),
        # MT: "Network provider: $X/ individual or $Y/ family"
        re.compile(r"[Nn]etwork\s+provider:\s*(\$[\d,]+)/\s*.{0,30}individual\s+or", re.DOTALL | re.IGNORECASE),
        # IL 36096: "Individual: Participating $X; Non-Participating $Y"
        re.compile(r"[Ii]ndividual:\s*Participating\s+(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # Quartz (space-free PDF text, normalized to "Individual: $X"): value before "deductible"
        re.compile(r"Individual:\s*(\$[\d,]+).{0,400}deductible", re.DOTALL),
        re.compile(r"deductible.{0,400}Individual:\s*(\$[\d,]+)", re.DOTALL),
        # NE: "In-Network:$X/$Y" slash-separated individual/family near deductible
        re.compile(r"deductible.{0,400}[Ii]n.network:\s*(\$[\d,]+)/\s*\$[\d,]+", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)/\s*\$[\d,]+.{0,400}deductible", re.DOTALL | re.IGNORECASE),
    ],
    "deductible_family_inn": [
        re.compile(r"[Dd]eductible.{0,100}[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+)", re.DOTALL),
        re.compile(r"[Dd]eductible.{0,600}\$[\d,]+/[Ii]ndividual\s+and\s+(\$[\d,]+)/[Ff]amily", re.DOTALL),
        re.compile(r"\$[\d,]+\s*/\s*[Ii]ndividual\s+or\s+(\$[\d,]+)\s*/\s*[Ff]amily", re.IGNORECASE),
        re.compile(r"\$[\d,]+\s*/\s*[Ss]elf.Only\s+or\s+(\$[\d,]+)\s*/\s*[Ff]amily", re.IGNORECASE),
        re.compile(r"[Dd]eductible.{0,200}[Ii]ndividual\s*/\s*(\$[\d,]+)", re.DOTALL),
        # FL Blue: Per Person/$Y family value
        re.compile(r"[Ii]n.network:\s*\$[\d,]+\s+Per\s+Person/(\$[\d,]+).{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}[Ii]n.network:\s*\$[\d,]+\s+Per\s+Person/(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # WI/TX/NH: "$X/person or $Y/family"
        re.compile(r"\$[\d,]+/[Pp]erson\s+or\s+(\$[\d,]+)/[Ff]amily.{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}\$[\d,]+/[Pp]erson\s+or\s+(\$[\d,]+)/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # MI/FL: "$X person / $Y family"
        re.compile(r"\$[\d,]+\s+person\s*/\s*(\$[\d,]+)\s+family.{0,400}deductible", re.DOTALL | re.IGNORECASE),
        re.compile(r"deductible.{0,400}\$[\d,]+\s+person\s*/\s*(\$[\d,]+)\s+family", re.DOTALL | re.IGNORECASE),
        # MT: family value follows "individual or $Y/ family"
        re.compile(r"[Nn]etwork\s+provider:\s*\$[\d,]+/.{0,30}individual\s+or\s+(\$[\d,]+)/\s*family", re.DOTALL | re.IGNORECASE),
        # IL 36096: "Family: Participating $X"
        re.compile(r"[Ff]amily:\s*Participating\s+(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # Quartz: "Family: $X/individualor $Y/family" (normalized text)
        re.compile(r"Family:\s*\$[\d,]+/\w+\s+(\$[\d,]+)/family.{0,400}deductible", re.DOTALL),
        re.compile(r"deductible.{0,400}Family:\s*\$[\d,]+/\w+\s+(\$[\d,]+)/family", re.DOTALL),
        # NE: slash-separated family value
        re.compile(r"deductible.{0,400}[Ii]n.network:\s*\$[\d,]+/\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*\$[\d,]+/\s*(\$[\d,]+).{0,400}deductible", re.DOTALL | re.IGNORECASE),
    ],
    # OOP max individual in-network.
    # Format 1 (most common): "In-network: $X Individual ... out-of-pocket" (value before question)
    # Format 2 (AK/Tier, value before question): "Tier 1: $X individual / $Y family ... out-of-pocket"
    # Format 3 (AK/Tier, value after question): "out-of-pocket ... Tier 1: $X individual"
    # Format 4 (AZ BCBS, value after question): "out-of-pocket ... $X/individual and"
    "oop_max_individual_inn": [
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual.{0,200}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"Tier \d+:\s*(\$[\d,]+)\s+individual\s*/\s*\$[\d,]+\s+family.{0,300}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,300}Tier \d+:\s*(\$[\d,]+)\s+individual", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,200}(\$[\d,]+)/[Ii]ndividual\s+and", re.DOTALL | re.IGNORECASE),
        # AL Blue: "For in-network $6,000 individual / $12,000 family" before "out-of-pocket" question
        re.compile(r"[Ff]or in.network\s+(\$[\d,]+)\s+individual\s*/\s*\$[\d,]+.{0,300}out.of.pocket", re.DOTALL | re.IGNORECASE),
        # AL Blue: value appears after "out-of-pocket" question text
        re.compile(r"out.of.pocket.{0,300}[Ff]or in.network\s+(\$[\d,]+)\s+individual", re.DOTALL | re.IGNORECASE),
        # Generic: "$X individual / $Y family" near out-of-pocket
        re.compile(r"out.of.pocket.{0,200}(\$[\d,]+)\s+individual\s*/\s*\$[\d,]+\s+family", re.DOTALL | re.IGNORECASE),
        # AL Blue: "For network providers: $9,200 [text] individual / $18,400"
        # "individual" can be separated by hundreds of chars of linearized left-column text
        re.compile(r"[Ff]or network providers:\s*(\$[\d,]+).{0,600}individual\s*/", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}[Ff]or network providers:\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # AL Self-Only: "limit for this plan? $7,200 Self-Only / $14,400 Family"
        re.compile(r"limit for this plan\?\s*(\$[\d,]+)\s+Self.Only", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}[Ff]or in.network\s+(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # AZ BCBS: "In-Network: Individual $7,495 / Family $14,990"
        re.compile(r"[Ii]n.Network:\s+[Ii]ndividual\s+(\$[\d,]+)\s*/\s*[Ff]amily", re.DOTALL | re.IGNORECASE),
        # "$X/Individual, $Y/Family" — comma-separated with capital I/F, value before question
        re.compile(r"(\$[\d,]+)/[Ii]ndividual,\s*\$[\d,]+/[Ff]amily.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}(\$[\d,]+)/[Ii]ndividual,\s*\$[\d,]+/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # "$X Per Person/$Y" answer column appears BEFORE out-of-pocket question in linearized text
        re.compile(r"(\$[\d,]+)\s+[Pp]er\s+[Pp]erson\s*/\s*\$[\d,]+.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        # "$X per person | $Y per group" — pipe separator, no comma in dollars
        re.compile(r"(\$[\d,]+)\s+per\s+person\s*\|.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        # "$X/person or $Y/family" format
        re.compile(r"(\$[\d,]+)/[Pp]erson\s+or\s+\$[\d,]+/[Ff]amily.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}(\$[\d,]+)/[Pp]erson\s+or\s+\$[\d,]+/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # "pocket limit for this $9,050 person/ $18,100 family" — question wraps, value embedded
        re.compile(r"pocket limit for this\s+(\$[\d,]+)\s+person/", re.DOTALL | re.IGNORECASE),
        # "Network providers: $X" (without leading "For")
        re.compile(r"[Nn]etwork providers:\s*(\$[\d,]+).{0,600}individual\s*/", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*(\$[\d,]+)\s+[Ii]ndividual.{0,300}[Oo]ut.of.[Pp]ocket", re.DOTALL),
    ],
    "oop_max_family_inn": [
        re.compile(r"[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+).{0,200}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"Tier \d+:\s*\$[\d,]+\s+individual\s*/\s*(\$[\d,]+)\s+family.{0,300}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,300}Tier \d+:\s*\$[\d,]+\s+individual\s*/\s*(\$[\d,]+)\s+family", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,200}\$[\d,]+/[Ii]ndividual\s+and\s+(\$[\d,]+)/[Ff]amily", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ff]or in.network\s+\$[\d,]+\s+individual\s*/\s*(\$[\d,]+)\s+family.{0,300}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,300}[Ff]or in.network\s+\$[\d,]+\s+individual\s*/\s*(\$[\d,]+)\s+family", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,200}\$[\d,]+\s+individual\s*/\s*(\$[\d,]+)\s+family", re.DOTALL | re.IGNORECASE),
        # AL Blue family: dollar follows "individual /"
        re.compile(r"[Ff]or network providers:\s*\$[\d,]+.{0,600}individual\s*/\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}[Ff]or network providers:\s*\$[\d,]+.{0,300}individual\s*/\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # AL Self-Only family
        re.compile(r"limit for this plan\?\s*\$[\d,]+\s+Self.Only\s*/\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # AZ BCBS family
        re.compile(r"[Ii]n.Network:\s+[Ii]ndividual\s+\$[\d,]+\s*/\s*[Ff]amily\s+(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        # "$X/Individual, $Y/Family" family value
        re.compile(r"\$[\d,]+/[Ii]ndividual,\s*(\$[\d,]+)/[Ff]amily.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}\$[\d,]+/[Ii]ndividual,\s*(\$[\d,]+)/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # "Per Person/$Y" family value
        re.compile(r"\$[\d,]+\s+[Pp]er\s+[Pp]erson\s*/\s*(\$[\d,]+).{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        # "per person | $Y per group" family
        re.compile(r"\$[\d,]+\s+per\s+person\s*\|\s*(\$[\d,]+).{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        # "$X/person or $Y/family" family
        re.compile(r"\$[\d,]+/[Pp]erson\s+or\s+(\$[\d,]+)/[Ff]amily.{0,400}out.of.pocket", re.DOTALL | re.IGNORECASE),
        re.compile(r"out.of.pocket.{0,400}\$[\d,]+/[Pp]erson\s+or\s+(\$[\d,]+)/[Ff]amily", re.DOTALL | re.IGNORECASE),
        # "pocket limit for this $X person/ $Y family" family value
        re.compile(r"pocket limit for this\s+\$[\d,]+\s+person/\s*(\$[\d,]+)\s+family", re.DOTALL | re.IGNORECASE),
        # "Network providers: $X [text] individual / $Y" family
        re.compile(r"[Nn]etwork providers:\s*\$[\d,]+.{0,600}individual\s*/\s*(\$[\d,]+)", re.DOTALL | re.IGNORECASE),
        re.compile(r"[Ii]n.network:\s*\$[\d,]+\s+[Ii]ndividual\s*/\s*(\$[\d,]+).{0,300}[Oo]ut.of.[Pp]ocket", re.DOTALL),
    ],
    # Copays: DOTALL with limited window so amount can be on a following line.
    # Generic drug also handles table-linearized format where copay appears before service name.
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
        re.compile(r"[Uu]rgent [Cc]are [Cc]enter.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        # Table-linearized: dollar amount appears before "Urgent Care" label
        re.compile(r"(\$[\d,]+|No [Cc]harge|\d+%)\s*/?\s*visit.{0,150}[Uu]rgent [Cc]are", re.DOTALL),
        re.compile(r"(\$[\d,]+|No [Cc]harge|\d+%).{0,80}[Uu]rgent [Cc]are", re.DOTALL),
    ],
    "copay_generic_drug": [
        re.compile(r"[Gg]eneric [Dd]rug.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        re.compile(r"[Pp]referred [Gg]eneric.{0,200}?(\$[\d,]+|No [Cc]harge|\d+%)", re.DOTALL),
        # Table-linearized format: copay value appears before service name
        re.compile(r"(\$[\d,]+)\s+copay.{0,80}[Gg]eneric\s+drug", re.DOTALL),
        # DE format: "$25 ... Generic drugs copayment/prescription"
        re.compile(r"(\$[\d,]+).{0,100}[Gg]eneric\s+drugs?\s+copay", re.DOTALL | re.IGNORECASE),
        # DE "Tier 1 No charge" in drug formulary table
        re.compile(r"[Ii]f you need drugs.{0,200}[Tt]ier 1\s+(No\s+[Cc]harge|\$[\d,]+)", re.DOTALL | re.IGNORECASE),
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
