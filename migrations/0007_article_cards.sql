-- ORPHEUS migration 0007 — article cards (T-019, ADR-0008)
-- Approved by the user on 2026-10-11 22:30 (manual session T-019): "Nuova migrazione 0007".
--
-- article_card: one card per accepted workflow_run (executor claude_session). Written only when the submitted JSON passed
--   every validator of its card version (validation_result rows of the same run). At most one 'current' card per
--   article and card version; an older one becomes 'superseded' (never deleted).
-- card_value: one value of a card (field, value, unit, group, position, verbatim quote, origin). A value with origin
--   'reported' or 'interpreted' always has a position (PDF page or JATS section) and a quote found there by the
--   validator; 'not_available' means the article does not report it (no value, no position).

CREATE TABLE article_card (
    id                 TEXT PRIMARY KEY,
    article_id         TEXT NOT NULL REFERENCES article(id),
    workflow_run_id    TEXT NOT NULL UNIQUE REFERENCES workflow_run(id),
    card_version       TEXT NOT NULL,
    document_id        TEXT REFERENCES document(id),
    jats_snapshot_id   TEXT REFERENCES source_snapshot(id),
    text_sha256        TEXT NOT NULL CHECK (length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'),
    file_sha256        TEXT NOT NULL CHECK (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    model_ref          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','superseded')),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (document_id IS NOT NULL OR jats_snapshot_id IS NOT NULL)
) STRICT;

CREATE UNIQUE INDEX article_card_one_current ON article_card (article_id, card_version) WHERE status = 'current';

CREATE TABLE card_value (
    card_id           TEXT NOT NULL REFERENCES article_card(id),
    ord               INTEGER NOT NULL CHECK (ord >= 1),
    field             TEXT NOT NULL,
    name              TEXT,
    value             TEXT,
    unit              TEXT,
    group_label       TEXT,
    page_no           INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    section_ord       INTEGER CHECK (section_ord IS NULL OR section_ord >= 0),
    quote             TEXT,
    origin            TEXT NOT NULL CHECK (origin IN ('reported','interpreted','not_available')),
    note              TEXT,
    verified_by_human INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_human IN (0,1)),
    human_verdict     TEXT CHECK (human_verdict IS NULL OR human_verdict IN ('right','wrong')),
    PRIMARY KEY (card_id, ord),
    CHECK (origin = 'not_available' OR (value IS NOT NULL AND quote IS NOT NULL
                                        AND ((page_no IS NULL) <> (section_ord IS NULL)))),
    CHECK (origin <> 'not_available' OR (value IS NULL AND quote IS NULL AND page_no IS NULL AND section_ord IS NULL)),
    CHECK (verified_by_human = 1 OR human_verdict IS NULL)
) STRICT;

CREATE INDEX card_value_field_idx ON card_value (field);

CREATE TRIGGER card_value_no_delete BEFORE DELETE ON card_value
BEGIN SELECT RAISE(ABORT, 'card_value rows are never deleted (supersede the card instead)'); END;
CREATE TRIGGER article_card_no_delete BEFORE DELETE ON article_card
BEGIN SELECT RAISE(ABORT, 'article_card rows are never deleted (status superseded instead)'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('article_card','', 'One article card (T-019, ADR-0008) filled in by Claude in a session from a work packet and accepted only because it passed every validator of its card version (see validation_result of the same workflow_run). At most one current card per article and version.'),
 ('article_card','workflow_run_id', 'The run (executor claude_session) the packet was issued for; its validation_result rows are the checks passed.'),
 ('article_card','card_version', 'Contract of the card, e.g. cards-v1 (src/orpheus/cards.py).'),
 ('article_card','document_id', 'The PDF whose pages were given in the packet (page numbers of card_value refer to it).'),
 ('article_card','jats_snapshot_id', 'The Europe PMC JATS text whose sections were given in the packet (section numbers of card_value refer to article_section.ord of this snapshot); NULL when only the PDF was given.'),
 ('article_card','text_sha256', 'SHA-256 of the text given in the packet (pages and sections); the submit is refused if the text in the database changed since.'),
 ('article_card','file_sha256', 'SHA-256 of the submitted card file.'),
 ('article_card','model_ref', 'The model that filled in the card, as declared in the card (e.g. claude-opus-5-5).'),
 ('article_card','status', 'current, or superseded by a newer card of the same article and version.'),
 ('card_value','', 'One value of an article card: field, value as written in the article, unit, group, position (PDF page or JATS section), verbatim quote, origin.'),
 ('card_value','field', 'Field of the card version (cards-v1: disegno_studio, partecipanti_numero, partecipanti_sesso, partecipanti_eta, partecipanti_livello, partecipanti_sport, intervento_esposizione, confronto, misura_principale, risultato_chiave, limite, conclusione_autori).'),
 ('card_value','name', 'Name of the measure (misura_principale) or of the result, when the field has one.'),
 ('card_value','value', 'The value, short, as written in the article; every number in it is in the quote. NULL for not_available.'),
 ('card_value','unit', 'Unit of the value, written in the quote (validator unita).'),
 ('card_value','group_label', 'Group, arm, sex or condition the value refers to, when not the whole sample.'),
 ('card_value','page_no', 'Page of article_card.document_id where the quote is (exactly one of page_no and section_ord).'),
 ('card_value','section_ord', 'Section (article_section.ord of article_card.jats_snapshot_id) where the quote is.'),
 ('card_value','quote', 'Continuous text copied from that page or section (found there by the validator up to spaces, line breaks, hyphenation, ligatures and case).'),
 ('card_value','origin', 'reported = written in the article; interpreted = deduced from the quote (e.g. the study design); not_available = the article does not report it.'),
 ('card_value','verified_by_human', '1 when the user checked this value by hand (T-019 sample).'),
 ('card_value','human_verdict', 'right / wrong, from the hand check; NULL if not checked.');
