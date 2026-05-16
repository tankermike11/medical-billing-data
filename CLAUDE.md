# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MASA Billing Advocacy Data Pilot — a data ingestion pipeline that collects public medical billing reference data (ICD codes, HCPCS, DRG, marketplace plans, SBC PDFs) into a local SQLite database for use in a patient billing advocacy tool.

## Environment Setup

Python 3.14. Always activate the virtual environment before running anything:
```
venv\Scripts\activate        # Windows PowerShell
source venv/bin/activate     # Unix
```

Install dependencies:
```
pip install -r requirements.txt
```

## Common Commands

```
python cli.py run <source_id>          # run a single ingestor
python cli.py run-all <family>         # run all ingestors in a family (a_codes, b_marketplace, f_sbc)
python cli.py status                   # show ingestion history from the database
python cli.py list                     # list all available source IDs
```

Family F must run in order: `f01_url_crawler` → `f02_downloader` → `f03_parser`.

## Architecture

### Source Registry

`source_registry.yaml` is the single source of truth for all data sources — canonical URLs, format, refresh cadence, and licensing. The pipeline reads this at runtime; ingestors never hardcode URLs (except `a08_pos` and legacy `a09_modifiers` which predate this pattern).

### Ingestor Pattern

Each ingestor lives at `ingestors/<family>/<source_id>/run.py` and exports a `main()` function. Most use `run_ingestor(SOURCE_ID, ingest)` from `core/pipeline.py`, which handles:
1. Loading the registry entry for the source
2. Downloading the file to `data/raw/<source_id>/` (skips if file hash unchanged)
3. Calling the `ingest(source, local_path, file_hash, conn)` function
4. Recording the release in `dataset_releases`

Exceptions: `a07_revenue` (reads a manual CSV, no download), `a08_pos` (PDF parsing via pdfplumber), `a09_modifiers` (reads from a03's cached ZIP), and Family F steps (operate on the database directly rather than downloading fresh files).

### Core Modules

- `ingestors/core/db.py` — `get_conn()` returns a SQLite connection with the full schema applied. Schema migrations are handled inline via try/except ALTER TABLE.
- `ingestors/core/http.py` — `download()` and `fetch_text()` with rate limiting, SSL via `truststore` (Windows certificate store), and SHA-256 hashing. All HTTP calls must use these functions or pass `verify=_ssl_ctx` directly to httpx.
- `ingestors/core/pipeline.py` — `run_ingestor()`, `record_release()`, `record_error()`, `load_registry()`.

### Database Schema (SQLite at `data/db/pilot.db`)

- `codes` — all Family A code sets (ICD-10-CM, ICD-10-PCS, HCPCS, CARC, RARC, MS-DRG, POS, Modifiers, NDC, Revenue). Keyed on `(code_type, code, source_version)`.
- `plans` / `plan_materials` / `plan_attributes` / `plan_benefits` — Family B marketplace data. `plan_materials` holds SBC/brochure/network URLs with `http_status` and `content_type` fields populated by f01.
- `sbc_documents` / `sbc_fields` — Family F parsed SBC PDFs. Fields include deductibles, OOP max, copays extracted by regex from pdfplumber text.
- `dataset_releases` — one row per successful ingestor run with file hash and row count. The pipeline uses the hash to skip re-parsing unchanged files.
- `extraction_errors` — ingestor failures logged here for debugging.

### Family F (SBC Pipeline)

Three-step sequential pipeline:
1. **f01_url_crawler** — HEAD-checks every SBC URL in `plan_materials`, writes `http_status` and `content_type` back. `MAX_URLS` controls pilot capping.
2. **f02_downloader** — downloads PDFs for `http_status=200` URLs not yet in `sbc_documents`. Stores at `data/raw/f_sbc/`. `MAX_DOWNLOADS` controls pilot capping.
3. **f03_parser** — extracts structured fields from PDFs using pdfplumber + regex patterns in `FIELD_PATTERNS`. Skips documents already in `sbc_fields`.

Key parsing note: pdfplumber extracts table rows with the answer column ("In-network: $X Individual / $Y") appearing **before** the question column ("What is the out-of-pocket limit") in linearized text. OOP max patterns are written to search forward from "In-network:" to find "out-of-pocket", not backward.

## Known Issues / Gotchas

**SSL on Windows**: The machine uses a corporate certificate store. All httpx calls must use `verify=_ssl_ctx` from `ingestors/core/http.py`. Never use bare `httpx.Client()` without this.

**Hash cache skip**: If an ingestor runs and records 0 rows as "success", re-running will skip parsing (hash matches). Fix by deleting the release record with `fix_release.py` or deleting the cached file.

**Schema migrations**: `CREATE TABLE IF NOT EXISTS` won't add new columns to existing tables. New columns require an `ALTER TABLE` in `get_conn()` wrapped in try/except (see `content_type` column as the pattern).

**a03_hcpcs / a09_modifiers**: CMS has published HCPCS quarterly-only since April 2020 — no separate annual file exists. Modifiers are in the same sheet as procedure codes, distinguished by `RECID=7` (procedures are `RECID=3`). a09 reads from a03's cached ZIP directly.

**ICD-10-PCS**: CMS's `2025-icd-10-pcs-code-tables-and-index.zip` contains XML, not a flat order file. The ingestor generates all ~79k codes by enumerating Cartesian products of axis values across `pcsRow` elements in `icd10pcs_tables_2025.xml`.

**QHP Landscape / SADP files**: `data.healthcare.gov` files have a metadata row at index 0 and real headers at index 1. Ingestors skip row 0 explicitly.

**Re-parsing SBC fields**: To force f03 to re-parse already-processed PDFs, run `clear_sbc_fields.py` first (deletes `sbc_fields` and resets `sbc_documents.extraction_method`).

## Source Status

Sources with empty `canonical_url` in `source_registry.yaml` still need a working download URL found manually. Check the registry comments for context on each blocked source.
