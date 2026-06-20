import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "pilot.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS dataset_releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    source_url      TEXT,
    source_version  TEXT,
    file_hash       TEXT,
    row_count       INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',
    notes           TEXT,
    retrieved_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    release_id  INTEGER REFERENCES dataset_releases(id),
    error_type  TEXT,
    message     TEXT,
    occurred_at TEXT NOT NULL
);

-- Unified code table for all Family A code sets
CREATE TABLE IF NOT EXISTS codes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code_type        TEXT NOT NULL,
    code             TEXT NOT NULL,
    description      TEXT,
    short_description TEXT,
    is_header        INTEGER NOT NULL DEFAULT 0,
    effective_date   TEXT,
    expiration_date  TEXT,
    extra            TEXT,          -- JSON blob for type-specific fields
    source_id        TEXT NOT NULL,
    source_url       TEXT,
    source_version   TEXT,
    retrieved_at     TEXT NOT NULL,
    UNIQUE(code_type, code, source_version)
);
CREATE INDEX IF NOT EXISTS idx_codes_type_code ON codes(code_type, code);

CREATE TABLE IF NOT EXISTS code_relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    code_type         TEXT NOT NULL,
    parent_code       TEXT NOT NULL,
    child_code        TEXT NOT NULL,
    relationship_type TEXT NOT NULL DEFAULT 'parent_child',
    source_id         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_rel_parent ON code_relationships(code_type, parent_code);

-- Family B: plans
CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         TEXT NOT NULL,
    plan_name       TEXT,
    issuer_id       TEXT,
    issuer_name     TEXT,
    state           TEXT,
    county_fips     TEXT,
    county_name     TEXT,
    plan_type       TEXT,
    metal_level     TEXT,
    plan_year       INTEGER,
    market_coverage TEXT,
    dental_only     INTEGER NOT NULL DEFAULT 0,
    national_network INTEGER NOT NULL DEFAULT 0,
    extra           TEXT,
    source_id       TEXT NOT NULL,
    source_url      TEXT,
    source_version  TEXT,
    retrieved_at    TEXT NOT NULL,
    UNIQUE(plan_id, plan_year, source_id)
);
CREATE INDEX IF NOT EXISTS idx_plans_state_year ON plans(state, plan_year);

CREATE TABLE IF NOT EXISTS plan_attributes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         TEXT NOT NULL,
    plan_year       INTEGER,
    attribute_name  TEXT NOT NULL,
    attribute_value TEXT,
    source_id       TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_attr ON plan_attributes(plan_id, plan_year, attribute_name);

CREATE TABLE IF NOT EXISTS plan_benefits (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id                 TEXT NOT NULL,
    plan_year               INTEGER,
    benefit_name            TEXT,
    covered                 TEXT,
    copay_inn_tier1         TEXT,
    coinsurance_inn_tier1   TEXT,
    copay_outn              TEXT,
    coinsurance_outn        TEXT,
    is_excluded             INTEGER NOT NULL DEFAULT 0,
    source_id               TEXT NOT NULL,
    retrieved_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plan_ben ON plan_benefits(plan_id, plan_year);

CREATE TABLE IF NOT EXISTS plan_materials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         TEXT NOT NULL,
    plan_year       INTEGER,
    material_type   TEXT NOT NULL,
    url             TEXT,
    http_status     INTEGER,
    content_type    TEXT,
    source_id       TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    UNIQUE(plan_id, plan_year, material_type)
);

-- Family F: SBC documents
CREATE TABLE IF NOT EXISTS sbc_documents (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id                TEXT,
    plan_year              INTEGER,
    url                    TEXT NOT NULL,
    file_hash              TEXT,
    local_path             TEXT,
    page_count             INTEGER,
    extraction_method      TEXT,
    extraction_confidence  REAL,
    source_id              TEXT NOT NULL,
    retrieved_at           TEXT NOT NULL,
    UNIQUE(url, plan_year)
);

CREATE TABLE IF NOT EXISTS sbc_fields (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sbc_document_id     INTEGER NOT NULL REFERENCES sbc_documents(id),
    field_name          TEXT NOT NULL,
    field_value         TEXT,
    confidence          REAL,
    extraction_method   TEXT,
    page_number         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sbc_fields ON sbc_fields(sbc_document_id, field_name);

-- Source attribution: resolves every source_id to publisher + license
CREATE TABLE IF NOT EXISTS sources (
    source_id        TEXT PRIMARY KEY,
    publisher        TEXT NOT NULL,
    canonical_url    TEXT,
    license          TEXT,
    refresh_cadence  TEXT,
    notes            TEXT
);

-- Family C: Medicare ground-ambulance reference rates
CREATE TABLE IF NOT EXISTS ambulance_fee_schedule (
    hcpcs           TEXT NOT NULL,
    geo_level       TEXT NOT NULL,      -- 'state' | 'urban_rural'
    geo_key         TEXT NOT NULL,      -- e.g. 'FL', 'TX'
    reference_rate  INTEGER NOT NULL,   -- integer cents (deliberate exception to raw-string convention)
    effective_year  INTEGER NOT NULL,
    source_id       TEXT NOT NULL,
    PRIMARY KEY (hcpcs, geo_level, geo_key, effective_year)
);

-- Family E: NSA / GFE-PPDR / ground-ambulance federal ruleset (Tables A–K)
CREATE TABLE IF NOT EXISTS nsa_rules (
    rule_id          TEXT PRIMARY KEY,
    category         TEXT NOT NULL,     -- A–K
    summary          TEXT,
    prototype_logic  TEXT,
    system_action    TEXT,
    citation         TEXT,
    deadline_days    INTEGER,
    deadline_basis   TEXT,
    qpa_dependent    INTEGER NOT NULL DEFAULT 0,
    ruleset_version  TEXT,
    effective_date   TEXT,
    last_reviewed    TEXT,
    reviewed_by      TEXT,
    status           TEXT NOT NULL,     -- 'draft' | 'counsel_approved'
    source_id        TEXT NOT NULL
);

-- E.2: CMS NCD 10.1 Ambulance Services criteria (reviewed=0 until human-approved)
CREATE TABLE IF NOT EXISTS ncd_ambulance (
    ncd_id             TEXT NOT NULL,   -- "10.1"
    section_id         TEXT NOT NULL,   -- e.g. "10.1.1"
    section_title      TEXT NOT NULL,
    coverage_indicator TEXT,            -- "covered"|"non_covered"|"conditional"|"informational"
    criteria_text      TEXT NOT NULL,
    criterion_type     TEXT,            -- "medical_necessity"|"transport_condition"|"facility_standard"|"exclusion"|"definition"
    effective_date     TEXT NOT NULL,
    citation           TEXT NOT NULL,
    reviewed           INTEGER NOT NULL DEFAULT 0,  -- 0=draft; 1=human-approved
    source_id          TEXT NOT NULL,
    PRIMARY KEY (ncd_id, section_id)
);

-- E.3: Medicare 5-level appeals framework
CREATE TABLE IF NOT EXISTS medicare_appeal_levels (
    level_id                  TEXT PRIMARY KEY,
    level_number              INTEGER NOT NULL,
    level_name                TEXT NOT NULL,
    decision_maker            TEXT NOT NULL,
    filing_deadline_days      INTEGER,           -- NULL for MA note row
    filing_deadline_basis     TEXT NOT NULL,
    min_amount_in_controversy INTEGER,           -- cents; NULL where no AIC requirement
    submission_address_notes  TEXT,
    citation                  TEXT NOT NULL,
    plan_type                 TEXT NOT NULL,     -- "traditional_medicare"|"medicare_advantage"|"both"
    deadline_reviewed_by      TEXT,              -- counsel initials; NULL until reviewed
    source_id                 TEXT NOT NULL
);

-- E.4: Federal ACA / commercial appeals framework
CREATE TABLE IF NOT EXISTS commercial_appeal_levels (
    level_id                  TEXT PRIMARY KEY,
    level_number              INTEGER NOT NULL,
    level_name                TEXT NOT NULL,
    applicable_plan_types     TEXT NOT NULL,     -- JSON array
    filing_deadline_days      INTEGER,           -- NULL where state-variable or plan-specific
    filing_deadline_basis     TEXT NOT NULL,
    decision_deadline_days    INTEGER,
    decision_deadline_basis   TEXT,
    iro_applicable            INTEGER NOT NULL,  -- 0|1
    erisa_preemption_note     TEXT,
    citation                  TEXT NOT NULL,
    deadline_reviewed_by      TEXT,
    source_id                 TEXT NOT NULL
);

-- C.4: Medicare Advantage Landscape
CREATE TABLE IF NOT EXISTS ma_plans (
    ma_plan_id        TEXT PRIMARY KEY,  -- contract_id_plan_id_segment_id_plan_year
    contract_id       TEXT NOT NULL,
    plan_id           TEXT NOT NULL,
    segment_id        TEXT,
    plan_year         INTEGER NOT NULL,
    plan_name         TEXT NOT NULL,
    organization_name TEXT NOT NULL,
    plan_type         TEXT NOT NULL,
    state             TEXT NOT NULL,
    county_name       TEXT NOT NULL,
    fips_code         TEXT,
    snp_type          TEXT,
    premium_cents     INTEGER,
    star_rating       TEXT,
    source_id         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ma_plans_search ON ma_plans (state, county_name, plan_year);
CREATE INDEX IF NOT EXISTS idx_ma_plans_name   ON ma_plans (organization_name, plan_name);

-- C.6: NCCI PTP (Procedure-to-Procedure) edits
CREATE TABLE IF NOT EXISTS ncci_ptp_edits (
    edit_id            TEXT PRIMARY KEY,  -- col1_col2_effective_date
    column_one_code    TEXT NOT NULL,     -- comprehensive (primary) code
    column_two_code    TEXT NOT NULL,     -- component (secondary) code
    modifier_indicator INTEGER NOT NULL,  -- 0=no override; 1=modifier may allow; 9=N/A
    effective_date     TEXT NOT NULL,
    deletion_date      TEXT,              -- NULL = currently active
    edit_type          TEXT NOT NULL,     -- "physician"|"outpatient_asc"
    quarter            TEXT NOT NULL,     -- e.g. "2026Q2"
    source_id          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ncci_ptp_col1 ON ncci_ptp_edits (column_one_code, deletion_date);
CREATE INDEX IF NOT EXISTS idx_ncci_ptp_col2 ON ncci_ptp_edits (column_two_code, deletion_date);

-- C.2: Medicare Physician Fee Schedule national payment amounts
-- Rates stored as INTEGER CENTS. non_fac_rate = office/non-facility allowed amount;
-- fac_rate = hospital/facility allowed amount. NULL where CMS marks N/A.
-- status_code: A=active, B=bundled, C=contractor-priced, N=non-covered, etc.
-- No AMA CPT descriptions stored — code number only (same carve-out as a04_cpt_handling).
CREATE TABLE IF NOT EXISTS physician_fee_schedule (
    hcpcs          TEXT NOT NULL,
    modifier       TEXT NOT NULL DEFAULT '',
    non_fac_rate   INTEGER,         -- cents; NULL if not applicable or contractor-priced
    fac_rate       INTEGER,         -- cents; NULL if not applicable or contractor-priced
    status_code    TEXT,
    effective_year INTEGER NOT NULL,
    source_id      TEXT NOT NULL,
    PRIMARY KEY (hcpcs, modifier, effective_year)
);
CREATE INDEX IF NOT EXISTS idx_pfs_hcpcs ON physician_fee_schedule(hcpcs, effective_year);

-- C.6: NCCI MUE (Medically Unlikely Edits)
CREATE TABLE IF NOT EXISTS ncci_mue (
    mue_id                     TEXT PRIMARY KEY,  -- hcpcs_effective_date
    hcpcs_code                 TEXT NOT NULL,
    mue_value                  INTEGER NOT NULL,  -- max units per claim line per DOS
    mue_adjudication_indicator TEXT NOT NULL,     -- "claim_line"|"date_of_service"|"per_patient_per_day"
    effective_date             TEXT NOT NULL,
    deletion_date              TEXT,
    quarter                    TEXT NOT NULL,
    source_id                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ncci_mue_code ON ncci_mue (hcpcs_code, deletion_date);
"""


def get_conn(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migrations for columns added after initial schema creation
    try:
        conn.execute("ALTER TABLE plan_materials ADD COLUMN content_type TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn
