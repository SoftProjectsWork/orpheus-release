-- ORPHEUS migration 0008 — journal cards (T-027, ADR-0009)
-- Approved by the user on 2026-10-14 16:40 (manual session): "Migrazione 0008", fields "proposta completa",
-- hand check of all 5 cards.
--
-- journal_card: one card of a journal, written from ONE version of its author guidelines (journal_policy_version) and
--   accepted only because the submitted JSON passed every validator of journal-cards-v1. At most one 'current' card
--   per journal and card version; an older one becomes 'superseded' (never deleted).
-- journal_card_value: one value of a card (field, article type, value, number, unit, page of the guideline PDF,
--   verbatim quote, origin). 'reported' / 'interpreted' values always have a page and a quote found there by the
--   validator; 'not_available' means the guidelines do not say it (no value, no page).

CREATE TABLE journal_card (
    id                 TEXT PRIMARY KEY,
    journal_id         TEXT NOT NULL REFERENCES journal(id),
    policy_version_id  TEXT NOT NULL REFERENCES journal_policy_version(id),
    document_id        TEXT NOT NULL REFERENCES document(id),
    card_version       TEXT NOT NULL,
    file_sha256        TEXT NOT NULL CHECK (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    model_ref          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','superseded')),
    note               TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE UNIQUE INDEX journal_card_one_current ON journal_card (journal_id, card_version) WHERE status = 'current';

CREATE TABLE journal_card_value (
    card_id           TEXT NOT NULL REFERENCES journal_card(id),
    ord               INTEGER NOT NULL CHECK (ord >= 1),
    field             TEXT NOT NULL,
    article_type      TEXT,
    value             TEXT,
    number            REAL,
    unit              TEXT,
    page_no           INTEGER CHECK (page_no IS NULL OR page_no >= 1),
    quote             TEXT,
    origin            TEXT NOT NULL CHECK (origin IN ('reported','interpreted','not_available')),
    note              TEXT,
    verified_by_human INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_human IN (0,1)),
    human_verdict     TEXT CHECK (human_verdict IS NULL OR human_verdict IN ('right','wrong')),
    PRIMARY KEY (card_id, ord),
    CHECK (origin = 'not_available' OR (value IS NOT NULL AND quote IS NOT NULL AND page_no IS NOT NULL)),
    CHECK (origin <> 'not_available' OR (value IS NULL AND quote IS NULL AND page_no IS NULL AND number IS NULL)),
    CHECK (verified_by_human = 1 OR human_verdict IS NULL)
) STRICT;

CREATE INDEX journal_card_value_field_idx ON journal_card_value (field, article_type);

CREATE TRIGGER journal_card_value_no_delete BEFORE DELETE ON journal_card_value
BEGIN SELECT RAISE(ABORT, 'journal_card_value rows are never deleted (supersede the card instead)'); END;
CREATE TRIGGER journal_card_no_delete BEFORE DELETE ON journal_card
BEGIN SELECT RAISE(ABORT, 'journal_card rows are never deleted (status superseded instead)'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('journal_card','', 'One journal card (T-027, ADR-0009) filled in by Claude in a session from ONE version of the journal''s author guidelines and accepted only because it passed every validator of its card version (journal-cards-v1). At most one current card per journal and version.'),
 ('journal_card','journal_id', 'The journal the card describes.'),
 ('journal_card','policy_version_id', 'The version of the author guidelines (journal_policy_version) the card was written from; a newer version makes the card to be redone.'),
 ('journal_card','document_id', 'The guideline PDF of that version (page numbers of journal_card_value refer to it).'),
 ('journal_card','card_version', 'Contract of the card, e.g. journal-cards-v1 (src/orpheus/journal_cards.py).'),
 ('journal_card','file_sha256', 'SHA-256 of the submitted card file.'),
 ('journal_card','model_ref', 'The model that filled in the card, as declared in the card (e.g. claude-opus-5-5).'),
 ('journal_card','status', 'current, or superseded by a newer card of the same journal and version.'),
 ('journal_card','note', 'General note of the card (note_generali), if any.'),
 ('journal_card_value','', 'One value of a journal card: field, article type, value as written in the guidelines, number, unit, page, verbatim quote, origin.'),
 ('journal_card_value','field', 'Field of the card version (journal-cards-v1: per article type tipo_articolo, parole_testo_max, abstract_parole_max, abstract_struttura, parole_chiave, riferimenti_max, figure_tabelle_max, altri_elementi; per journal ambito, stile_citazioni, revisione, preprint, politica_ia, dati, registrazione, linee_guida_reporting, etica_consenso, conflitti_finanziamenti, open_access_costi, orcid, documenti_invio).'),
 ('journal_card_value','article_type', 'Article type the value refers to (as named in the card), "tutti i tipi" for every type; NULL for a field of the journal.'),
 ('journal_card_value','value', 'The value, short, as written in the guidelines; every number in it is in the quote. NULL for not_available.'),
 ('journal_card_value','number', 'The first number of the value for the numeric fields (word, reference, figure limits), when it has one.'),
 ('journal_card_value','unit', 'Unit of the value, written in the quote (e.g. words).'),
 ('journal_card_value','page_no', 'Page of journal_card.document_id where the quote is.'),
 ('journal_card_value','quote', 'Continuous text copied from that page (found there by the validator up to spaces, line breaks, hyphenation, ligatures and case).'),
 ('journal_card_value','origin', 'reported = written in the guidelines; interpreted = deduced from the quote; not_available = the guidelines do not say it.'),
 ('journal_card_value','note', 'Note on the value, if any.'),
 ('journal_card_value','verified_by_human', '1 when the user checked this value by hand (T-027 check of all cards).'),
 ('journal_card_value','human_verdict', 'right / wrong, from the hand check; NULL if not checked.');
