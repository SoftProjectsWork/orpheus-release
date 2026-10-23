-- ORPHEUS migration 0001 — core schema (SQLite, STRICT tables)
-- Rewritten for SQLite on 2026-10-09 (ADR-0005) before any permanent database existed.
-- Conventions:
--   * Primary keys are app-generated prefixed ULIDs (src/orpheus/ids.py).
--   * Timestamps are UTC ISO-8601 TEXT ending in 'Z'; dates are 'YYYY-MM-DD' TEXT.
--   * Booleans are INTEGER 0/1; JSON is TEXT checked with json_valid().
--   * acquired_at (when ORPHEUS got a fact) is distinct from the scientific/editorial date.
--   * Facts from external sources link to the source_snapshot they came from.
-- Foreign keys require PRAGMA foreign_keys = ON on every connection (done in orpheus.db.connect).

-- ===================== Project & sources =====================

CREATE TABLE project (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE research_domain (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES project(id),
    name             TEXT NOT NULL,
    description      TEXT,
    controlled_terms TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(controlled_terms)),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, name)
) STRICT;

CREATE TABLE source (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('metadata','fulltext','bibliometric','editorial','registry','local')),
    acquisition TEXT NOT NULL CHECK (acquisition IN ('api','manual','local_files')),
    base_url    TEXT,
    auth        TEXT NOT NULL CHECK (auth IN ('none','api_key','institutional','licence')),
    terms_url   TEXT,
    notes       TEXT
) STRICT;

CREATE TABLE source_snapshot (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source(id),
    request_uri     TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    http_status     INTEGER,
    content_sha256  TEXT NOT NULL CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    content_type    TEXT,
    storage_path    TEXT,
    size_bytes      INTEGER CHECK (size_bytes >= 0),
    etag            TEXT,
    last_modified   TEXT,
    licence         TEXT,
    storage_allowed INTEGER NOT NULL DEFAULT 0 CHECK (storage_allowed IN (0,1)),
    UNIQUE (source_id, request_uri, content_sha256)
) STRICT;

-- ===================== Journals =====================

CREATE TABLE journal (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    publisher     TEXT,
    homepage_url  TEXT,
    status        TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('active','ceased','unknown')),
    openalex_id   TEXT UNIQUE,
    completeness  REAL CHECK (completeness BETWEEN 0 AND 1),
    next_check_at TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE journal_issn (
    issn               TEXT PRIMARY KEY CHECK (issn GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9X]'),
    journal_id         TEXT NOT NULL REFERENCES journal(id),
    issn_type          TEXT NOT NULL CHECK (issn_type IN ('print','electronic','linking','unknown')),
    source_snapshot_id TEXT REFERENCES source_snapshot(id),
    verified_at        TEXT
) STRICT;

CREATE TABLE journal_policy_version (
    id                 TEXT PRIMARY KEY,
    journal_id         TEXT NOT NULL REFERENCES journal(id),
    policy_type        TEXT NOT NULL CHECK (policy_type IN (
                         'author_guidelines','scope','article_types','apc','open_access',
                         'ai_policy','data_policy','peer_review','ethics','reporting_guidelines','other')),
    content_sha256     TEXT NOT NULL CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    detected_at        TEXT NOT NULL,
    effective_date     TEXT,
    summary            TEXT,
    supersedes_id      TEXT REFERENCES journal_policy_version(id),
    importance         TEXT CHECK (importance IN ('low','medium','high')),
    needs_human_review INTEGER NOT NULL DEFAULT 0 CHECK (needs_human_review IN (0,1)),
    UNIQUE (journal_id, policy_type, content_sha256)
) STRICT;

CREATE TABLE journal_metric (
    id                 TEXT PRIMARY KEY,
    journal_id         TEXT NOT NULL REFERENCES journal(id),
    metric             TEXT NOT NULL CHECK (metric IN (
                         'JIF','JIF_5Y','CiteScore','SJR','SNIP','quartile','h_index',
                         'two_year_mean_citedness','other')),
    value              REAL,
    value_text         TEXT,
    reference_year     INTEGER NOT NULL CHECK (reference_year BETWEEN 1950 AND 2100),
    subject_category   TEXT NOT NULL DEFAULT '',
    provider           TEXT NOT NULL,
    is_official        INTEGER NOT NULL CHECK (is_official IN (0,1)),
    definition_url     TEXT,
    retrieved_at       TEXT NOT NULL,
    source_snapshot_id TEXT REFERENCES source_snapshot(id),
    CHECK (value IS NOT NULL OR value_text IS NOT NULL),
    CHECK (metric <> 'quartile' OR value_text IN ('Q1','Q2','Q3','Q4')),
    UNIQUE (journal_id, metric, reference_year, provider, subject_category)
) STRICT;

-- ===================== Articles & authors =====================

CREATE TABLE article (
    id                   TEXT PRIMARY KEY,
    doi                  TEXT UNIQUE CHECK (doi = lower(doi) AND doi GLOB '10.[0-9][0-9][0-9][0-9]*/?*' AND doi NOT GLOB '*[ 	]*'),
    pmid                 TEXT UNIQUE CHECK (length(pmid) BETWEEN 1 AND 9 AND pmid NOT GLOB '*[^0-9]*' AND pmid NOT GLOB '0*'),
    pmcid                TEXT UNIQUE CHECK (pmcid GLOB 'PMC[1-9]*' AND substr(pmcid, 4) NOT GLOB '*[^0-9]*'),
    openalex_id          TEXT UNIQUE,
    title                TEXT NOT NULL,
    abstract             TEXT,
    publication_date     TEXT,
    publication_year     INTEGER CHECK (publication_year BETWEEN 1600 AND 2100),
    journal_id           TEXT REFERENCES journal(id),
    article_type         TEXT,
    language             TEXT,
    access_level         TEXT NOT NULL CHECK (access_level IN ('full_text','abstract_only','metadata_only','not_accessible')),
    licence              TEXT,
    integrity_status     TEXT NOT NULL DEFAULT 'unknown' CHECK (integrity_status IN (
                           'none','corrected','expression_of_concern','retracted','unknown')),
    integrity_checked_at TEXT,
    analysis_level       TEXT NOT NULL DEFAULT 'none' CHECK (analysis_level IN (
                           'none','metadata','abstract','full_text','full_text_verified')),
    acquired_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (doi IS NOT NULL OR pmid IS NOT NULL OR pmcid IS NOT NULL OR openalex_id IS NOT NULL)
) STRICT;

CREATE TABLE article_version (
    id                 TEXT PRIMARY KEY,
    article_id         TEXT NOT NULL REFERENCES article(id),
    version_type       TEXT NOT NULL CHECK (version_type IN (
                         'preprint','accepted_manuscript','published','correction','retraction_notice','expression_of_concern')),
    doi                TEXT CHECK (doi IS NULL OR doi = lower(doi)),
    event_date         TEXT,
    source_snapshot_id TEXT REFERENCES source_snapshot(id),
    recorded_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE author (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    orcid        TEXT UNIQUE CHECK (orcid GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9X]'),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE article_author (
    article_id       TEXT NOT NULL REFERENCES article(id),
    position         INTEGER NOT NULL CHECK (position >= 1),
    author_id        TEXT NOT NULL REFERENCES author(id),
    raw_affiliation  TEXT,
    is_corresponding INTEGER CHECK (is_corresponding IN (0,1)),
    PRIMARY KEY (article_id, position)
) STRICT;

CREATE TABLE citation (
    id                  TEXT PRIMARY KEY,
    citing_article_id   TEXT NOT NULL REFERENCES article(id),
    cited_article_id    TEXT REFERENCES article(id),
    cited_doi           TEXT CHECK (cited_doi IS NULL OR cited_doi = lower(cited_doi)),
    cited_raw           TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
                          'verified','unverified','not_found','mismatch')),
    verified_at         TEXT,
    verification_source TEXT REFERENCES source(id),
    source_snapshot_id  TEXT REFERENCES source_snapshot(id),
    CHECK (cited_article_id IS NOT NULL OR cited_doi IS NOT NULL OR cited_raw IS NOT NULL),
    CHECK (verification_status <> 'verified' OR (verified_at IS NOT NULL AND verification_source IS NOT NULL))
) STRICT;

-- ===================== Search provenance =====================

CREATE TABLE search_strategy (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES project(id),
    name               TEXT NOT NULL,
    source_id          TEXT NOT NULL REFERENCES source(id),
    query              TEXT NOT NULL,
    filters            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(filters)),
    inclusion_criteria TEXT,
    exclusion_criteria TEXT,
    version            INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (project_id, name, version)
) STRICT;

CREATE TABLE search_run (
    id                 TEXT PRIMARY KEY,
    strategy_id        TEXT NOT NULL REFERENCES search_strategy(id),
    executed_at        TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('ok','partial','failed')),
    result_count       INTEGER CHECK (result_count >= 0),
    retrieved_count    INTEGER CHECK (retrieved_count >= 0),
    limitations        TEXT,
    source_snapshot_id TEXT REFERENCES source_snapshot(id)
) STRICT;

CREATE TABLE search_result (
    run_id         TEXT NOT NULL REFERENCES search_run(id),
    rank           INTEGER NOT NULL CHECK (rank >= 1),
    article_id     TEXT REFERENCES article(id),
    raw_identifier TEXT NOT NULL,
    PRIMARY KEY (run_id, rank)
) STRICT;

-- ===================== Claims & evidence =====================

CREATE TABLE scientific_claim (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    statement  TEXT NOT NULL,
    claim_type TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by TEXT NOT NULL
) STRICT;

CREATE TABLE evidence_record (
    id                TEXT PRIMARY KEY,
    claim_id          TEXT NOT NULL REFERENCES scientific_claim(id),
    article_id        TEXT NOT NULL REFERENCES article(id),
    location          TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(location)),
    support           TEXT NOT NULL CHECK (support IN ('supports','contradicts','mentions','unclear')),
    origin            TEXT NOT NULL CHECK (origin IN ('reported','recalculated','interpreted','not_available')),
    extracted_by_run  TEXT,
    verified_by_human INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_human IN (0,1)),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- ===================== Orchestration =====================

CREATE TABLE agent (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    layer            TEXT NOT NULL CHECK (layer IN ('A','B','C','D','E','F','G','H')),
    role             TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','implemented','tested','active','retired')),
    config           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(config))
) STRICT;

CREATE TABLE model (
    id          TEXT PRIMARY KEY,
    executor    TEXT NOT NULL CHECK (executor IN ('claude_session','local_llm')),
    model_ref   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('candidate','verified','deprecated')),
    verified_at TEXT,
    UNIQUE (executor, model_ref)
) STRICT;

CREATE TABLE task (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES project(id),
    title               TEXT NOT NULL,
    priority            INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 5),
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                          'pending','ready','running','blocked','awaiting_approval','done','failed','cancelled')),
    owner_agent_id      TEXT REFERENCES agent(id),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    due_at              TEXT,
    expected_output     TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    idempotency_key     TEXT UNIQUE,
    schedule            TEXT
) STRICT;

CREATE TABLE task_dependency (
    task_id            TEXT NOT NULL REFERENCES task(id),
    depends_on_task_id TEXT NOT NULL REFERENCES task(id),
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
) STRICT;

CREATE TABLE workflow_run (
    id                TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES task(id),
    agent_id          TEXT REFERENCES agent(id),
    executor          TEXT NOT NULL CHECK (executor IN ('deterministic','claude_session','local_llm')),
    model_id          TEXT REFERENCES model(id),
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','timeout','cancelled')),
    parameters        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(parameters)),
    error             TEXT,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (validation_status IN ('pending','pass','fail','needs_human_review')),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (executor = 'deterministic' OR model_id IS NOT NULL)
) STRICT;

CREATE TABLE validation_result (
    id              TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_run(id),
    check_name      TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('pass','fail','needs_human_review')),
    validator       TEXT NOT NULL CHECK (validator IN ('deterministic','llm','human')),
    details         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE alert (
    id              TEXT PRIMARY KEY,
    severity        TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    category        TEXT NOT NULL,
    message         TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    acknowledged_at TEXT,
    acknowledged_by TEXT
) STRICT;

-- ===================== Audit log (append-only, hash-chained) =====================

CREATE TABLE audit_event (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    payload     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    prev_hash   TEXT NOT NULL CHECK (length(prev_hash) = 64),
    event_hash  TEXT NOT NULL UNIQUE CHECK (length(event_hash) = 64)
) STRICT;

CREATE TRIGGER audit_event_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only (UPDATE not allowed)'); END;

CREATE TRIGGER audit_event_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only (DELETE not allowed)'); END;

-- ===================== Full-text search on articles =====================

CREATE VIRTUAL TABLE article_fts USING fts5(
    title, abstract, content='article', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');

CREATE TRIGGER article_fts_ai AFTER INSERT ON article BEGIN
    INSERT INTO article_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
END;
CREATE TRIGGER article_fts_ad AFTER DELETE ON article BEGIN
    INSERT INTO article_fts(article_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract);
END;
CREATE TRIGGER article_fts_au AFTER UPDATE OF title, abstract ON article BEGIN
    INSERT INTO article_fts(article_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract);
    INSERT INTO article_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
END;

-- ===================== Indexes =====================

CREATE INDEX article_journal_year_idx ON article (journal_id, publication_year);
CREATE INDEX citation_citing_idx ON citation (citing_article_id);
CREATE INDEX citation_cited_idx ON citation (cited_article_id);
CREATE INDEX evidence_claim_idx ON evidence_record (claim_id);
CREATE INDEX policy_journal_type_idx ON journal_policy_version (journal_id, policy_type, detected_at);
CREATE INDEX metric_journal_idx ON journal_metric (journal_id, metric, reference_year);
CREATE INDEX task_status_idx ON task (status, priority);
CREATE INDEX snapshot_source_time_idx ON source_snapshot (source_id, retrieved_at);

-- ===================== Table descriptions (SQLite has no COMMENT ON) =====================

CREATE TABLE schema_doc (
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    PRIMARY KEY (table_name, column_name)
) STRICT;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('project','', 'An ORPHEUS project (normally one per research group/line).'),
 ('research_domain','', 'Scientific area covered by a project, with controlled vocabulary (e.g. MeSH terms).'),
 ('source','', 'External or local data source (Crossref, PubMed, local PDF folder, manual import, ...).'),
 ('source','acquisition', 'api = free public API; manual = exported by the user (e.g. institutional access); local_files = files on the PC.'),
 ('source_snapshot','', 'Immutable record of one raw response or file obtained from a source; the provenance anchor for every acquired fact.'),
 ('source_snapshot','storage_allowed', '1 only if licence/terms allow keeping the raw content; otherwise only hash and metadata are kept.'),
 ('journal','completeness', 'Share of required profile fields filled from verified sources (0-1).'),
 ('journal_issn','', 'ISSNs of a journal; check digit is validated in application code before insert.'),
 ('journal_policy_version','', 'Versioned editorial policy text; a new row is created only when the content hash changes.'),
 ('journal_policy_version','detected_at', 'When ORPHEUS detected this version (acquisition date).'),
 ('journal_policy_version','effective_date', 'Effective date declared by the journal, if any (editorial date).'),
 ('journal_metric','', 'One bibliometric indicator value with provider, reference year and acquisition date. Estimates are never stored as official values.'),
 ('journal_metric','is_official', '1 only when obtained from the indicator owner (e.g. JIF from Clarivate JCR).'),
 ('article','access_level', 'What ORPHEUS can legally access: full text, abstract only, metadata only, or nothing.'),
 ('article','analysis_level', 'How deeply ORPHEUS has actually analysed the article; never inferred from access_level.'),
 ('article','acquired_at', 'When ORPHEUS first recorded the article (distinct from publication_date).'),
 ('article_version','', 'Preprints, published versions, corrections and retraction notices linked to one work.'),
 ('citation','verification_status', 'A reference is verified only after a deterministic match against an authoritative source; otherwise it stays unverified (NON VERIFICATA).'),
 ('search_strategy','', 'Reproducible search definition; edits create a new version, old versions are kept.'),
 ('evidence_record','location', 'Position in the source: {"document_id":..,"page":..,"char_start":..,"char_end":..,"quote_sha256":..}.'),
 ('evidence_record','origin', 'reported = stated by the authors; recalculated = computed by ORPHEUS; interpreted = agent interpretation; not_available.'),
 ('model','executor', 'claude_session = Claude in an interactive session (user subscription); local_llm = a free model running on the PC. No paid APIs (ADR-0006).'),
 ('task','acceptance_criteria', 'A task is done only when these criteria are met and validated, never because an agent returned text.'),
 ('workflow_run','executor', 'deterministic = code; claude_session / local_llm = language model, whose output must pass the task validators.'),
 ('audit_event','', 'Append-only log; each event hash covers the previous hash, so any later edit breaks the chain (orpheus audit-verify).');
