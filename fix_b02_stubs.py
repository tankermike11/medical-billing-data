import sqlite3
conn = sqlite3.connect("data/db/pilot.db")
cur = conn.execute("DELETE FROM plans WHERE source_id='b02_plan_attributes_puf'")
conn.commit()
print(f"Deleted {cur.rowcount} stub plan records from b02")
