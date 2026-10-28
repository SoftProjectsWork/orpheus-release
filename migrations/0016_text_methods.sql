-- ORPHEUS migration 0016 — T-057 livello 1: what the published articles report about methods, statistics, transparency
-- and structure (rules stats-terms-v1, deterministic, no model, no network)
-- APPROVED by the user on 2026-10-27 16:17 (Europe/Rome), manual session: "Approvo" (proposal shown at 16:16 as
-- MIGRAZIONE_0016_PROPOSTA.sql). Revision 2 APPROVED at 16:33 ("Approvo la modifica"): no foreign key from
-- text_method_item to document_page, so the items can go to colleagues (the sharing package never has page texts).
--
-- Decisions of the user (2026-10-27): start from part B level 1; dictionary of the statistical methods approved
-- (groups: confronto, modello, approccio, affidabilita, riporto, software, trasparenza; structure counts apart);
-- tests to be read also by objective (cross with the objective type written by Claude in cards-v2: no table here);
-- profiles and extracted items shared with colleagues, submissions and reviewers' comments never.
--
-- Only additions: three new tables, no change to existing tables or rows. The rows of the first two are derived from
-- document_page by code (src/orpheus/stats_terms.py): rebuilt when the rules change, like `segment`. The third keeps the
-- user's manual check (S/N) and survives the rebuilds (no foreign key to the rebuilt rows).
-- Level 2 (objective, objective type, design of times and groups, primary outcome, main analysis, effect with CI) needs
-- no migration: new field keys of cards-v2 in `card_item` (decision of the user: cards-v2).

CREATE TABLE text_method_item (
    id           TEXT PRIMARY KEY,                                         -- tmi_
    article_id   TEXT NOT NULL REFERENCES article(id),
    document_id  TEXT NOT NULL REFERENCES document(id),
    rules        TEXT NOT NULL,                                            -- e.g. stats-terms-v1
    grp          TEXT NOT NULL CHECK (grp IN ('confronto','modello','approccio','affidabilita','riporto',
                                                'software','trasparenza')),
    term         TEXT NOT NULL CHECK (term GLOB '[a-z]*' AND term NOT GLOB '*[^a-z0-9_]*'),
    detail       TEXT CHECK (detail IS NULL OR length(trim(detail)) > 0),  -- e.g. paired, 26.0, 0.05, NCT01234567
    section      TEXT,                                                     -- heading of the section where it was found
    page_no      INTEGER NOT NULL CHECK (page_no >= 1),
    quote        TEXT NOT NULL CHECK (length(quote) BETWEEN 1 AND 600),    -- the sentence, exactly as on the page
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (document_id, rules, term, page_no, quote)
) STRICT;
CREATE INDEX text_method_item_article_idx ON text_method_item (article_id, rules);
CREATE INDEX text_method_item_term_idx ON text_method_item (rules, term);

CREATE TABLE text_structure (
    document_id        TEXT NOT NULL REFERENCES document(id),
    article_id         TEXT NOT NULL REFERENCES article(id),
    rules              TEXT NOT NULL,
    pages              INTEGER NOT NULL CHECK (pages >= 1),
    words_abstract     INTEGER CHECK (words_abstract IS NULL OR words_abstract >= 0),
    words_introduction INTEGER CHECK (words_introduction IS NULL OR words_introduction >= 0),
    words_methods      INTEGER CHECK (words_methods IS NULL OR words_methods >= 0),
    words_results      INTEGER CHECK (words_results IS NULL OR words_results >= 0),
    words_discussion   INTEGER CHECK (words_discussion IS NULL OR words_discussion >= 0),
    words_body         INTEGER CHECK (words_body IS NULL OR words_body >= 0),
    figures            INTEGER CHECK (figures IS NULL OR figures >= 0),
    tables             INTEGER CHECK (tables IS NULL OR tables >= 0),
    references_n       INTEGER CHECK (references_n IS NULL OR references_n >= 0),
    note               TEXT,                                               -- why a count is missing (NULL)
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (document_id, rules)
) STRICT;

CREATE TABLE text_method_check (
    document_id   TEXT NOT NULL REFERENCES document(id),
    rules         TEXT NOT NULL,
    term          TEXT NOT NULL,
    page_no       INTEGER NOT NULL CHECK (page_no >= 1),
    quote_sha256  TEXT NOT NULL CHECK (length(quote_sha256) = 64 AND quote_sha256 NOT GLOB '*[^0-9a-f]*'),
    verdict       TEXT NOT NULL CHECK (verdict IN ('S','N')),             -- S = the item is right
    source_file   TEXT NOT NULL,                                           -- the compiled CSV it comes from
    checked_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (document_id, rules, term, page_no, quote_sha256)
) STRICT;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('text_method_item','', 'A statistical method, reporting element, software or transparency statement found in the body of a complete article by the rules stats-terms-v1 (T-057, deterministic, no model); one row per term and sentence, with page and the exact sentence. Rebuilt when the rules change.'),
 ('text_method_item','grp', 'Group of the dictionary: confronto, modello, approccio, affidabilita, riporto, software, trasparenza.'),
 ('text_method_item','term', 'Term of the dictionary, e.g. t_test, mixed_model, effect_size, spss, consort, registration.'),
 ('text_method_item','detail', 'What the rule read next to the term when the dictionary asks for it (paired/independent, effect size kind, software version, p threshold, registry number); NULL otherwise.'),
 ('text_method_item','quote', 'The sentence exactly as on the page (document_page), never from the bibliography.'),
 ('text_structure','', 'Structure of a complete article counted by code (stats-terms-v1): words per section, figures, tables and references; NULL when the count cannot be made with certainty (note says why).'),
 ('text_method_check','', 'The user''s manual check of a sample of text_method_item (CSV S/N, like T-027); used to measure the precision of the rules (≥ 0.95 before use). Kept across rebuilds; matched by document, rules, term, page and SHA-256 of the sentence.');
