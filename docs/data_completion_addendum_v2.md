# MASA Public Data Ingestion Layer — Data Completion Addendum v2.0

**Repository:** `medical_billing_data`
**Companion documents:** MASA Public Data Ingestion Layer PRD v1.1; SAM Prototype PRD v1.2
**Status:** Build-ready
**Date:** June 2026

---

## Purpose

This addendum specifies five new ingestor modules to add to the `medical_billing_data` pipeline.
All five write to `pilot.db` and must pass their acceptance gates before the SAM prototype PRD
v2.0 build begins. They follow the same ingestor contract as the 22 existing modules.

**Scope boundary:** This addendum covers data pipeline work only. The NSA rule counsel review
pass — marking `nsa_rules` rows `counsel_approved` and populating `deadline_days` /
`deadline_basis` — is a parallel legal-review task tracked separately. Do not block this sprint
on it.

---

## Pipeline conventions

These conventions apply to all modules in this addendum. They match the patterns used by the
22 existing ingestors — do not deviate without explicit reason.

### Source registry — `source_registry.yaml` (not a DB table)

There is no `sources` table in `pilot.db` and no `register_source()` function. The pipeline
uses `source_registry.yaml` as the source registry. For each new module, add an entry to that
file before writing any ingestor code. This is a one-time setup step per source.

```yaml
# Example entry to add to source_registry.yaml
- source_id: e02_ncd_ambulance
  name: "Medicare NCD 10.1 — Ambulance Services"
  url: "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34"
  cadence: quarterly_health_check
  license: public_domain
  phi_status: non_phi
  owner: data_team
```

The `source_id` values in the schemas below (e.g. `e02_ncd_ambulance`) are FKs that reference
this YAML registry. They are not `dataset_releases` IDs.

### `record_release()` — actual signature

The actual signature in `core/pipeline.py` is:

```python
record_release(conn, source_id, source_url, file_hash, row_count, status="success", notes="")
```

**For modules that fetch a file** (E.2 NCD 10.1, C.4 MA Landscape, C.6 NCCI edits), pass the
download URL as `source_url` and the SHA-256 hash of the downloaded file as `file_hash`.

**For hand-authored modules with no downloaded file** (E.3 Medicare appeals, E.4 ACA appeals),
use the authoritative CFR/CMS URL as `source_url` and the sentinel string `"manual"` as
`file_hash`:

```python
# E.3 and E.4 — hand-authored fixture, no downloaded file
record_release(
    conn=conn,
    source_id="e03_medicare_appeals",
    source_url="https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-405",
    file_hash="manual",
    row_count=6,
    status="success",
    notes="Hand-authored from 42 CFR §405.900-1140 and CMS Pub. 100-04 Ch. 29",
)
```

**On error**, call `record_release()` with `status="error"` and put the error message in
`notes`. Never call `record_release()` before `publish.py` completes successfully.

---

## Current `pilot.db` state

| Table | Rows | Status |
|---|---|---|
| `codes` | 301,842 | Complete — ICD-10, HCPCS, CARC, RARC, modifiers, revenue, POS, MS-DRG, NDC |
| `plans` | 5,486 | FFE Marketplace + CA. Missing 12 SBE states. MA Landscape not yet ingested. |
| `plan_benefits` | 1,645,743 | Complete for ingested plans |
| `plan_attributes` | 112,265 | Complete for ingested plans |
| `sbc_fields` | 22,886 | Copays 93–99%. Deductible/OOP 59–69%. Cap at 2,384 SBCs of 10,677 available. |
| `ambulance_fee_schedule` | 520 | 10 HCPCS × 52 states — complete |
| `nsa_rules` | 59 | All rules loaded. `status = draft`. Counsel review pending (separate track). |
| `ncd_ambulance` | — | **NOT YET INGESTED — blocks Flow 3** |
| `medicare_appeal_levels` | — | **NOT YET INGESTED — blocks Flow 3** |
| `commercial_appeal_levels` | — | **NOT YET INGESTED — blocks Flow 3** |
| `ncci_ptp_edits` | — | **NOT YET INGESTED — needed for Flow 2** |
| `ncci_mue` | — | **NOT YET INGESTED — needed for Flow 2** |
| `ma_plans` | — | **NOT YET INGESTED — needed for Flows 1 + 3** |

---

## Modules summary

| # | Module | ID | `source_id` | Blocks | Priority |
|---|---|---|---|---|---|
| 1 | Medicare NCD 10.1 | `E.2` | `e02_ncd_ambulance` | Flows 2 + 3 | **P0** |
| 2 | Medicare 5-level appeals framework | `E.3` | `e03_medicare_appeals` | Flow 3 | **P0** |
| 3 | Federal ACA / commercial appeals framework | `E.4` | `e04_aca_appeals` | Flow 3 | **P0** |
| 4 | Medicare Advantage Landscape | `C.4` | `c04_ma_landscape` | Flows 1 + 3 | P1 |
| 5 | NCCI edits — full table (PTP + MUE) | `C.6` | `c06_ncci_ptp` / `c06_ncci_mue` | Flow 2 | P1 |

**E-family source_id registry after this sprint:**

| `source_id` | Module | Status |
|---|---|---|
| `e01_nsa_rules` | NSA rules | Existing — do not modify |
| `e02_ncd_ambulance` | NCD 10.1 ambulance | This sprint |
| `e03_medicare_appeals` | Medicare 5-level appeals | This sprint |
| `e04_aca_appeals` | ACA / commercial appeals | This sprint |
| `e05_*` — `e09_*` | State surprise billing laws, GFE/PPDR, etc. | Reserved — P1/P2 scope |

**C-family source_id registry after this sprint:**

| `source_id` | Module | Status |
|---|---|---|
| `c01_mcd_ambulance` | Medicare Coverage Database — ambulance scope | Existing |
| `c02_ambulance_fee_schedule` | Medicare Ambulance Fee Schedule | Existing |
| `c03_*` | Medicare manuals — ambulance chapters | Existing or future |
| `c04_ma_landscape` | Medicare Advantage Landscape | This sprint |
| `c05_*` | Reserved — TBD | Do not assign this sprint |
| `c06_ncci_ptp` | NCCI PTP edits | This sprint |
| `c06_ncci_mue` | NCCI MUE table | This sprint |

> **Note on `c05_*`:** This slot is intentionally reserved. Do not assign any `c05_` source IDs
> during this sprint.

> **Note on existing C-family IDs:** The entries above for `c01`–`c03` reflect the expected
> naming convention based on the original PRD phasing. Verify against the actual entries in
> `source_registry.yaml` before writing new modules — correct any discrepancies there rather
> than in this document.

Build P0 modules first. P1 modules may run concurrently with the SAM prototype build once P0
gates pass.

---

## Build order

Modules are independent of each other. Build in this order:

1. **E.2 — NCD 10.1** (`e02_ncd_ambulance`) — start first; LLM extraction + human review is the longest step
2. **E.3 — Medicare appeals** (`e03_medicare_appeals`) — ~6 records; draft and send to counsel while E.2 extraction runs
3. **E.4 — ACA/commercial appeals** (`e04_aca_appeals`) — ~8 records; draft in parallel with E.3
4. **C.4 — MA Landscape** — small CSV ingest, no human review gate; run any time after infra check
5. **C.6 — NCCI edits** — largest volume; run last to avoid I/O contention

---

## Module E.2 — Medicare NCD 10.1 (Ambulance Services)

### Rationale

NCD 10.1 is the CMS National Coverage Determination for Ambulance Services. It defines the
medical necessity criteria that are the legal foundation of every Medicare and Medicare Advantage
ambulance denial appeal. The appeal letter must cite specific NCD text ("the patient's condition
precluded transport by any other means," "nearest appropriate facility") — this cannot come from
LLM inference. Structured source text enables "per CMS NCD 10.1, Section 10.1.1" citations.

### Source

- **Primary:** CMS Coverage Database
  `https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=34`
- **API:** CMS MCD API `https://api.cms.gov/medicare-coverage-database/`
- **Format:** Structured HTML / JSON via API
- **Update cadence:** NCD 10.1 is stable; quarterly URL health-check sufficient
- **License:** Public domain

### Schema — table `ncd_ambulance`

```sql
CREATE TABLE IF NOT EXISTS ncd_ambulance (
  ncd_id           TEXT NOT NULL,          -- "10.1"
  section_id       TEXT NOT NULL,          -- e.g. "10.1.1", "10.1.2"
  section_title    TEXT NOT NULL,
  coverage_indicator TEXT,                 -- "covered"|"non_covered"|"conditional"|"informational"
  criteria_text    TEXT NOT NULL,          -- verbatim or minimally-structured criterion text
  criterion_type   TEXT,                   -- "medical_necessity"|"transport_condition"|
                                           -- "facility_standard"|"exclusion"|"definition"
  effective_date   TEXT NOT NULL,          -- ISO 8601
  citation         TEXT NOT NULL,          -- full CMS citation string for user-facing documents
  reviewed         INTEGER NOT NULL DEFAULT 0, -- 0=draft, 1=human-reviewed and approved
  source_id        TEXT NOT NULL,          -- "e02_ncd_ambulance"
  PRIMARY KEY (ncd_id, section_id)
);
```

### Key criteria to extract as discrete rows

Each of the following must be a separate row — not buried in prose:

| `criterion_type` | Description |
|---|---|
| `medical_necessity` | Emergency: patient's condition required immediate transport; other means would endanger health |
| `medical_necessity` | Non-emergency: patient's condition precluded use of other transportation |
| `facility_standard` | Nearest appropriate facility: transport to nearest facility able to treat the condition |
| `transport_condition` | Loaded vs. unloaded mileage — coverage criteria for A0425 |
| `transport_condition` | BLS vs. ALS level — conditions requiring ALS1, ALS2, ALS Emergency vs. BLS |
| `transport_condition` | Origin/destination modifiers D, E, G, H, I, J, N, P, R, S, X and coverage implications |
| `exclusion` | Specifically non-covered transport scenarios |

### Ingestor contract

**Module path:** `ingestors/e_rules/e02_ncd_ambulance/`

```
fetch.py      HTTP GET to CMS MCD API for NCD 10.1.
              Store raw response at data/raw/e02_ncd_ambulance/ with SHA-256 hash.

parse.py      Extract section hierarchy, criterion text, coverage_indicator.
              Use LLM-assisted extraction for prose → structured rows.
              Output goes to staging table only (reviewed=0).

normalize.py  Assign criterion_type enum. Strip HTML. Normalize citation string.

validate.py   Assert row count >= 7 (one row per criterion type in the key criteria table).
              Assert every row has criteria_text (len > 20) and citation.
              Flag rows with criteria_text len < 20 for manual review.

publish.py    INSERT OR REPLACE into ncd_ambulance.
              Call record_release(conn, "e02_ncd_ambulance", source_url, file_hash, row_count)
              where source_url is the CMS MCD API URL and file_hash is the SHA-256 of the
              raw response stored in data/raw/e02_ncd_ambulance/.
```

> **Human review gate — BLOCKING:** All rows produced by LLM-assisted parsing must be
> human-reviewed before `reviewed` is set to `1`. The application serves only `reviewed=1` rows.
> Do not set `reviewed=1` programmatically — it must be a manual step after content verification.

### Acceptance gate

- [ ] Table `ncd_ambulance` exists and is non-empty
- [ ] All 7 key criteria present (one row per `criterion_type` group in the table above)
- [ ] At least 6 rows have `reviewed=1`
- [ ] `citation` non-null on all rows
- [ ] Entry present in `source_registry.yaml` for `e02_ncd_ambulance`

---

## Module E.3 — Medicare 5-level appeals framework

### Rationale

Medicare has a defined five-level appeals process for denied claims. Each level has a specific
name, deadline, decision-maker, and filing path. The SAM appeal letter must cite the correct
level and deadline. **Deadlines must never be LLM-derived** (PRD §13 constraint) — they must
come from structured, human-reviewed records.

### Source

- **CMS Medicare Appeals:** `https://www.cms.gov/medicare/appeals-and-grievances/medpresent`
- **eCFR:** 42 CFR §405.900–§405.1140 (Part B redetermination/reconsideration); 42 CFR §423.1966 (ALJ)
- **CMS Pub. 100-04 Ch. 29:** Medicare Claims Processing Manual — Medicare Appeals
- **Update cadence:** Annual review; flag immediately on any CMS Medicare Appeals notice
- **License:** Public domain

### Schema — table `medicare_appeal_levels`

```sql
CREATE TABLE IF NOT EXISTS medicare_appeal_levels (
  level_id                  TEXT PRIMARY KEY,  -- "medicare_L1" through "medicare_L5"
  level_number              INTEGER NOT NULL,  -- 1–5
  level_name                TEXT NOT NULL,     -- e.g. "Redetermination"
  decision_maker            TEXT NOT NULL,     -- MAC, QIC, OMHA ALJ, DAB MAC, U.S. District Court
  filing_deadline_days      INTEGER,           -- days from prior determination to file; NULL for MA note row
  filing_deadline_basis     TEXT NOT NULL,     -- what the deadline is measured from
  min_amount_in_controversy INTEGER,           -- cents; NULL for L1/L2 (no AIC requirement)
  submission_address_notes  TEXT,              -- general routing; plan-specific from EOB
  citation                  TEXT NOT NULL,     -- CFR cite and/or CMS manual reference
  plan_type                 TEXT NOT NULL,     -- "traditional_medicare"|"medicare_advantage"|"both"
  deadline_reviewed_by      TEXT,              -- counsel initials; NULL until reviewed
  source_id                 TEXT NOT NULL      -- "e03_medicare_appeals"
);
```

### Records to create

Create these 6 rows. **Do not populate `deadline_reviewed_by` — counsel must do this.**

| `level_id` | `level_name` | `filing_deadline_days` | `filing_deadline_basis` | `min_aic_cents` | `plan_type` |
|---|---|---|---|---|---|
| `medicare_L1` | Redetermination | 120 | Date of initial determination | NULL | `traditional_medicare` |
| `medicare_L2` | Reconsideration (QIC) | 180 | Date of redetermination notice | NULL | `traditional_medicare` |
| `medicare_L3` | ALJ Hearing | 60 | Date of QIC decision | *CMS-annual — do not hardcode; store as NULL and note* | `traditional_medicare` |
| `medicare_L4` | Medicare Appeals Council | 60 | Date of ALJ decision | NULL | `traditional_medicare` |
| `medicare_L5` | Federal District Court | 60 | Date of MAC decision | *CMS-annual — store as NULL and note* | `traditional_medicare` |
| `medicare_MA` | MA Equivalent Process | NULL | Plan-specific | NULL | `medicare_advantage` |

> The MA note row (`medicare_MA`) flags that MA plans must offer an equivalent 5-level process
> but submission is plan-specific. The application routes MA members to their plan's appeals
> contact rather than to a federal address.

### Ingestor contract

**Module path:** `ingestors/e_rules/e03_medicare_appeals/`

```
fetch.py      No automated download required. Records are hand-authored from CMS CFR and
              manual guidance. Place source text reference at
              data/raw/e03_medicare_appeals/source_refs.txt.

parse.py      Not applicable — records are manually authored in a fixture file
              data/raw/e03_medicare_appeals/records.json.

normalize.py  Load records.json, validate enum values, convert AIC to integer cents.

validate.py   Assert exactly 6 rows. Assert filing_deadline_days non-null on all non-MA rows.
              Warn (do not fail) if deadline_reviewed_by IS NULL on any row.

publish.py    INSERT OR REPLACE into medicare_appeal_levels.
              Call record_release(conn, "e03_medicare_appeals",
              "https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-405",
              "manual", row_count) — use sentinel "manual" for file_hash (no downloaded file).
```

> **Deadline constraint:** The application serves `filing_deadline_days` only when
> `deadline_reviewed_by IS NOT NULL`. When NULL, the UI shows "verify the deadline on your
> denial notice." The build is not blocked by missing counsel sign-off — the UI degrades safely.

### Acceptance gate

- [ ] Table `medicare_appeal_levels` has exactly 6 rows
- [ ] All 5 traditional Medicare rows have `filing_deadline_days` non-null
- [ ] `citation` non-null on all rows
- [ ] Entry present in `source_registry.yaml` for `e03_medicare_appeals`

---

## Module E.4 — Federal ACA / commercial appeals framework

### Rationale

Commercial and ACA Marketplace plan denials follow a different pathway from Medicare: internal
appeal first, then external independent review (IRO). The pathway also varies by plan funding
type — fully-insured plans are subject to state external review requirements; self-funded ERISA
plans are generally preempted from state law and subject only to federal ERISA rules. The
application must route correctly based on `plan_funding_type` captured at intake.

### Source

- **45 CFR §147.136:** Internal claims appeal and external review for ACA-compliant plans
- **EBSA guidance:** `https://www.dol.gov/agencies/ebsa/laws-and-regulations/laws/affordable-care-act/for-employers-and-advisers/appeals`
- **29 CFR §2560.503-1:** ERISA claims procedure regulations
- **Update cadence:** Annual review
- **License:** Public domain

### Schema — table `commercial_appeal_levels`

```sql
CREATE TABLE IF NOT EXISTS commercial_appeal_levels (
  level_id                  TEXT PRIMARY KEY,  -- "aca_L1", "aca_L2", "erisa_L1", "erisa_L2"
  level_number              INTEGER NOT NULL,  -- 1 or 2
  level_name                TEXT NOT NULL,     -- "Internal Appeal" | "External Independent Review"
  applicable_plan_types     TEXT NOT NULL,     -- JSON array: ["fully_insured","self_funded_erisa"]
  filing_deadline_days      INTEGER,           -- days from denial to file; NULL where state-variable
  filing_deadline_basis     TEXT NOT NULL,
  decision_deadline_days    INTEGER,           -- days plan must decide; NULL if variable or not mandated
  decision_deadline_basis   TEXT,
  iro_applicable            INTEGER NOT NULL,  -- 0|1 — external IRO available at this level
  erisa_preemption_note     TEXT,              -- populated for ERISA rows
  citation                  TEXT NOT NULL,
  deadline_reviewed_by      TEXT,              -- counsel initials; NULL until reviewed
  source_id                 TEXT NOT NULL      -- "e04_aca_appeals"
);
```

### Records to create

`aca_L2` has no federal filing deadline (it varies by state and plan) — store `NULL` for both
`filing_deadline_days` and `decision_deadline_days`. The application must instruct the member
to check their plan documents and state rules for these values.

| `level_id` | `level_name` | `applicable_plan_types` | `filing_deadline_days` | `decision_deadline_days` | `iro_applicable` |
|---|---|---|---|---|---|
| `aca_L1` | Internal Appeal | `["fully_insured"]` | 180 | 60 (post-service); 30 (pre-service) — store 60 as conservative default | 0 |
| `aca_L2` | External Independent Review | `["fully_insured"]` | NULL — varies by state | NULL — varies by state | 1 |
| `erisa_L1` | Internal Appeal | `["self_funded_erisa"]` | 180 | 60 (post-service); 30 (pre-service) — store 60 | 0 |
| `erisa_L2` | Voluntary External Review | `["self_funded_erisa"]` | NULL — plan-specific | NULL — plan-specific | 1 |

> **ERISA preemption note** must be populated on `erisa_L2`:
> `"State external review laws are generally preempted for self-funded ERISA plans. External
> review at this level is voluntary and plan-specific. Verify with your plan documents."`
>
> **Routing rule for the application:** When `plan_funding_type = self_funded_erisa` and the
> member asks about state external review, the engine must surface the ERISA preemption note
> and escalate to human review. This is a load-bearing edge case.

### Ingestor contract

**Module path:** `ingestors/e_rules/e04_aca_appeals/`

Same pattern as E.3: hand-authored fixture file at
`data/raw/e04_aca_appeals/records.json` loaded by `normalize.py` → `validate.py` → `publish.py`.
No automated fetch required.

```
validate.py   Assert exactly 4 rows.
              Assert filing_deadline_days non-null on aca_L1 and erisa_L1 only
              (aca_L2 and erisa_L2 are intentionally NULL — do not assert non-null on these).
              Assert erisa_preemption_note non-null on erisa_L2.
              Warn (do not fail) if deadline_reviewed_by IS NULL.

publish.py    INSERT OR REPLACE into commercial_appeal_levels.
              Call record_release(conn, "e04_aca_appeals",
              "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-B/part-147",
              "manual", row_count) — use sentinel "manual" for file_hash (no downloaded file).
```

### Acceptance gate

- [ ] Table `commercial_appeal_levels` has exactly 4 rows
- [ ] `erisa_preemption_note` non-null on `erisa_L2`
- [ ] `filing_deadline_days` non-null on `aca_L1` and `erisa_L1`; NULL on `aca_L2` and `erisa_L2`
- [ ] `citation` non-null on all rows
- [ ] Entry present in `source_registry.yaml` for `e04_aca_appeals`

---

## Module C.4 — Medicare Advantage Landscape

### Rationale

MASA's member population skews 55+ with a high concentration of Medicare Advantage enrollees.
Without MA Landscape data, plan identification fails for this segment — the application cannot
match a member's plan name to a contract/PBP identifier, cannot retrieve plan benefits, and
cannot route an appeal to the correct MA pathway. This is the highest-impact single gap for
MASA's actual member population.

### Source

- **CMS MA Landscape files:** `https://www.cms.gov/medicare/health-drug-plans/medicareadvantage/enrollment/plan-information`
- **Format:** CSV, annual release (October) covering upcoming plan year
- **Update cadence:** Annual; re-run in October/November each year
- **License:** Public domain

### Schema — table `ma_plans`

```sql
CREATE TABLE IF NOT EXISTS ma_plans (
  ma_plan_id        TEXT PRIMARY KEY,  -- contract_id + "_" + plan_id + "_" + segment_id + "_" + plan_year
  contract_id       TEXT NOT NULL,     -- e.g. "H1234"
  plan_id           TEXT NOT NULL,     -- PBP identifier e.g. "001"
  segment_id        TEXT,              -- NULL if no segments
  plan_year         INTEGER NOT NULL,
  plan_name         TEXT NOT NULL,
  organization_name TEXT NOT NULL,
  plan_type         TEXT NOT NULL,     -- "HMO"|"PPO"|"PFFS"|"MSA"|"SNP"
  state             TEXT NOT NULL,     -- 2-letter code
  county_name       TEXT NOT NULL,
  fips_code         TEXT,              -- 5-digit FIPS county code
  snp_type          TEXT,              -- "D-SNP"|"C-SNP"|"I-SNP"|NULL
  premium_cents     INTEGER,           -- monthly premium in cents; NULL if not published
  star_rating       TEXT,              -- CMS overall star rating; NULL if not yet rated
  source_id         TEXT NOT NULL      -- "c04_ma_landscape"
);

-- Search index for intake plan identification
CREATE INDEX IF NOT EXISTS idx_ma_plans_search
  ON ma_plans (state, county_name, plan_year);

CREATE INDEX IF NOT EXISTS idx_ma_plans_name
  ON ma_plans (organization_name, plan_name);
```

### Ingestor contract

**Module path:** `ingestors/c_medicare/c04_ma_landscape/`

```
fetch.py      HTTP GET to CMS MA Landscape CSV download URL.
              Store at data/raw/c04_ma_landscape/ with SHA-256 hash.
              Handle both the medical and Part D landscape files (medical is primary).

parse.py      Read CSV. Map CMS column names to schema columns.
              Construct ma_plan_id composite key.
              Parse premium to integer cents (handle "$0.00" and blank).

normalize.py  Normalize state to 2-letter code. Normalize plan_type to enum.
              Set snp_type from SNP_TYPE column where present.

validate.py   Assert row count >= 10,000.
              Assert all rows have contract_id, plan_id, plan_year, plan_name, state, county_name.
              Assert plan_year IN (2025, 2026) — both are acceptable during the Oct 2025→Oct 2026
              transition window (2025 = currently active plans; 2026 = upcoming plan year data
              released October 2026). Fail if neither year is present.
              Assert no duplicate ma_plan_id values.

publish.py    Bulk INSERT OR REPLACE into ma_plans using transaction batching (1,000 rows/tx).
              Call record_release(conn, "c04_ma_landscape", source_url, file_hash, row_count)
              where source_url is the CMS MA Landscape CSV download URL and file_hash is the
              SHA-256 of the downloaded ZIP/CSV. Rebuild search indexes after load.
```

### Lookup design for intake

The application intake flow uses fuzzy plan name matching — members are unlikely to know their
contract ID. The data-access module exposes:

```python
search_ma_plan(name_fragment: str, state: str, county: str = None) -> list[dict]
```

Implementation: full-text search on `plan_name` and `organization_name`, filtered by `state`
and optionally `county_name`. Returns up to 10 candidates for member selection from a dropdown.
"Unknown plan" is a valid intake answer — routes to human review.

### Acceptance gate

- [ ] Table `ma_plans` exists and non-empty
- [ ] Row count >= 10,000
- [ ] `search_ma_plan('Humana', 'FL', 'Miami-Dade')` returns >= 1 result
- [ ] `plan_year IN (2025, 2026)` — at least one of these years present (both acceptable during transition)
- [ ] Entry present in `source_registry.yaml` for `c04_ma_landscape`

---

> **C.5 is intentionally reserved** — not accidentally omitted. The C.5 slot is held for a
> future module (TBD). Do not assign `c05_*` source IDs in this sprint.

## Module C.6 — NCCI edits (full table)

### Rationale

The National Correct Coding Initiative (NCCI) edits are CMS-published rules specifying which
procedure code pairs cannot be billed together (Procedure-to-Procedure, PTP edits) and maximum
units per code (Medically Unlikely Edits, MUE). They are the definitive reference for detecting
unbundling and unit-count overcharges.

The full table is ingested (not just ambulance-scoped) because the SAM roadmap includes
non-ambulance claim denial support. The application-layer search index prioritizes ambulance
HCPCS first; the data is complete underneath.

### Source

- **CMS NCCI:** `https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits`
- **Files:** Quarterly ZIP releases — PTP edits (physician and outpatient/ASC) + MUE tables
- **Format:** Fixed-width or CSV; column layout varies by release year
- **Update cadence:** Quarterly (January, April, July, October)
- **License:** Public domain

### Schema — table `ncci_ptp_edits`

```sql
CREATE TABLE IF NOT EXISTS ncci_ptp_edits (
  edit_id             TEXT PRIMARY KEY,  -- column_one_code + "_" + column_two_code + "_" + effective_date
  column_one_code     TEXT NOT NULL,     -- comprehensive (primary) procedure code
  column_two_code     TEXT NOT NULL,     -- component (secondary) code — cannot be billed with col 1
  modifier_indicator  INTEGER NOT NULL,  -- 0=modifier cannot override; 1=modifier may allow; 9=N/A
  effective_date      TEXT NOT NULL,     -- ISO 8601
  deletion_date       TEXT,              -- ISO 8601; NULL = currently active
  edit_type           TEXT NOT NULL,     -- "physician"|"outpatient_asc"
  quarter             TEXT NOT NULL,     -- release quarter e.g. "2026Q2"
  source_id           TEXT NOT NULL      -- "c06_ncci_ptp"
);

CREATE INDEX IF NOT EXISTS idx_ncci_ptp_col1 ON ncci_ptp_edits (column_one_code, deletion_date);
CREATE INDEX IF NOT EXISTS idx_ncci_ptp_col2 ON ncci_ptp_edits (column_two_code, deletion_date);
```

### Schema — table `ncci_mue`

```sql
CREATE TABLE IF NOT EXISTS ncci_mue (
  mue_id                      TEXT PRIMARY KEY,  -- hcpcs_code + "_" + effective_date
  hcpcs_code                  TEXT NOT NULL,
  mue_value                   INTEGER NOT NULL,  -- maximum units per claim line per DOS
  mue_adjudication_indicator  TEXT NOT NULL,     -- "claim_line"|"date_of_service"|"per_patient_per_day"
  effective_date              TEXT NOT NULL,     -- ISO 8601
  deletion_date               TEXT,              -- NULL = currently active
  quarter                     TEXT NOT NULL,
  source_id                   TEXT NOT NULL      -- "c06_ncci_mue"
);

CREATE INDEX IF NOT EXISTS idx_ncci_mue_code ON ncci_mue (hcpcs_code, deletion_date);
```

### Ingestor contract

**Module path:** `ingestors/c_medicare/c06_ncci_edits/`

```
fetch.py      HTTP GET to CMS NCCI quarterly ZIP download URL.
              Store ZIP at data/raw/c06_ncci_edits/{quarter}/ with SHA-256 hash.
              Extract CSV/fixed-width files from ZIP.

parse.py      CRITICAL: Read column-header row dynamically — do NOT use hardcoded column
              offsets. CMS has changed the layout between release years.
              Map discovered headers to schema columns.
              Parse both physician and outpatient_asc PTP files.
              Parse MUE file separately.

normalize.py  Construct composite edit_id. Normalize dates to ISO 8601.
              Set deletion_date to NULL where the source encodes "active" as blank or "0".
              Set quarter from filename/release metadata.

validate.py   Assert ncci_ptp_edits row count >= 100,000 (full table, not a subset).
              Assert ncci_mue row count >= 5,000.
              Assert no duplicate edit_id values within a quarter.
              Run golden fixture: check_ncci_conflict('A0427', 'A0429', '2026-01-01')
              returns expected result (verify against CMS source before wiring the fixture).
              Assert schema_drift: compare incoming headers against prior quarter's header set;
              FAIL FAST if unexpected columns appear rather than silently mis-mapping.

publish.py    Bulk INSERT OR REPLACE using transaction batching (5,000 rows/tx) for PTP edits.
              Bulk INSERT OR REPLACE for MUE (smaller; 1,000 rows/tx sufficient).
              Call record_release(conn, "c06_ncci_ptp", source_url, file_hash, ptp_row_count)
              and record_release(conn, "c06_ncci_mue", source_url, file_hash, mue_row_count)
              where source_url is the CMS NCCI quarterly ZIP URL and file_hash is its SHA-256.
              Rebuild indexes after load.
              Add/update entry in source_registry.yaml to reflect quarterly cadence.
```

> **Versioning:** Never overwrite prior quarter data. `deletion_date` tracks retired edits.
> The application queries `WHERE deletion_date IS NULL OR deletion_date > :service_date`
> to get the edit set applicable to a given claim date.
>
> **Volume:** PTP edit tables are large. Bulk INSERT with transaction batching is required —
> do not row-by-row insert.

### Acceptance gate

- [ ] Table `ncci_ptp_edits` exists and non-empty; row count >= 100,000
- [ ] Table `ncci_mue` exists and non-empty; row count >= 5,000
- [ ] Golden fixture lookup passes (see `validate.py` above)
- [ ] Entry present in `source_registry.yaml` for `c06_ncci_ptp` and `c06_ncci_mue` with quarterly cadence noted
- [ ] Schema drift test in place and passing on current quarter

---

## Data-access module additions (`masa-sam-advocate`)

The SAM application's data-access module must expose the following functions to consume the new
tables. Specify these in the SAM prototype PRD v2.0 — they are documented here so the data
contract is locked before the application build starts.

```python
def get_ncd_criteria(
    criterion_type: str = None,
    transport_type: str = None
) -> list[dict]:
    """
    Returns reviewed ncd_ambulance rows matching filters.
    Only rows with reviewed=1 are returned.
    """

def get_appeal_pathway(
    insurance_situation: str,
    plan_funding_type: str,
    state: str
) -> list[dict]:
    """
    Returns ordered appeal level records applicable to the member's situation.
    Routes to medicare_appeal_levels for medicare_only / medicare_advantage.
    Routes to commercial_appeal_levels for commercial_employer / commercial_individual.
    Only returns levels with deadline_reviewed_by IS NOT NULL.
    When no reviewed deadlines exist, returns levels with filing_deadline_days=None
    and a flag indicating the UI should show 'verify on denial notice'.
    """

def check_ncci_conflict(
    code_a: str,
    code_b: str,
    service_date: str
) -> dict | None:
    """
    Returns ncci_ptp_edits row if code_a + code_b is an active PTP edit on service_date.
    Checks both (code_a, code_b) and (code_b, code_a) — order matters for column_one/two.
    Returns None if no conflict found.
    Includes modifier_indicator in result so application can advise on modifier override.
    """

def get_mue(
    hcpcs_code: str,
    service_date: str
) -> dict | None:
    """
    Returns ncci_mue row for hcpcs_code active on service_date.
    Returns None if no MUE entry exists.
    """

def search_ma_plan(
    name_fragment: str,
    state: str,
    county: str = None,
    plan_year: int = None
) -> list[dict]:
    """
    Fuzzy search on plan_name and organization_name, filtered by state and optionally county.
    Returns up to 10 candidates ordered by name relevance.
    Used in intake plan-identification step for Medicare Advantage members.
    """
```

---

## Acceptance gates summary

All P0 gates must pass before the SAM prototype PRD v2.0 build begins.

| Module | Gate | Type |
|---|---|---|
| E.2 NCD 10.1 | `ncd_ambulance` non-empty; >= 7 key criteria rows; >= 6 with `reviewed=1`; `citation` non-null on all; entry in `source_registry.yaml` | Schema + content + human review log |
| E.3 Medicare appeals | 6 rows; `filing_deadline_days` non-null on all 5 traditional Medicare rows; `citation` non-null on all; entry in `source_registry.yaml` | Schema + row count + deadline audit |
| E.4 ACA/commercial appeals | 4 rows; `erisa_preemption_note` on `erisa_L2`; `filing_deadline_days` non-null on `aca_L1` and `erisa_L1`; NULL on `aca_L2` and `erisa_L2`; `citation` non-null on all; entry in `source_registry.yaml` | Schema + row count + content check |
| C.4 MA Landscape | >= 10,000 rows; `search_ma_plan('Humana', 'FL', 'Miami-Dade')` returns >= 1; `plan_year IN (2025, 2026)`; entry in `source_registry.yaml` | Schema + row count + golden search |
| C.6 NCCI edits | `ncci_ptp_edits` >= 100,000 rows; `ncci_mue` >= 5,000 rows; golden fixture passes; entries in `source_registry.yaml` for both `c06_ncci_ptp` and `c06_ncci_mue`; schema drift test passing | Schema + row count + golden fixture |

---

## Out of scope — this sprint

Do not build the following as part of this addendum:

- **NSA rule counsel review pass** — legal task, not ingestion. `nsa_rules` is already loaded.
  Counsel reviews `nsa_ruleset.xlsx`, populates `deadline_days` / `deadline_basis`, marks
  eligible rows `counsel_approved`. Ingestor re-runs after review. Tracked separately.
- **SBC download cap lift** — pipeline tuning, not a new module. Lifting `MAX_DOWNLOADS` from
  2,384 expands deductible/OOP SBC coverage; recommended follow-on after this sprint.
- **State surprise billing laws (Family E, P1 slots)** — per-state adapters remain P1 in the original
  PRD. Not required for the prototype flows.
- **Payer medical policy crawlers (Family G)** — remain P1. Not required for ambulance denial
  appeals in the prototype.
- **Hospital Price Transparency / TiC MRFs** — ambulance-scoped P2. Not required for prototype.
- **SBE state plans (NY, MA, WA, CO, etc.)** — requires per-state scrapers. Not in scope.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| NCD 10.1 LLM extraction produces low-quality criteria rows | High | Human review gate (`reviewed=1`) is mandatory before any row reaches the application. LLM output goes to staging only. Accept slower timeline over incorrect citations in appeal letters. |
| Counsel unavailable to review appeal deadlines before build completes | Medium | Application degrades safely: `filing_deadline_days` served only when `deadline_reviewed_by IS NOT NULL`; UI shows "verify on denial notice" otherwise. Build not blocked. |
| NCCI format changes between quarterly releases | Medium | Parser reads column headers dynamically. Schema drift test in `validate.py` fails fast on unexpected columns rather than silently mis-mapping data. |
| MA Landscape plan names don't match member-entered names at intake | Low–Med | Fuzzy search + dropdown selection mitigates exact-match failures. "Unknown plan" is a valid intake answer that routes to human review. |
| CMS URL restructuring breaks `fetch.py` | Low | Source registry centralizes all URLs. Weekly URL health-check catches broken links within days. Archive copy of last successful fetch preserved by hash. |
