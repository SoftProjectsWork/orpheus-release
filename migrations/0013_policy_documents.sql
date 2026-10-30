-- ORPHEUS migration 0013 — more guideline PDFs in one version (T-050, rules policy-v2, journal-cards-v1 with "documento")
-- Asked by the user on 2026-10-19 12:15 (manual session): "Sviluppa la possibilità di leggere più pdf per cartella di
-- ORPHEUS". A journal's author guidelines are often split over several web pages (instructions, aims and scope, APC,
-- one page per article type). From policy-v2 a version of the guidelines is the SET of the current PDFs in
-- inbox/riviste/<SIGLA>/linee_guida (one per printed page address: a newer print of the same page replaces the older).
--
-- journal_policy_document: the PDFs of one version, in order (the instructions page first). Versions written before
--   this migration have no rows here: their only PDF is the document of the version's source snapshot (as before).
-- journal_card_value.document_id: the PDF of the version where the value's page and quote are; NULL = the card's
--   document (cards written before this migration, or versions with one PDF).
-- Only additions: no row is changed or deleted.

CREATE TABLE journal_policy_document (
    policy_version_id TEXT NOT NULL REFERENCES journal_policy_version(id),
    document_id       TEXT NOT NULL REFERENCES document(id),
    ord               INTEGER NOT NULL CHECK (ord >= 1),
    file_name         TEXT NOT NULL,
    page_key          TEXT NOT NULL,
    content_sha256    TEXT NOT NULL CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (policy_version_id, document_id),
    UNIQUE (policy_version_id, ord)
) STRICT;

CREATE TRIGGER journal_policy_document_no_delete BEFORE DELETE ON journal_policy_document
BEGIN SELECT RAISE(ABORT, 'journal_policy_document rows are never deleted'); END;

ALTER TABLE journal_card_value ADD COLUMN document_id TEXT REFERENCES document(id);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('journal_policy_document','', 'The guideline PDFs that make up one version of a journal''s author guidelines (policy-v2, T-050): one per printed page address, the newest print of each page; the instructions page first.'),
 ('journal_policy_document','policy_version_id', 'The version (journal_policy_version).'),
 ('journal_policy_document','document_id', 'One guideline PDF of the version.'),
 ('journal_policy_document','ord', 'Order of the PDF in the version (1 = instructions for authors).'),
 ('journal_policy_document','file_name', 'File name of the PDF in inbox/riviste/<SIGLA>/linee_guida (what a card names in "documento").'),
 ('journal_policy_document','page_key', 'What identifies the web page: the address printed by the browser, or the file name when none is printed.'),
 ('journal_policy_document','content_sha256', 'Fingerprint of the PDF''s content text (policy-v1 rules: browser print lines left out).'),
 ('journal_card_value','document_id', 'The guideline PDF where page_no and quote are (journal_policy_document); NULL = journal_card.document_id.');
