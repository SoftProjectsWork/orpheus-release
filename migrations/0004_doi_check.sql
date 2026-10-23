-- ORPHEUS migration 0004 — verification of the DOI candidates against Crossref (T-008)
-- One row per verification of one document's DOI candidate. Rows are never changed or deleted:
-- a later verification (new rules, new response) adds a new row, and the latest row is the
-- current state. An `article` is created only by a 'confirmed' verification.

CREATE TABLE doi_check (
    id                 TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL REFERENCES document(id),
    doi                TEXT NOT NULL CHECK (doi = lower(doi)),
    source_id          TEXT NOT NULL REFERENCES source(id),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    outcome            TEXT NOT NULL CHECK (outcome IN ('confirmed','not_found','mismatch')),
    reasons            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reasons)),
    details            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details)),
    rules_version      TEXT NOT NULL,
    article_id         TEXT REFERENCES article(id),
    checked_at         TEXT NOT NULL,
    CHECK (outcome <> 'confirmed' OR article_id IS NOT NULL),
    CHECK (outcome = 'confirmed' OR article_id IS NULL)
) STRICT;

CREATE TRIGGER doi_check_no_update BEFORE UPDATE ON doi_check
BEGIN SELECT RAISE(ABORT, 'doi_check is append-only (UPDATE not allowed)'); END;
CREATE TRIGGER doi_check_no_delete BEFORE DELETE ON doi_check
BEGIN SELECT RAISE(ABORT, 'doi_check is append-only (DELETE not allowed)'); END;

CREATE INDEX doi_check_document_idx ON doi_check (document_id, checked_at);
CREATE INDEX document_article_idx ON document (article_id);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('doi_check','', 'One verification of a document''s DOI candidate against an external source (Crossref). Append-only; the latest row per document is the current state.'),
 ('doi_check','outcome', 'confirmed = the source record matches the PDF (an article is linked); not_found = the source does not know the DOI; mismatch = the record does not match the PDF, or the evidence is not enough.'),
 ('doi_check','reasons', 'JSON list of the reasons for the outcome, in Italian, as shown to the user.'),
 ('doi_check','details', 'JSON with every comparison (title, authors, year, journal/ISSN, pages, type) and the source values used.'),
 ('doi_check','rules_version', 'Version of the comparison rules; re-evaluating stored responses with new rules adds a new row.'),
 ('doi_check','source_snapshot_id', 'The raw response (also 404 responses) the verification was based on.');
