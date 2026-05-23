# MASA Public Data Ingestion Layer

## Data Completion Addendum v1.3

**Three data-prep tasks required by the SAM Medical Bill Advocate prototype**

Prepared for: Claude Code build — repository `medical_billing_data` (existing)
Status: Build-ready
Companion documents: MASA Public Data Ingestion Layer PRD v1.1 (the pipeline this extends); MASA SAM Medical Bill Advocate Prototype PRD v1.2 (the application that consumes the result).

**Revision history**
- **v1.1 → v1.2** — Initial addendum: `sources`, `ambulance_fee_schedule`, `nsa_rules`.
- **v1.2 → v1.3** — Review fixes: ambulance geography committed to **state-level minimum** (national-only is insufficient); the `sources` backfill scan now includes the two new tables; added a hash-skip 0-row warning for the ambulance ingestor; specified a `Meta` sheet for workbook-level ruleset provenance; the `nsa_rules` acceptance criterion is now "row count matches the workbook" rather than a fixed 59.

---

## 1. Purpose and context

The SAM Medical Bill Advocate prototype is built in a separate repository (`masa-sam-advocate`) and consumes `pilot.db` **read-only**. Before that build can begin, three tables must be added to `pilot.db`. All three are **ingestion work** — they write to `pilot.db` — so they belong here, in the data pipeline, not in the application repository. This keeps `pilot.db` single-writer: the data pipeline owns it, the application only reads it.

This addendum extends the v1.1 pipeline. It is a focused, additive piece of work — it does not modify existing ingestors or the existing schema, and it does not address the other known gaps (`code_relationships`, the hash-skip bug, monetary-string normalization). It is the partial build of two families the v1.1 pilot deferred — Family C (ambulance) and Family E (regulatory rules) — limited to exactly the slices the advocacy prototype needs, plus the `sources` table that the v1.1 architecture review flagged as a real fix.

The three tasks:

1. **`sources` table** — give every row's `source_id` a queryable home, so publisher and license are resolvable from the DB rather than only from `source_registry.yaml`.
2. **`ambulance_fee_schedule` table** (Family C) — Medicare ground-ambulance reference rates at state-level geography. A new ingestor in the standard `run_ingestor()` pattern.
3. **`nsa_rules` table** (Family E) — the counsel-reviewed NSA / GFE-PPDR / ground-ambulance ruleset (Tables A–K), loaded from a manually-placed workbook in the `a07_revenue` pattern.

---

## 2. Conventions to follow

Reuse the existing pipeline's conventions exactly — this work should look like it was always part of the codebase:

- **Schema** — add new tables to the `SCHEMA` string in `core/db.py` as `CREATE TABLE IF NOT EXISTS`, applied by the existing `executescript(SCHEMA)` on every `get_conn()`. No `ALTER TABLE` migration is needed for new tables.
- **Ingestors** — new source modules under `ingestors/<family>/<source_id>/run.py`, each exposing an `ingest()` callback. Register each in `source_registry.yaml` and in the CLI `INGESTOR_MAP`.
- **`run_ingestor()`** — the ambulance ingestor uses the standard orchestrator (download → hash-skip → `ingest()` → `record_release()`). The `nsa_rules` ingestor follows the `a07_revenue` / `a05_carc` pattern: no file to download, so it bypasses `run_ingestor()` and calls `record_release()` / `record_error()` directly.
- **Upsert** — `INSERT OR REPLACE` against `UNIQUE` / primary-key constraints, so every loader is re-runnable without corrupting data.
- **Tracking** — every load records a `dataset_releases` row; failures record `extraction_errors`.
- **Logging** — plain `print()` prefixed `[source_id]`; summary stats into `dataset_releases.notes`.
- **Booleans** — `INTEGER` 0/1.

**One deliberate exception to convention.** The pipeline stores monetary values as raw strings ("$1,500"). `ambulance_fee_schedule.reference_rate` is the exception: it is stored as an **integer (cents)**, because the advocacy app computes a billed-vs-reference gap against it and a raw string would push parsing into every caller. This exception is intentional and limited to this one column.

---

## 3. Task 1 — `sources` table

### 3.1 Rationale

`pilot.db` has no `sources` table. Every data row carries a freetext `source_id` (e.g. `a01_icd10cm`) and an inline `source_url`, but publisher and license live only in `source_registry.yaml` and are not queryable from the DB. The advocacy app's `resolve_source()` needs publisher-grade attribution for every cited fact. A `sources` table seeded from the registry closes this.

### 3.2 Schema

Add to `core/db.py` `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS sources (
  source_id        TEXT PRIMARY KEY,
  publisher        TEXT NOT NULL,
  canonical_url    TEXT,
  license          TEXT,
  refresh_cadence  TEXT,
  notes            TEXT
);
```

`source_id` is the join key used by `resolve_source()`. It is not declared as a formal foreign key on the existing tables (SQLite would require rebuilds and the existing `source_id` columns stay as-is), but it functions as one.

### 3.3 Build

A one-time, idempotent backfill script — `tools/backfill_sources.py`, invoked manually (consistent with the existing root-level utility scripts such as `fix_release.py`):

1. Parse `source_registry.yaml` via the existing `load_registry()`.
2. For each registry entry, `INSERT OR REPLACE` a `sources` row, mapping registry fields → `publisher`, `canonical_url`, `license`, `refresh_cadence`.
3. Scan `SELECT DISTINCT source_id` across **all data tables that carry one**: `codes`, `plans`, `plan_attributes`, `plan_benefits`, `plan_materials`, `sbc_documents`, and the two new tables `ambulance_fee_schedule` and `nsa_rules`. Any `source_id` present in the data but absent from `sources` is printed as a warning so it can be added to the registry.

Run this **after** Tasks 2 and 3 have added their `source_registry.yaml` entries and populated their tables, so the new `c01` and `e01` sources are both registered and detected by the scan. The script is idempotent — re-running it is safe.

### 3.4 Acceptance

Every distinct `source_id` appearing in any data table — including `ambulance_fee_schedule` and `nsa_rules` — resolves to exactly one `sources` row with a non-null `publisher`.

---

## 4. Task 2 — `ambulance_fee_schedule` table (Family C)

### 4.1 Rationale

The advocacy prototype's ground-ambulance node needs a Medicare **reference rate** as a negotiation anchor — ground ambulance is not protected by the No Surprises Act, so the reference rate is the member's negotiation position, not a legal entitlement. This is the first build of Family C, limited to the ground-ambulance slice.

### 4.2 Scope — reference rate, state-level geography

P0 builds a **reference rate** at **state-level geography** at minimum. The advocacy app's member-facing copy refers to the rate "in your state," so the data must support a per-state figure — **a national-only rate is not sufficient.** Finer `urban_rural` tiers may be added if the source file supports them cleanly; ZIP-to-locality precision is explicitly P1 and out of scope here. This is a reference rate for negotiation, not a penny-exact Medicare adjudication.

In-scope HCPCS: the ground-ambulance transport and mileage codes (the `A0425`–`A0434` range — BLS/ALS transport levels and ground mileage). These HCPCS already exist in the `codes` table; the ingestor may cross-reference `codes` (where `code_type='HCPCS'`) to validate the set.

### 4.3 Schema

Add to `core/db.py` `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS ambulance_fee_schedule (
  hcpcs           TEXT NOT NULL,
  geo_level       TEXT NOT NULL,      -- 'state' (required minimum) | 'urban_rural'
  geo_key         TEXT NOT NULL,      -- e.g. 'FL', 'TX'  (or 'urban'/'rural')
  reference_rate  INTEGER NOT NULL,   -- integer cents (deliberate exception, see Section 2)
  effective_year  INTEGER NOT NULL,
  source_id       TEXT NOT NULL,
  PRIMARY KEY (hcpcs, geo_level, geo_key, effective_year)
);
```

Every in-scope HCPCS must have at least one `geo_level='state'` row per state.

### 4.4 Ingestor

New module `ingestors/c_ambulance/c01_ambulance_fee_schedule/run.py`, using the standard `run_ingestor()` pattern (download → hash-skip → `ingest()` → `record_release()`).

- **Source** — the CMS Medicare Ambulance Fee Schedule public use file. Add a `c01_ambulance_fee_schedule` entry to `source_registry.yaml` with `publisher: CMS`, the canonical CMS Ambulance Fee Schedule URL, `license: Public domain (U.S. Government work)`, `refresh_cadence: Annual`.
- **Parse** — the CMS file is organized by HCPCS and locality/carrier. The simplest robust approach for a state-level reference rate is to **aggregate the published locality amounts up to the state level** (e.g. the median amount per HCPCS per state) rather than reconstructing the Medicare payment formula (base rate × RVU × geographic adjustment). Reconstructing the formula is error-prone and unnecessary for a negotiation anchor. The exact column mapping is confirmed against the actual downloaded file — this is normal per-source parsing work, consistent with every existing ingestor.
- **Load** — convert amounts to integer cents; `INSERT OR REPLACE` one row per `(hcpcs, geo_level, geo_key, effective_year)`; return `row_count`.
- **CLI** — add `c_ambulance` as a `run-all` family and `c01_ambulance_fee_schedule` to `INGESTOR_MAP`.

**Hash-skip caution.** The pipeline has a known gotcha: a run that records 0 rows as a *success* writes a `dataset_releases` row that blocks all future parses (the hash matches, so `run_ingestor()` exits early) until that release record or the raw file is deleted. The `c01` ingestor must guard against this — if parsing yields 0 rows, it must `record_error()` (a failure), not return a 0-row success, so a bad parse can be re-run without manual DB surgery.

### 4.5 Acceptance

Every in-scope ground-ambulance HCPCS has a `reference_rate` at `geo_level='state'` for every state; all rates are positive integers (cents); `source_id` resolves to a `sources` row. A 0-row parse is recorded as an error, not a success.

---

## 5. Task 3 — `nsa_rules` table (Family E)

### 5.1 Rationale

The advocacy prototype's rule engine evaluates the No Surprises Act / GFE-PPDR / ground-ambulance ruleset deterministically. The ruleset is authored and counsel-reviewed as a workbook (Tables A–K); this task loads it into a queryable table. This is the first build of Family E, limited to the federal ruleset slice. The workbook is the spec; the application's predicate functions are the implementation.

### 5.2 Input — the reviewed workbook

A manually-placed file, in the `a07_revenue` pattern (no download): the counsel-reviewed ruleset workbook at `data/raw/e01_nsa_rules/nsa_ruleset.xlsx`. The workbook contains eleven rule sheets (`Table 1`–`Table 10` and `Table K`) plus an `Index` sheet mapping each table to a category letter (A–K). As of this writing the workbook holds 59 rules, but the count will change with counsel review — the loader and its acceptance criterion must not assume a fixed number.

Before load, the workbook must be extended (during counsel review) beyond its current five columns (`Rule ID`, `Summary`, `Prototype logic`, `System action`, `Citation`):

- **Per-rule columns** added to each table sheet: `deadline_days`, `deadline_basis` (nullable, counsel-populated — deadlines are structured, human-reviewed fields, never derived); `qpa_dependent` (0/1, flags rules dependent on the Qualifying Payment Amount methodology); `status` (`draft` or `counsel_approved`, which may legitimately vary rule by rule during review).
- **Workbook-level provenance** placed on a dedicated **`Meta` sheet** as key-value rows: `ruleset_version`, `effective_date`, `last_reviewed`, `reviewed_by`. These are constants for the whole ruleset; the `Meta` sheet keeps them in one place rather than repeated on every rule row. The loader reads the `Meta` sheet once and applies these values to every `nsa_rules` row.

### 5.3 Schema

Add to `core/db.py` `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS nsa_rules (
  rule_id          TEXT PRIMARY KEY,   -- e.g. 'NSA-EMERG-001', 'GROUND-003'
  category         TEXT NOT NULL,      -- A–K
  summary          TEXT,
  prototype_logic  TEXT,               -- human-readable spec for the predicate
  system_action    TEXT,
  citation         TEXT,
  deadline_days    INTEGER,            -- nullable
  deadline_basis   TEXT,               -- nullable
  qpa_dependent    INTEGER NOT NULL DEFAULT 0,
  ruleset_version  TEXT,               -- from Meta sheet
  effective_date   TEXT,               -- from Meta sheet
  last_reviewed    TEXT,               -- from Meta sheet
  reviewed_by      TEXT,               -- from Meta sheet
  status           TEXT NOT NULL,      -- 'draft' | 'counsel_approved'
  source_id        TEXT NOT NULL
);
```

### 5.4 Ingestor

New module `ingestors/e_rules/e01_nsa_rules/run.py`. Following the `a07_revenue` pattern, it has no file to download and so bypasses `run_ingestor()`, calling `record_release()` on success or `record_error()` on exception directly.

- **Read** — open `data/raw/e01_nsa_rules/nsa_ruleset.xlsx` with `openpyxl` (read-only). Read the `Meta` sheet for the four workbook-level provenance constants. Read the `Index` sheet to build the `Table N → category letter` map. Iterate the eleven rule sheets; for each rule row, take `category` from the Index map, the per-rule columns from the sheet, and the provenance constants from `Meta`.
- **Load** — `INSERT OR REPLACE` keyed on `rule_id`; set `source_id = 'e01_nsa_rules'` on every row; return `row_count`.
- **Registry** — add an `e01_nsa_rules` entry to `source_registry.yaml`: `publisher: MASA (compiled from 45 CFR Part 149 and related authorities)`, `canonical_url`: the eCFR Part 149 landing page, `license: Internal — counsel-reviewed compilation`, `refresh_cadence: On regulatory change or counsel review`.
- **CLI** — add `e_rules` as a `run-all` family and `e01_nsa_rules` to `INGESTOR_MAP`.
- **Faithful load** — the ingestor loads `status` exactly as the workbook states it; it does not approve rules. Enforcement of `status` (degrading non-approved rules to "needs human review") is the application's job.
- **0-row guard** — as with `c01`, if the workbook yields 0 rules the ingestor records an error, not a success.

### 5.5 Acceptance

Every rule row present in the workbook's eleven table sheets loads into `nsa_rules` — the loaded row count equals the workbook row count (no fixed expected number; the workbook is the source of truth). Every row has a `category` in A–K, a `rule_id`, and a `status`; the four `Meta`-sheet provenance fields are populated on every row; `source_id` resolves to a `sources` row. Re-running the ingestor against an updated workbook replaces rows cleanly via `rule_id`.

---

## 6. Build sequence

1. Add all three `CREATE TABLE IF NOT EXISTS` blocks to `core/db.py` `SCHEMA`.
2. Add `source_registry.yaml` entries for `c01_ambulance_fee_schedule` and `e01_nsa_rules`.
3. Build and run the `c01` ambulance ingestor.
4. Place the reviewed workbook (with the `Meta` sheet and the added per-rule columns); build and run the `e01` ruleset ingestor.
5. Run `tools/backfill_sources.py` last, so it captures the new `c01` and `e01` sources alongside the existing A/B/F sources.
6. Verify all acceptance criteria.

The output is an updated `pilot.db`. A snapshot of it is then copied into the `masa-sam-advocate` repository as the build input for the advocacy prototype (PRD v1.2, Phase 0).

---

## 7. Out of scope

- The other known pipeline gaps — `code_relationships` population, a general fix for the hash-skip bug (the two new ingestors guard against it locally, but the systemic fix is not in scope), monetary-string normalization in existing tables.
- The remainder of Families C, D, and E beyond the slices above (NCD 10.1 medical-necessity criteria, Medicaid data, state-specific surprise-billing rules, payer policies).
- ZIP-to-locality ambulance precision (P1).
- Any change to existing ingestors or existing table schemas.

---

*End of Addendum v1.3.*
