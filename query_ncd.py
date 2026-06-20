import sqlite3
conn = sqlite3.connect("data/db/pilot.db")
conn.row_factory = sqlite3.Row

# Error docs
print("=== Documents with _error ===\n")
errors = conn.execute("""
    SELECT sd.plan_id, sf.field_value, sf.confidence
    FROM sbc_fields sf
    JOIN sbc_documents sd ON sd.id = sf.sbc_document_id
    WHERE sf.field_name = '_error'
    ORDER BY sf.field_value
""").fetchall()
from collections import Counter
error_types = Counter(r["field_value"] for r in errors)
for msg, cnt in error_types.most_common():
    print(f"  {cnt:>4}  {msg}")
print(f"\n  Total error docs: {len(errors)}")

# Low-confidence fields (confidence < 0.5, value is not null)
print("\n=== Low-confidence fields (confidence < 0.5) ===\n")
low_conf = conn.execute("""
    SELECT sf.field_name, COUNT(*) as cnt,
           AVG(sf.confidence) as avg_conf
    FROM sbc_fields sf
    WHERE sf.confidence < 0.5
      AND sf.field_value IS NOT NULL
      AND sf.field_name != '_error'
    GROUP BY sf.field_name
    ORDER BY cnt DESC
""").fetchall()
for r in low_conf:
    print(f"  {r['cnt']:>5}  {r['field_name']:<35} avg_conf={r['avg_conf']:.2f}")

total_low = conn.execute("""
    SELECT COUNT(*) FROM sbc_fields
    WHERE confidence < 0.5 AND field_value IS NOT NULL AND field_name != '_error'
""").fetchone()[0]
print(f"\n  Total low-confidence field instances: {total_low}")

# Fields that extracted nothing (null value, confidence=0)
print("\n=== Fields with no extraction (value=NULL, confidence=0) by field ===\n")
null_fields = conn.execute("""
    SELECT sf.field_name, COUNT(*) as cnt
    FROM sbc_fields sf
    WHERE sf.field_value IS NULL
      AND sf.confidence = 0
      AND sf.field_name != '_page_count'
    GROUP BY sf.field_name
    ORDER BY cnt DESC
""").fetchall()
for r in null_fields:
    total_docs = conn.execute("SELECT COUNT(*) FROM sbc_documents WHERE plan_year=2025").fetchone()[0]
    print(f"  {r['cnt']:>5}/{total_docs}  {r['field_name']}")

conn.close()
