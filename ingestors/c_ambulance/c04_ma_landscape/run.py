"""Medicare Advantage Landscape ingestor (Family C, C.4).

Source: CMS Medicare Advantage Landscape source files (annual release, October).
  canonical_url: see source_registry.yaml — verify and update each October.
  Landing page: cms.gov/medicare/health-drug-plans/medicareadvantage/enrollment/plan-information

Downloads the landscape ZIP, reads the medical (non-Part D) CSV, maps CMS column
names to the ma_plans schema, and bulk-inserts using transaction batching.

Run in October/November each year for the upcoming plan year's landscape data.
The plan_year column drives the plan_year IN (2025, 2026) acceptance gate.
"""
import csv
import re
import sqlite3
import zipfile
from io import TextIOWrapper
from pathlib import Path

from ingestors.core.pipeline import run_ingestor, now_utc

SOURCE_ID = "c04_ma_landscape"

# CMS column-name candidates across release years (lower-cased for matching)
_COL_MAP: dict[str, list[str]] = {
    "contract_id":       ["contract id", "contract_id", "h_number"],
    "plan_id":           ["plan id", "plan_id", "pbp"],
    "segment_id":        ["segment id", "segment_id", "segment"],
    "organization_name": ["organization name", "organization marketing name",
                          "org name", "issuer name"],
    "plan_name":         ["plan name", "plan marketing name", "marketing name"],
    "plan_type":         ["plan type", "plan_type", "type"],
    "state":             ["state", "state code"],
    "county_name":       ["county name", "county/parish name", "county"],
    "fips_code":         ["fips state county code", "fips", "fips_code", "county fips"],
    "snp_type":          ["snp type", "snp_type", "special needs plan type"],
    "premium_raw":       ["monthly consolidated premium", "premium", "part c premium",
                          "total monthly premium"],
    "star_rating":       ["overall star rating", "star rating", "overall rating"],
    "plan_year":         ["plan year", "plan_year", "contract year"],
}

_PLAN_TYPE_NORM = {
    "hmo": "HMO", "hmo-pos": "HMO", "local ppo": "PPO", "regional ppo": "PPO",
    "ppo": "PPO", "pffs": "PFFS", "msa": "MSA",
    "snp": "SNP", "hmo snp": "SNP", "ppo snp": "SNP",
}

_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "puerto rico": "PR",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}


def _resolve_col(header_lower: list[str], candidates: list[str]) -> int | None:
    for c in candidates:
        try:
            return header_lower.index(c)
        except ValueError:
            pass
    # Partial match fallback
    for i, h in enumerate(header_lower):
        for c in candidates:
            if c in h or h in c:
                return i
    return None


def _parse_premium(raw) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    if not s or s.lower() in ("n/a", "na", "-", ""):
        return None
    try:
        return round(float(s) * 100)
    except (ValueError, TypeError):
        return None


def _norm_state(raw: str) -> str:
    """Return 2-letter state code. Passes through if already 2 chars."""
    s = raw.strip()
    if len(s) == 2:
        return s.upper()
    return _STATE_ABBR.get(s.lower(), s.upper()[:2])


def _norm_plan_type(raw: str) -> str:
    s = raw.strip().lower()
    return _PLAN_TYPE_NORM.get(s, raw.strip().upper())


def _open_landscape_csv(local_path: Path) -> tuple[list[str], list[list]]:
    """Open ZIP and return (header, rows) for the medical landscape CSV."""
    with zipfile.ZipFile(local_path) as zf:
        names = zf.namelist()
        # Prefer files with "landscape" in the name; exclude Part D / dental files
        candidates = [
            n for n in names
            if n.lower().endswith(".csv")
            and "partd" not in n.lower()
            and "dental" not in n.lower()
            and not n.startswith("__")
        ]
        if not candidates:
            raise FileNotFoundError(f"No suitable CSV found in ZIP. Contents: {names}")

        # Pick the largest medical landscape CSV
        csv_name = max(candidates, key=lambda n: zf.getinfo(n).file_size)
        print(f"[{SOURCE_ID}] reading {csv_name} from ZIP")

        with zf.open(csv_name) as raw:
            reader = csv.reader(TextIOWrapper(raw, encoding="utf-8-sig", errors="replace"))
            header = next(reader)
            rows = list(reader)

    return header, rows


def ingest(source: dict, local_path: Path, file_hash: str, conn: sqlite3.Connection) -> int:
    header, rows = _open_landscape_csv(local_path)
    header_lower = [str(h or "").strip().lower() for h in header]

    cols = {field: _resolve_col(header_lower, cands) for field, cands in _COL_MAP.items()}

    missing = [f for f, i in cols.items() if i is None and f not in ("segment_id", "fips_code", "snp_type", "star_rating")]
    if missing:
        raise ValueError(f"[{SOURCE_ID}] Cannot resolve required columns: {missing}. Header: {header}")

    db_rows: list[tuple] = []
    skipped = 0

    for row in rows:
        def cell(field: str) -> str | None:
            i = cols.get(field)
            if i is None or i >= len(row):
                return None
            v = row[i]
            return str(v).strip() if v is not None else None

        contract_id = cell("contract_id") or ""
        plan_id = cell("plan_id") or ""
        if not contract_id or not plan_id:
            skipped += 1
            continue

        # Determine plan_year: from column if present, else infer from source filename
        plan_year_raw = cell("plan_year")
        if plan_year_raw and re.match(r"^\d{4}$", plan_year_raw.strip()):
            plan_year = int(plan_year_raw)
        else:
            # Infer from ZIP filename (e.g. "2025-landscape-source-file.zip")
            m = re.search(r"(20\d{2})", local_path.name)
            plan_year = int(m.group(1)) if m else 2025

        segment_id = cell("segment_id") or ""
        ma_plan_id = f"{contract_id}_{plan_id}_{segment_id}_{plan_year}"

        state_raw = cell("state") or ""
        if not state_raw:
            skipped += 1
            continue
        state = _norm_state(state_raw)

        county = cell("county_name") or ""
        plan_name = cell("plan_name") or ""
        org_name = cell("organization_name") or ""
        plan_type_raw = cell("plan_type") or ""

        db_rows.append((
            ma_plan_id,
            contract_id,
            plan_id,
            segment_id or None,
            plan_year,
            plan_name,
            org_name,
            _norm_plan_type(plan_type_raw),
            state,
            county,
            cell("fips_code"),
            cell("snp_type") or None,
            _parse_premium(cell("premium_raw")),
            cell("star_rating") or None,
            SOURCE_ID,
        ))

    if skipped:
        print(f"[{SOURCE_ID}] skipped {skipped} rows with missing contract_id / plan_id / state")

    if len(db_rows) < 10_000:
        raise ValueError(
            f"[{SOURCE_ID}] Only {len(db_rows)} rows parsed — expected >= 10,000. "
            f"Check ZIP contents and column mapping. Header: {header}"
        )

    # Bulk insert with transaction batching
    batch_size = 1000
    inserted = 0
    for i in range(0, len(db_rows), batch_size):
        batch = db_rows[i : i + batch_size]
        conn.executemany(
            """INSERT OR REPLACE INTO ma_plans
               (ma_plan_id, contract_id, plan_id, segment_id, plan_year,
                plan_name, organization_name, plan_type, state, county_name,
                fips_code, snp_type, premium_cents, star_rating, source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )
        conn.commit()
        inserted += len(batch)

    years = sorted({r[4] for r in db_rows})
    states = len({r[8] for r in db_rows})
    print(f"[{SOURCE_ID}] {inserted} rows loaded — plan_year(s): {years}, {states} states/territories")
    return inserted


def main():
    run_ingestor(SOURCE_ID, ingest)


if __name__ == "__main__":
    main()
