-- ORPHEUS migration 0014 — EURYDIKE: systematic reviews and meta-analysis (T-051c, ADR-0012)
-- APPROVED by the user on 2026-10-19 20:12 (Europe/Rome), manual session, revision 2 (unified card cards-v2): "Approvo".
-- PROPOSAL shown to the user on 2026-10-19 (as EURYDIKE_MIGRAZIONE_0014.sql). Schema approved on 2026-10-19 16:16
-- ("Approvo tutte le decisioni prese", text in EURYDIKE_MIGRAZIONE_0014_PROPOSTA.md). When the user approves THIS text it
-- becomes migrations/0014_eurydike.sql, is tried on a copy of the real database (gate E2) and installed by
-- installa_orpheus.cmd with a backup first.
-- REVISION 2 (2026-10-19 20:30): the user approved revision 1 at 20:04 and in the same message asked for ONE card per
-- article usable by ORPHEUS and EURYDIKE ("scheda unica a due livelli", answer "Sì, come proposta"; "Riscrivila con la
-- scheda unica"). Changes: + card_item and card_outcome_row (the unified article card cards-v2, with states and
-- corrections written by the user); form_field.card_key (a field taken from the card is never extracted again);
-- extraction_value only for fields of the review alone; ma_outcome_row removed (outcome rows live in the card);
-- ma_input points to card_outcome_row. 18 tables. To be approved again on THIS text.
--
-- Only additions: no existing table is changed. ORPHEUS conventions: STRICT tables, prefixed ULIDs, ISO-8601 UTC,
-- CHECK for enumerations, json_valid for JSON, schema_doc for every table, no deletions (triggers), a new row replaces
-- an old one (superseded_by), one current row per key (partial UNIQUE indexes). Derived numbers (PRISMA counts, kappa,
-- scores, conversions, effects) are NEVER stored: they are computed by eurydike/core from these rows.

-- ------------------------------------------------------------------ the unified article card (cards-v2)
-- One card per article, shared by ORPHEUS (search, claims, writing) and EURYDIKE (extraction, meta-analysis). The
-- cards-v1 cards (article_card, card_value) are not changed: their values are copied here by code (from_card_id, from_ord).
CREATE TABLE card_item (
    id              TEXT PRIMARY KEY,                                      -- cdi_
    article_id      TEXT NOT NULL REFERENCES article(id),
    card_version    TEXT NOT NULL DEFAULT 'cards-v2',
    field_key       TEXT NOT NULL CHECK (field_key GLOB '[a-z]*' AND field_key NOT GLOB '*[^a-z0-9_]*'),
    item_ord        INTEGER NOT NULL DEFAULT 1 CHECK (item_ord >= 1),
    name            TEXT,
    value           TEXT,
    unit            TEXT,
    group_label     TEXT,
    document_id     TEXT REFERENCES document(id),
    jats_snapshot_id TEXT REFERENCES source_snapshot(id),
    quote           TEXT,
    page_no         INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord     INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    quote_check     TEXT NOT NULL DEFAULT 'none' CHECK (quote_check IN ('exact','fuzzy','not_found','none')),
    confidence      TEXT CHECK (confidence IS NULL OR confidence IN ('high','medium','low')),
    note            TEXT,
    state           TEXT NOT NULL CHECK (state IN ('ai','confirmed','edited','nr')),
    actor           TEXT NOT NULL CHECK (actor IN ('human','claude_session','chat_manuale')),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    from_card_id    TEXT,
    from_ord        INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES card_item(id),
    FOREIGN KEY (from_card_id, from_ord) REFERENCES card_value(card_id, ord),
    CHECK ((state = 'ai') = (actor <> 'human')),
    CHECK (state <> 'ai' OR workflow_run_id IS NOT NULL),
    CHECK (state <> 'nr' OR (value IS NULL AND quote IS NULL)),
    CHECK (state NOT IN ('ai','confirmed') OR value IS NOT NULL),
    CHECK (quote IS NULL OR ((page_no IS NULL) <> (section_ord IS NULL))),
    CHECK (page_no IS NULL OR document_id IS NOT NULL),
    CHECK (section_ord IS NULL OR jats_snapshot_id IS NOT NULL),
    CHECK (quote IS NOT NULL OR quote_check = 'none'),
    CHECK ((from_card_id IS NULL) = (from_ord IS NULL))
) STRICT;
CREATE UNIQUE INDEX card_item_one_current ON card_item (article_id, field_key, item_ord) WHERE superseded_by IS NULL;
CREATE INDEX card_item_field_idx ON card_item (field_key);

CREATE TABLE card_outcome_row (
    id              TEXT PRIMARY KEY,                                      -- cor_
    article_id      TEXT NOT NULL REFERENCES article(id),
    outcome         TEXT NOT NULL,
    group_label     TEXT NOT NULL,
    timepoint       TEXT NOT NULL,
    unit            TEXT,
    n TEXT, mean TEXT, sd TEXT, median TEXT, q1 TEXT, q3 TEXT, min TEXT, max TEXT, se TEXT, ci_low TEXT, ci_high TEXT,
    document_id     TEXT REFERENCES document(id),
    jats_snapshot_id TEXT REFERENCES source_snapshot(id),
    quote           TEXT,
    page_no         INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord     INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    table_label     TEXT,
    quote_check     TEXT NOT NULL DEFAULT 'none' CHECK (quote_check IN ('exact','fuzzy','not_found','none')),
    note            TEXT,
    state           TEXT NOT NULL CHECK (state IN ('ai','confirmed','edited')),
    actor           TEXT NOT NULL CHECK (actor IN ('human','claude_session','chat_manuale')),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES card_outcome_row(id),
    CHECK ((state = 'ai') = (actor <> 'human')),
    CHECK (state <> 'ai' OR (workflow_run_id IS NOT NULL AND quote IS NOT NULL)),
    CHECK (quote IS NULL OR ((page_no IS NULL) <> (section_ord IS NULL))),
    CHECK (page_no IS NULL OR document_id IS NOT NULL),
    CHECK (section_ord IS NULL OR jats_snapshot_id IS NOT NULL),
    CHECK (quote IS NOT NULL OR quote_check = 'none')
) STRICT;
CREATE UNIQUE INDEX card_outcome_row_one_current ON card_outcome_row (article_id, outcome, group_label, timepoint)
    WHERE superseded_by IS NULL;

-- ------------------------------------------------------------------ the review and its protocol
CREATE TABLE review (
    id                 TEXT PRIMARY KEY,                                   -- rev_
    title              TEXT NOT NULL,
    question           TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('systematic_review','meta_analysis','scoping_review')),
    pico               TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(pico)),
    registration       TEXT,
    search_strategy    TEXT,
    screen_reviewers   INTEGER NOT NULL DEFAULT 2 CHECK (screen_reviewers >= 1),
    blind_reviewers    INTEGER NOT NULL DEFAULT 1 CHECK (blind_reviewers IN (0,1)),
    blind_ai           INTEGER NOT NULL DEFAULT 1 CHECK (blind_ai IN (0,1)),
    prior_identified   INTEGER CHECK (prior_identified IS NULL OR prior_identified >= 0),
    prior_dupes        INTEGER CHECK (prior_dupes IS NULL OR prior_dupes >= 0),
    rob_tool_default   TEXT,
    qual_tool_default  TEXT,
    status             TEXT NOT NULL DEFAULT 'protocol'
                       CHECK (status IN ('protocol','screening','fulltext','extraction','analysis','closed')),
    data_dir           TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE review_criterion (
    id            TEXT PRIMARY KEY,                                        -- rcr_
    review_id     TEXT NOT NULL REFERENCES review(id),
    kind          TEXT NOT NULL CHECK (kind IN ('inclusion','exclusion','prefilter')),
    ord           INTEGER NOT NULL CHECK (ord >= 1),
    text          TEXT NOT NULL,
    rule_json     TEXT CHECK (rule_json IS NULL OR json_valid(rule_json)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by TEXT REFERENCES review_criterion(id),
    CHECK (kind = 'prefilter' OR rule_json IS NULL)
) STRICT;

CREATE TABLE review_reviewer (
    id         TEXT PRIMARY KEY,                                           -- rvw_
    review_id  TEXT NOT NULL REFERENCES review(id),
    kind       TEXT NOT NULL CHECK (kind IN ('human','claude_session','chat_manuale','consensus')),
    label      TEXT NOT NULL,
    model_ref  TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (review_id, label),
    CHECK (kind IN ('human','consensus') OR model_ref IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX review_reviewer_one_consensus ON review_reviewer (review_id) WHERE kind = 'consensus';

CREATE TABLE review_log (
    id          TEXT PRIMARY KEY,                                          -- rlg_
    review_id   TEXT NOT NULL REFERENCES review(id),
    reviewer_id TEXT NOT NULL REFERENCES review_reviewer(id),
    kind        TEXT NOT NULL CHECK (kind IN ('deviation','bulk','rule','pdf','note','prefilter','analysis')),
    title       TEXT NOT NULL,
    why         TEXT,
    n_records   INTEGER CHECK (n_records IS NULL OR n_records >= 0),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- ------------------------------------------------------------------ search, records, screening
CREATE TABLE search_import (
    id            TEXT PRIMARY KEY,                                        -- imp_
    review_id     TEXT NOT NULL REFERENCES review(id),
    source_name   TEXT NOT NULL,
    database_name TEXT NOT NULL,
    format        TEXT NOT NULL CHECK (format IN ('ris','nbib','wos','csv','tsv')),
    file_sha256   TEXT NOT NULL CHECK (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    searched_on   TEXT,
    raw_count     INTEGER NOT NULL CHECK (raw_count >= 0),
    dupes_count   INTEGER NOT NULL CHECK (dupes_count >= 0 AND dupes_count <= raw_count),
    imported_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (review_id, file_sha256)
) STRICT;

CREATE TABLE review_record (
    id                     TEXT PRIMARY KEY,                               -- rrc_
    review_id              TEXT NOT NULL REFERENCES review(id),
    import_id              TEXT NOT NULL REFERENCES search_import(id),
    title                  TEXT NOT NULL,
    abstract               TEXT,
    authors                TEXT,
    year                   TEXT,
    journal                TEXT,
    doi                    TEXT CHECK (doi IS NULL OR (doi = lower(doi) AND doi GLOB '10.*/*')),
    pmid                   TEXT CHECK (pmid IS NULL OR pmid NOT GLOB '*[^0-9]*'),
    dedup_key              TEXT NOT NULL,
    duplicate_of           TEXT REFERENCES review_record(id),
    prefilter_criterion_id TEXT REFERENCES review_criterion(id),
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX review_record_one_key ON review_record (review_id, dedup_key) WHERE duplicate_of IS NULL;
CREATE INDEX review_record_review_idx ON review_record (review_id);

CREATE TABLE screening_decision (
    id              TEXT PRIMARY KEY,                                      -- sdc_
    record_id       TEXT NOT NULL REFERENCES review_record(id),
    reviewer_id     TEXT NOT NULL REFERENCES review_reviewer(id),
    decision        TEXT NOT NULL CHECK (decision IN ('include','exclude','maybe')),
    criterion_id    TEXT REFERENCES review_criterion(id),
    rationale       TEXT,
    confidence      TEXT CHECK (confidence IS NULL OR confidence IN ('high','medium','low')),
    origin          TEXT NOT NULL CHECK (origin IN ('manual','bulk','rule','model')),
    rule_log_id     TEXT REFERENCES review_log(id),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES screening_decision(id),
    CHECK (decision <> 'exclude' OR criterion_id IS NOT NULL),
    CHECK (origin <> 'model' OR workflow_run_id IS NOT NULL),
    CHECK (origin NOT IN ('rule','bulk') OR rule_log_id IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX screening_decision_one_current ON screening_decision (record_id, reviewer_id)
    WHERE superseded_by IS NULL;

-- ------------------------------------------------------------------ studies read in full
CREATE TABLE review_study (
    id              TEXT PRIMARY KEY,                                      -- rst_
    review_id       TEXT NOT NULL REFERENCES review(id),
    record_id       TEXT REFERENCES review_record(id),
    article_id      TEXT REFERENCES article(id),
    document_id     TEXT REFERENCES document(id),
    label           TEXT NOT NULL,
    ft_decision     TEXT NOT NULL DEFAULT 'pending'
                    CHECK (ft_decision IN ('pending','include','exclude','not_retrieved')),
    ft_criterion_id TEXT REFERENCES review_criterion(id),
    ft_decided_by   TEXT REFERENCES review_reviewer(id),
    ft_decided_at   TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (ft_decision NOT IN ('exclude','not_retrieved') OR ft_criterion_id IS NOT NULL),
    CHECK (ft_decision = 'pending' OR (ft_decided_by IS NOT NULL AND ft_decided_at IS NOT NULL))
) STRICT;
CREATE INDEX review_study_review_idx ON review_study (review_id);

CREATE TABLE ft_proposal (
    id              TEXT PRIMARY KEY,                                      -- ftp_
    study_id        TEXT NOT NULL REFERENCES review_study(id),
    workflow_run_id TEXT NOT NULL REFERENCES workflow_run(id),
    decision        TEXT NOT NULL CHECK (decision IN ('include','exclude','unsure')),
    summary         TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    criterion_id    TEXT REFERENCES review_criterion(id),
    quote           TEXT,
    page_no         INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord     INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES ft_proposal(id),
    CHECK (decision <> 'exclude' OR criterion_id IS NOT NULL),
    CHECK (quote IS NULL OR ((page_no IS NULL) <> (section_ord IS NULL)))
) STRICT;
CREATE UNIQUE INDEX ft_proposal_one_current ON ft_proposal (study_id) WHERE superseded_by IS NULL;

-- ------------------------------------------------------------------ extraction form and values
CREATE TABLE extraction_form (
    id         TEXT PRIMARY KEY,                                           -- frm_
    review_id  TEXT NOT NULL REFERENCES review(id),
    version    INTEGER NOT NULL CHECK (version >= 1),
    status     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','frozen')),
    contract   TEXT NOT NULL DEFAULT 'eurydike-extract-v1',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    frozen_at  TEXT,
    UNIQUE (review_id, version),
    CHECK ((status = 'frozen') = (frozen_at IS NOT NULL))
) STRICT;

CREATE TABLE form_field (
    form_id  TEXT NOT NULL REFERENCES extraction_form(id),
    key      TEXT NOT NULL CHECK (key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
    ord      INTEGER NOT NULL CHECK (ord >= 1),
    section  TEXT,
    label    TEXT NOT NULL,
    type     TEXT NOT NULL CHECK (type IN ('text','long','number','choice','multi','bool','outcome')),
    unit     TEXT,
    hint     TEXT,
    options  TEXT CHECK (options IS NULL OR (json_valid(options) AND json_type(options) = 'array')),
    required INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0,1)),
    card_key TEXT CHECK (card_key IS NULL OR (card_key GLOB '[a-z]*' AND card_key NOT GLOB '*[^a-z0-9_]*')),
    PRIMARY KEY (form_id, key),
    CHECK (type NOT IN ('choice','multi') OR options IS NOT NULL),
    CHECK (type <> 'outcome' OR coalesce(card_key, '') = 'righe_esito')
) STRICT;

CREATE TABLE extraction_value (
    id              TEXT PRIMARY KEY,                                      -- exv_
    study_id        TEXT NOT NULL REFERENCES review_study(id),
    reviewer_id     TEXT NOT NULL REFERENCES review_reviewer(id),
    form_id         TEXT NOT NULL REFERENCES extraction_form(id),
    field_key       TEXT NOT NULL,
    value           TEXT CHECK (value IS NULL OR json_valid(value)),
    quote           TEXT,
    page_no         INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord     INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    quote_check     TEXT NOT NULL DEFAULT 'none' CHECK (quote_check IN ('exact','fuzzy','not_found','none')),
    confidence      TEXT CHECK (confidence IS NULL OR confidence IN ('high','medium','low')),
    note            TEXT,
    state           TEXT NOT NULL CHECK (state IN ('ai','confirmed','edited','nr')),
    adopted_from    TEXT REFERENCES review_reviewer(id),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES extraction_value(id),
    FOREIGN KEY (form_id, field_key) REFERENCES form_field(form_id, key),
    CHECK (state <> 'ai' OR workflow_run_id IS NOT NULL),
    CHECK (state <> 'nr' OR (value IS NULL AND quote IS NULL)),
    CHECK (quote IS NULL OR ((page_no IS NULL) <> (section_ord IS NULL))),
    CHECK (quote IS NOT NULL OR quote_check = 'none')
) STRICT;
CREATE UNIQUE INDEX extraction_value_one_current ON extraction_value (study_id, reviewer_id, field_key)
    WHERE superseded_by IS NULL;

-- ------------------------------------------------------------------ risk of bias and quality
CREATE TABLE appraisal_tool_choice (
    id            TEXT PRIMARY KEY,                                        -- atc_
    study_id      TEXT NOT NULL REFERENCES review_study(id),
    kind          TEXT NOT NULL CHECK (kind IN ('rob','quality')),
    tool          TEXT NOT NULL,
    chosen_by     TEXT NOT NULL REFERENCES review_reviewer(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by TEXT REFERENCES appraisal_tool_choice(id)
) STRICT;
CREATE UNIQUE INDEX appraisal_tool_choice_one_current ON appraisal_tool_choice (study_id, kind)
    WHERE superseded_by IS NULL;

CREATE TABLE appraisal (
    id              TEXT PRIMARY KEY,                                      -- apr_
    study_id        TEXT NOT NULL REFERENCES review_study(id),
    reviewer_id     TEXT NOT NULL REFERENCES review_reviewer(id),
    tool            TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    judgement       TEXT NOT NULL,
    rationale       TEXT,
    quote           TEXT,
    page_no         INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord     INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    quote_check     TEXT NOT NULL DEFAULT 'none' CHECK (quote_check IN ('exact','fuzzy','not_found','none')),
    state           TEXT NOT NULL CHECK (state IN ('ai','confirmed','edited')),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES appraisal(id),
    CHECK (state <> 'ai' OR workflow_run_id IS NOT NULL),
    CHECK (quote IS NULL OR ((page_no IS NULL) <> (section_ord IS NULL)))
) STRICT;
CREATE UNIQUE INDEX appraisal_one_current ON appraisal (study_id, reviewer_id, tool, item_id)
    WHERE superseded_by IS NULL;

-- ------------------------------------------------------------------ registered analyses (snapshots, recomputable)
CREATE TABLE ma_analysis (
    id            TEXT PRIMARY KEY,                                        -- mas_
    review_id     TEXT NOT NULL REFERENCES review(id),
    outcome       TEXT NOT NULL,
    timepoint     TEXT,
    measure       TEXT NOT NULL CHECK (measure IN ('MD','SMD')),
    model         TEXT NOT NULL CHECK (model IN ('FE','RE-DL','RE-REML')),
    ci_method     TEXT NOT NULL CHECK (ci_method IN ('wald','hksj')),
    settings      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings)),
    code_version  TEXT NOT NULL,
    inputs_sha256 TEXT NOT NULL CHECK (length(inputs_sha256) = 64 AND inputs_sha256 NOT GLOB '*[^0-9a-f]*'),
    result        TEXT NOT NULL CHECK (json_valid(result)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE ma_input (
    analysis_id  TEXT NOT NULL REFERENCES ma_analysis(id),
    study_id     TEXT NOT NULL REFERENCES review_study(id),
    row_e_id     TEXT NOT NULL REFERENCES card_outcome_row(id),
    row_c_id     TEXT NOT NULL REFERENCES card_outcome_row(id),
    mean_e TEXT NOT NULL, sd_e TEXT NOT NULL, n_e TEXT NOT NULL,
    mean_c TEXT NOT NULL, sd_c TEXT NOT NULL, n_c TEXT NOT NULL,
    conversion_e TEXT NOT NULL,
    conversion_c TEXT NOT NULL,
    yi           REAL NOT NULL,
    vi           REAL NOT NULL CHECK (vi > 0),
    PRIMARY KEY (analysis_id, study_id)
) STRICT;

-- ------------------------------------------------------------------ rules that need a trigger
-- the full-text decision is written only by a human reviewer or by the consensus (decision D11)
CREATE TRIGGER review_study_ft_human_only BEFORE UPDATE OF ft_decision, ft_decided_by ON review_study
WHEN NEW.ft_decided_by IS NOT NULL
 AND (SELECT kind FROM review_reviewer WHERE id = NEW.ft_decided_by) NOT IN ('human','consensus')
BEGIN SELECT RAISE(ABORT, 'the full-text decision is written only by a human reviewer or the consensus'); END;
CREATE TRIGGER review_study_ft_human_only_insert BEFORE INSERT ON review_study
WHEN NEW.ft_decided_by IS NOT NULL
 AND (SELECT kind FROM review_reviewer WHERE id = NEW.ft_decided_by) NOT IN ('human','consensus')
BEGIN SELECT RAISE(ABORT, 'the full-text decision is written only by a human reviewer or the consensus'); END;
-- confirmed / edited / not reported: only a human reviewer or the consensus (principle 1: Claude proposes, the user decides)
CREATE TRIGGER extraction_value_human_states BEFORE INSERT ON extraction_value
WHEN NEW.state IN ('confirmed','edited','nr')
 AND (SELECT kind FROM review_reviewer WHERE id = NEW.reviewer_id) NOT IN ('human','consensus')
BEGIN SELECT RAISE(ABORT, 'confirmed, edited and nr values come only from a human reviewer or the consensus'); END;
-- a field taken from the article card is never extracted again in a review (it is read from card_item / card_outcome_row)
CREATE TRIGGER extraction_value_review_fields_only BEFORE INSERT ON extraction_value
WHEN (SELECT card_key FROM form_field WHERE form_id = NEW.form_id AND key = NEW.field_key) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'this field comes from the article card: correct it there (card_item)'); END;
CREATE TRIGGER appraisal_human_states BEFORE INSERT ON appraisal
WHEN NEW.state IN ('confirmed','edited')
 AND (SELECT kind FROM review_reviewer WHERE id = NEW.reviewer_id) NOT IN ('human','consensus')
BEGIN SELECT RAISE(ABORT, 'confirmed and edited judgements come only from a human reviewer or the consensus'); END;
-- a frozen form does not change: a new version is a new form
CREATE TRIGGER form_field_frozen BEFORE INSERT ON form_field
WHEN (SELECT status FROM extraction_form WHERE id = NEW.form_id) = 'frozen'
BEGIN SELECT RAISE(ABORT, 'the form is frozen: make a new version'); END;
CREATE TRIGGER form_field_no_update BEFORE UPDATE ON form_field
BEGIN SELECT RAISE(ABORT, 'form fields are not changed: make a new version of the form'); END;
CREATE TRIGGER review_log_no_update BEFORE UPDATE ON review_log
BEGIN SELECT RAISE(ABORT, 'review_log rows are never changed (add a new row)'); END;
CREATE TRIGGER ma_analysis_no_update BEFORE UPDATE ON ma_analysis
BEGIN SELECT RAISE(ABORT, 'a registered analysis is a snapshot: register a new one'); END;

-- nothing is deleted
CREATE TRIGGER review_no_delete BEFORE DELETE ON review BEGIN SELECT RAISE(ABORT, 'review rows are never deleted'); END;
CREATE TRIGGER review_criterion_no_delete BEFORE DELETE ON review_criterion BEGIN SELECT RAISE(ABORT, 'review_criterion rows are never deleted (supersede them)'); END;
CREATE TRIGGER review_reviewer_no_delete BEFORE DELETE ON review_reviewer BEGIN SELECT RAISE(ABORT, 'review_reviewer rows are never deleted'); END;
CREATE TRIGGER review_log_no_delete BEFORE DELETE ON review_log BEGIN SELECT RAISE(ABORT, 'review_log rows are never deleted'); END;
CREATE TRIGGER search_import_no_delete BEFORE DELETE ON search_import BEGIN SELECT RAISE(ABORT, 'search_import rows are never deleted'); END;
CREATE TRIGGER review_record_no_delete BEFORE DELETE ON review_record BEGIN SELECT RAISE(ABORT, 'review_record rows are never deleted'); END;
CREATE TRIGGER screening_decision_no_delete BEFORE DELETE ON screening_decision BEGIN SELECT RAISE(ABORT, 'screening_decision rows are never deleted (supersede them)'); END;
CREATE TRIGGER review_study_no_delete BEFORE DELETE ON review_study BEGIN SELECT RAISE(ABORT, 'review_study rows are never deleted'); END;
CREATE TRIGGER ft_proposal_no_delete BEFORE DELETE ON ft_proposal BEGIN SELECT RAISE(ABORT, 'ft_proposal rows are never deleted (supersede them)'); END;
CREATE TRIGGER extraction_form_no_delete BEFORE DELETE ON extraction_form BEGIN SELECT RAISE(ABORT, 'extraction_form rows are never deleted'); END;
CREATE TRIGGER form_field_no_delete BEFORE DELETE ON form_field BEGIN SELECT RAISE(ABORT, 'form_field rows are never deleted'); END;
CREATE TRIGGER extraction_value_no_delete BEFORE DELETE ON extraction_value BEGIN SELECT RAISE(ABORT, 'extraction_value rows are never deleted (supersede them)'); END;
CREATE TRIGGER card_item_no_delete BEFORE DELETE ON card_item BEGIN SELECT RAISE(ABORT, 'card_item rows are never deleted (supersede them)'); END;
CREATE TRIGGER card_outcome_row_no_delete BEFORE DELETE ON card_outcome_row BEGIN SELECT RAISE(ABORT, 'card_outcome_row rows are never deleted (supersede them)'); END;
CREATE TRIGGER appraisal_tool_choice_no_delete BEFORE DELETE ON appraisal_tool_choice BEGIN SELECT RAISE(ABORT, 'appraisal_tool_choice rows are never deleted (supersede them)'); END;
CREATE TRIGGER appraisal_no_delete BEFORE DELETE ON appraisal BEGIN SELECT RAISE(ABORT, 'appraisal rows are never deleted (supersede them)'); END;
CREATE TRIGGER ma_analysis_no_delete BEFORE DELETE ON ma_analysis BEGIN SELECT RAISE(ABORT, 'ma_analysis rows are never deleted'); END;
CREATE TRIGGER ma_input_no_delete BEFORE DELETE ON ma_input BEGIN SELECT RAISE(ABORT, 'ma_input rows are never deleted'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('card_item','', 'One value of the unified article card (cards-v2): one card per article, used by ORPHEUS and EURYDIKE, so the system works without the PDFs. value + verbatim quote + page (or JATS section). state ai = proposed by a model (claude_session or chat_manuale), never exported or analysed as verified; confirmed, edited (corrected or added by hand by the user) and nr (not reported) are written only by the user. A correction is a new row that supersedes the old one (never deleted).'),
 ('card_item','field_key', 'Field of cards-v2 (the 12 fields of cards-v1 plus the blocks of Tabula: study, participants, intervention and comparison, outcomes and instruments, results and conclusions); item_ord numbers repeated fields (e.g. misura_principale).'),
 ('card_item','from_card_id', 'With from_ord: the cards-v1 value this row was copied from (cards-v1 cards are never changed).'),
 ('card_outcome_row','', 'Outcome statistics of one group at one time point of an article, exactly as written in the paper (all TEXT): the numeric part of the unified card, used by the meta-analysis of any review. Corrections by the user supersede the row (state edited). Mean and SD derived from them are computed by to_mean_sd, never stored.'),
 ('form_field','card_key', 'When set, the field is taken from the unified article card (card_item.field_key, or righe_esito for outcome rows): it is not extracted again and is corrected only in the card.'),
 ('review','', 'A systematic review or meta-analysis conducted in EURYDIKE (T-051, ADR-0012): question, PICO, registration, settings. In English (R11); the review in preparation is a manuscript: never on GitHub (R10).'),
 ('review','screen_reviewers', 'Reviewers needed per record before a final screening decision (final_decision: fewer = partial).'),
 ('review','data_dir', 'Folder of the review in the data folder (revisioni\<name>): search files, PDFs, reports.'),
 ('review_criterion','', 'Inclusion and exclusion criteria of the protocol (every exclusion criterion is a selectable reason) and deterministic pre-filters (rule_json). A changed criterion is a new row plus a deviation in review_log.'),
 ('review_reviewer','', 'Who decides in a review: the user (human), Claude declared as second reviewer (claude_session), a free chat (chat_manuale), and one consensus. The user-Claude kappa is reported as such (D6).'),
 ('review_log','', 'Method log, one row per entry (deviations, bulk actions, rules, PDF links, notes, pre-filters, analyses); never rewritten.'),
 ('search_import','', 'One exported search file (RIS, PubMed, Web of Science, CSV/TSV) with its fingerprint; raw_count and dupes_count feed the PRISMA flow (computed, never typed).'),
 ('review_record','', 'One bibliographic record of a review. Records stay records (D5): only studies read in full with a complete PDF become ORPHEUS articles. dedup_key = rec_key (eurydike/core/records.py); duplicate_of points to the record kept.'),
 ('screening_decision','', 'One title/abstract decision of one reviewer on one record; a new decision supersedes the old one. Claude''s decisions come from a workflow_run (origin model, blind); a suggestion that is not stored here is never a decision. The final decision is computed (final_decision), never stored.'),
 ('review_study','', 'A study sought for the full text: linked to the ORPHEUS article and document when read. ft_decision is written only by a human reviewer or the consensus (trigger, D11).'),
 ('ft_proposal','', 'Claude''s blind full-text proposal (eurydike-ft-v1) with quote and page; never a decision.'),
 ('extraction_form','', 'The ad hoc extraction form of a review (eurydike-extract-v1), in versions: a form with values is frozen; a change is a new version.'),
 ('form_field','', 'One field of an extraction form (label in English; types text, long, number, choice, multi, bool, outcome).'),
 ('extraction_value','', 'One extracted value of a field of the review alone (fields taken from the article card are refused by a trigger): value + verbatim quote + page (or section), checked by the ORPHEUS validators (quote_check). state ai = proposed by Claude, never exported or analysed until a human (or the consensus) makes it confirmed, edited or nr (trigger).'),
  ('appraisal_tool_choice','', 'The risk-of-bias or quality tool chosen for one study (tools in eurydike/core/tools.py).'),
 ('appraisal','', 'One judgement on one domain or item of a risk-of-bias or quality tool, with rationale and quote; item_id overall = the overall judgement written by hand. Scores and suggestions are computed (qual_score, rob_overall), never stored.'),
 ('ma_analysis','', 'A registered meta-analysis: a snapshot to cite numbers in a manuscript, with code version and fingerprint of the inputs; it can always be recomputed and the report warns when the data changed.'),
 ('ma_input','', 'The per-study inputs of a registered analysis (as used, with the conversion method of each group) and the effect yi with its variance vi.');
