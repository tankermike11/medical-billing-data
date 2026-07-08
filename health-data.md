  SAM Health Data Repository — Full Review

  What This Repo Is
  
  This is the data backbone for SAM, an AI-powered health advocacy assistant built by MASA. The repo contains a Python ingestion pipeline (sam-content-ingest) that pulls publicly available, legally-clean federal health content from authoritative US
  government sources and normalises it into a unified format — KnowledgeBlock — which SAM uses as its evidence layer when answering health questions.

  ---
  Content Block Inventory (Confirmed Counts)

  ┌─────────────────────┬───────────────────────────┬─────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────┬───────────────────────────┐
  │      Use Case       │           File            │                   Block Count                   │                       Primary Source(s)                       │          License          │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Travel Health       │ travel_health.jsonl       │ ~4,800 (est. from PRD; 15.7 MB file)            │ CDC Travelers' Health (~200 country pages, ~14 sections each) │ us_gov                    │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Household Readiness │ household_readiness.jsonl │ 62                                              │ Ready.gov / FEMA                                              │ us_gov, ready_gov_reprint │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Condition Explainer │ condition_explainer.jsonl │ 40                                              │ MedlinePlus (NLM)                                             │ medlineplus_terms         │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Medication          │ medication.jsonl          │ 25+ (file is 4.81 MB; GitHub truncated display) │ DailyMed (NLM/FDA)                                            │ public_domain             │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Aging / Home Safety │ aging_home_safety.jsonl   │ 14                                              │ CDC (falls prevention) + MedlinePlus                          │ us_gov, medlineplus_terms │
  ├─────────────────────┼───────────────────────────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┼───────────────────────────┤
  │ Visit Prep          │ visit_prep.jsonl          │ 2                                               │ AHRQ + MedlinePlus                                            │ us_gov, medlineplus_terms │
  └─────────────────────┴───────────────────────────┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────┴───────────────────────────┘

  Estimated total: ~4,943+ blocks across 6 use cases.

  Travel Health is by far the dominant corpus. The other five use cases are comparatively thin, which the PRD explicitly flags as a "richness gap" that licensed sources (e.g. Mayo Clinic) are intended to fill later.

  ---
  Breakdown by Use Case

  1. Travel Health (~4,800 blocks) — travel_health.jsonl

  The largest dataset by a wide margin. Covers ~200 CDC Traveler's Health destination pages, with each country generating up to 14–15 standardised section blocks:

  - After your trip
  - Avoid sharing body fluids
  - Eat and drink safely
  - Healthy travel packing list
  - Keep away from animals
  - Know how to get medical care while travelling
  - Maintain personal security
  - Non-vaccine-preventable diseases
  - Prevent bug bites
  - Reduce exposure to germs
  - Select safe transportation
  - Stay healthy and safe
  - Stay safe outdoors
  - Travel health notices
  - Vaccines and medicines

  Countries confirmed in the data include: Afghanistan, Albania, Algeria, American Samoa, Andorra, Angola, Anguilla, Antarctica, Antigua and Barbuda, Argentina — and continuing through the full global alphabet. Note: many "generic" sections (e.g. "Eat and  
  drink safely", "Keep away from animals") share identical content hashes across countries, meaning the raw block count overstates unique content. Truly unique, destination-specific blocks are concentrated in vaccine recommendations, disease notices, and   
  packing lists.

  ---
  2. Household Readiness (62 blocks) — household_readiness.jsonl

  Well-structured, complete set from Ready.gov / FEMA covering:

  - Disaster supply kit: basics, additional supplies, storage locations, maintenance
  - Evacuation: before/during/after, go-bags, routes, risks
  - Extreme heat: preparation, cooling strategies, recognising heat illness
  - Financial preparedness: document organisation, emergency funds
  - Floods: risk, warnings, safety during and after, insurance
  - Hurricanes: preparation, before/during/after protocols (29 blocks alone)
  - Family communication plan (reprint-licensed card)

  This is the most polished and complete use case — described in the PRD review as "Phase 1" because it proved the full pipeline end-to-end.

  ---
  3. Condition Explainer (40 blocks) — condition_explainer.jsonl

  All sourced from MedlinePlus (US National Library of Medicine). Each block is an overview section for a common health condition. Conditions confirmed in the data include:

  Acute Bronchitis, Alzheimer's Disease, Anemia, Anxiety, Arthritis, Asthma, COPD, Depression, Diabetes, Flu — and 30 more common diagnoses.

  Each block contains a full plain-language summary, keywords, and structured metadata. Currently only overview sections are captured (single block per condition); the PRD had envisioned subsections like Symptoms and When-to-See-a-Doctor, but MedlinePlus   
  delivers a single narrative HTML block, not discrete subsections.

  ---
  4. Medication (25+ blocks) — medication.jsonl

  Sourced from DailyMed (FDA drug label database). The file is 4.81 MB so the actual block count is almost certainly far higher than the 25 visible in the GitHub preview. Confirmed drugs include:

  - Acetaminophen (Tylenol Extra Strength)
  - Albuterol
  - Allopurinol
  - Alprazolam

  Each drug label is broken into LOINC-coded sections (e.g. indications, dosage, warnings, patient counselling), making this the most structurally rich dataset. All content is public_domain via NLM.

  Known limitation: The PRD review flagged that no policy exists for which SPL record to select when DailyMed returns multiple records per drug (brand vs. generic, multiple manufacturers). This means the current selection may be inconsistent.

  ---
  5. Aging / Home Safety (14 blocks) — aging_home_safety.jsonl

  A small but useful set split across two sources:

  - CDC (8 blocks): falls prevention strategies — talks to doctors, balance/strength exercises, osteoporosis screening, vision checks, home modifications (grab bars, lighting)
  - MedlinePlus (6 blocks): caregiver guidance for Alzheimer's patients, assistive devices, mobility aids, fall causes (medication side effects, balance disorders, vision problems)

  All classified as "evergreen", targeting patient and caregiver audiences.

  ---
  6. Visit Prep (2 blocks) — visit_prep.jsonl

  The thinnest use case — only 2 blocks:

  1. AHRQ "10 Questions You Should Know" for medical appointments
  2. MedlinePlus guidance on preparing for in-person and telehealth visits

  This thinness is a known and documented issue. The PRD review found AHRQ's content inaccessible to crawling (CloudFront + robots.txt blocking), and the "10 Questions" interactive feature isn't a static page. The two blocks present are the maximum
  extractable from publicly available static content. The PRD recommends this use case be explicitly flagged as content-thin until a licensed structured source is added.

  ---
  Schema: What Every Block Contains

  Each KnowledgeBlock record is stored as a single line of JSON (JSONL format) with these fields:

  ┌────────────────────────────────┬─────────────────────────────────────────────────────────────────┐
  │             Field              │                           Description                           │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ id                             │ Stable source-prefixed ID (e.g. medlineplus:diabetes:overview)  │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ use_case                       │ One of the 6 use cases                                          │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ title                          │ Human-readable title                                            │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ body                           │ Plain-language markdown content                                 │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ source                         │ Source identifier (ready_gov, medlineplus, cdc, dailymed, ahrq) │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ source_url                     │ Direct URL to original content                                  │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ citation                       │ Attribution text per source's terms                             │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ license                        │ us_gov, medlineplus_terms, public_domain, or ready_gov_reprint  │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ content_hash                   │ SHA-256 for deduplication and change detection                  │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ ingested_at                    │ Timestamp of ingestion                                          │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ icd10 / snomed / rxcui / loinc │ Medical codes where available                                   │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ volatility                     │ evergreen, periodic, or volatile (travel blocks)                │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ language                       │ Language (defaults to en)                                       │
  └────────────────────────────────┴─────────────────────────────────────────────────────────────────┘

  Travel health blocks additionally carry geo (ISO country codes), trip_types, and valid_until.

  ---
  Source Health Summary

  ┌───────────────────────────────┬─────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │            Source             │   Pipeline Status   │                                                  Notes                                                   │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ MedlinePlus                   │ ✅ Working          │ Daily XML bulk feed; dynamic URL resolution needed for date-stamped filenames                            │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ DailyMed (FDA)                │ ✅ Working          │ Minor fix: param is drug_name not drugname; full text needs XML extraction                               │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Ready.gov / FEMA              │ ✅ Working          │ 3 URL slugs need correction; content frozen as of Sept 2025                                              │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ CDC Travelers' Health         │ ✅ Working (travel) │ Successfully generating the ~4,800 block corpus                                                          │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ CDC Syndication (STEADI etc.) │ ❌ Broken           │ Migrated to HHS platform; aging_home_safety CDC blocks appear to have been captured before the migration │
  ├───────────────────────────────┼─────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ AHRQ                          │ ❌ Broken           │ Blocks all crawlers; only 1 static page capturable manually                                              │
  └───────────────────────────────┴─────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  Gaps and What's Missing

  1. Visit Prep is critically thin (2 blocks) — needs a licensed structured question-bank source
  2. Condition Explainer covers ~40 conditions; a full clinical library (e.g. Mayo) could expand this to thousands
  3. Medication block count is unclear due to GitHub file size limits — the true count from DailyMed is likely in the hundreds given the 4.81 MB file size
  4. No Spanish content — three sources include it (MedlinePlus, CDC, Ready.gov) but the pipeline currently defaults to English-only
  5. Travel Health deduplication: many of the ~4,800 blocks share identical body content across countries; unique content is a smaller subset

  ---
  Bottom Line

  The repository holds a functional, legally-clean health knowledge corpus dominated by travel health content (~4,800 blocks) with a solid household readiness dataset (62 blocks) and smaller but useful condition and medication libraries. The architecture is
   well-designed with proper citations, licensing metadata, and content hashing throughout. The two most pressing production gaps are the thin Visit Prep use case and the need for a licensed clinical content partner (Mayo Clinic is the planned next step) to
   meaningfully expand condition and medication depth.