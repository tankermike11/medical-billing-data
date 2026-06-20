"""Quick pattern coverage test — run before re-parsing SBCs."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdfplumber
from ingestors.f_sbc.f03_parser.run import FIELD_PATTERNS

DB = Path("data/db/pilot.db")
FIELDS = ["oop_max_individual_inn", "deductible_individual_inn", "copay_generic_drug", "copay_urgent_care"]

conn = sqlite3.connect(DB)

for field in FIELDS:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT sd.local_path FROM sbc_fields sf
           JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
           WHERE sf.field_name = ? AND sf.field_value IS NULL
           AND sd.local_path IS NOT NULL LIMIT 30""",
        (field,),
    ).fetchall()

    matched = 0
    tried = 0
    for row in rows:
        p = Path(row["local_path"])
        if not p.exists():
            continue
        with pdfplumber.open(p) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        tried += 1
        for i, pat in enumerate(FIELD_PATTERNS[field]):
            m = pat.search(text)
            if m:
                matched += 1
                break

    pct = 100 * matched / tried if tried else 0
    print(f"{field:<30} {matched:>3}/{tried:<3} now matching  ({pct:.0f}%)")
