"""Print the relevant text excerpt from docs that still fail after new patterns."""
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pdfplumber
from ingestors.f_sbc.f03_parser.run import FIELD_PATTERNS

DB = Path("data/db/pilot.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

KEYWORDS = {
    "oop_max_individual_inn":    ["out-of-pocket", "out of pocket"],
    "deductible_individual_inn": ["deductible"],
    "copay_generic_drug":        ["generic", "tier 1", "drug"],
    "copay_urgent_care":         ["urgent"],
}

for field, keywords in KEYWORDS.items():
    rows = conn.execute(
        """SELECT sd.local_path FROM sbc_fields sf
           JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
           WHERE sf.field_name = ? AND sf.field_value IS NULL
           AND sd.local_path IS NOT NULL LIMIT 40""",
        (field,),
    ).fetchall()

    still_failing = []
    for row in rows:
        p = Path(row["local_path"])
        if not p.exists():
            continue
        with pdfplumber.open(p) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        hit = any(pat.search(text) for pat in FIELD_PATTERNS[field])
        if not hit:
            still_failing.append((p, text))
        if len(still_failing) >= 5:
            break

    print(f"\n{'='*60}")
    print(f"STILL FAILING: {field}  ({len(still_failing)} sampled)")
    print('='*60)
    for p, text in still_failing[:3]:
        for kw in keywords:
            idx = text.lower().find(kw)
            if idx >= 0:
                print(f"\n  {p.name}:")
                print(repr(text[max(0, idx-80):idx+250]))
                break
        else:
            print(f"\n  {p.name}: keyword not found in text at all")
