"""Re-extract deductible/oop_max fields for docs where they are currently NULL.

Uses the updated FIELD_PATTERNS from f03_parser. Does not touch docs with _error
or fields that already have a value. Updates existing sbc_fields rows in-place.
"""
import sqlite3
import sys
from pathlib import Path

import pdfplumber

sys.stdout.reconfigure(encoding="utf-8")

# Import the updated patterns and helpers from f03_parser
sys.path.insert(0, str(Path(__file__).parent))
from ingestors.f_sbc.f03_parser.run import (
    FIELD_PATTERNS, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
    _extract_text, _normalize_text, _has_sbc_markers,
)

TARGET_FIELDS = [
    "deductible_individual_inn",
    "deductible_family_inn",
    "oop_max_individual_inn",
    "oop_max_family_inn",
]


def _find_field(text: str, field_name: str):
    patterns = FIELD_PATTERNS.get(field_name, [])
    for i, pat in enumerate(patterns):
        m = pat.search(text)
        if m:
            value = m.group(1).strip()
            confidence = CONFIDENCE_HIGH if i == 0 else CONFIDENCE_MEDIUM
            return value, confidence
    return None, 0.0


def _get_text(path: Path) -> str:
    text, _ = _extract_text(path)
    if not _has_sbc_markers(text):
        normalized = _normalize_text(text)
        if _has_sbc_markers(normalized, min_count=2):
            return normalized
    return text


conn = sqlite3.connect("data/db/pilot.db")
conn.row_factory = sqlite3.Row

# Get all null-valued fields for target field names (excluding error docs)
null_rows = conn.execute("""
    SELECT sf.id as field_id, sf.field_name, sd.id as doc_id, sd.local_path, sd.plan_id
    FROM sbc_fields sf
    JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
    WHERE sf.field_name IN ('deductible_individual_inn', 'deductible_family_inn',
                            'oop_max_individual_inn', 'oop_max_family_inn')
      AND sf.field_value IS NULL
      AND sf.confidence = 0
      AND sd.id NOT IN (SELECT DISTINCT sbc_document_id FROM sbc_fields WHERE field_name='_error')
    ORDER BY sd.id
""").fetchall()

print(f"Null fields to re-extract: {len(null_rows)}")

# Group by doc_id to avoid re-opening same PDF multiple times
from collections import defaultdict
by_doc = defaultdict(list)
for r in null_rows:
    by_doc[r["doc_id"]].append(r)

fixed = 0
unchanged = 0
errors = 0

for doc_id, fields in by_doc.items():
    local_path = fields[0]["local_path"]
    plan_id = fields[0]["plan_id"]
    try:
        text = _get_text(Path(local_path))
    except Exception as e:
        print(f"  ERROR {plan_id}: {e}")
        errors += 1
        continue

    for row in fields:
        field_name = row["field_name"]
        value, confidence = _find_field(text, field_name)
        if value is not None:
            conn.execute(
                "UPDATE sbc_fields SET field_value=?, confidence=? WHERE id=?",
                (value, confidence, row["field_id"]),
            )
            fixed += 1
        else:
            unchanged += 1

conn.commit()
print(f"Fixed: {fixed}  Unchanged: {unchanged}  Errors: {errors}")

# Summary by field name
print("\nFixed counts by field:")
for field in TARGET_FIELDS:
    count = conn.execute("""
        SELECT COUNT(*) FROM sbc_fields sf
        JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
        WHERE sf.field_name=? AND sf.field_value IS NOT NULL
          AND sd.id NOT IN (SELECT DISTINCT sbc_document_id FROM sbc_fields WHERE field_name='_error')
    """, (field,)).fetchone()[0]
    total = conn.execute("""
        SELECT COUNT(*) FROM sbc_fields sf
        JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
        WHERE sf.field_name=?
          AND sd.id NOT IN (SELECT DISTINCT sbc_document_id FROM sbc_fields WHERE field_name='_error')
    """, (field,)).fetchone()[0]
    print(f"  {field:<35}  {count}/{total}  ({count/total*100:.1f}%)")

conn.close()
