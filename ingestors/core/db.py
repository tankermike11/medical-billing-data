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
