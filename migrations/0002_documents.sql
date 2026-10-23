-- ORPHEUS migration 0002 — local documents (PDF articles and author guidelines)
-- A document is a file ORPHEUS has read. Its identity as an `article` is NOT assumed:
-- the DOI found in the PDF stays a candidate until verified against Crossref/PubMed.

CREATE TABLE document (
    id                   TEXT PRIMARY KEY,
    source_snapshot_id   TEXT NOT NULL UNIQUE REFERENCES source_snapshot(id),
    kind                 TEXT NOT NULL CHECK (kind IN ('article','author_guidelines','other')),
    original_path        TEXT NOT NULL,
    file_sha256          TEXT NOT NULL UNIQUE CHECK (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    page_count           INTEGER NOT NULL CHECK (page_count >= 0),
    extractor            TEXT NOT NULL,
    text_status          TEXT NOT NULL CHECK (text_status IN ('ok','partial','no_text')),
    pdf_title            TEXT,
    pdf_author           TEXT,
    doi_candidate        TEXT CHECK (doi_candidate IS NULL OR doi_candidate = lower(doi_candidate)),
    doi_candidate_source TEXT NOT NULL CHECK (doi_candidate_source IN ('pdf_metadata','first_page','ambiguous','none')),
    article_id           TEXT REFERENCES article(id),
    journal_id           TEXT REFERENCES journal(id),
    ingested_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (doi_candidate_source IN ('pdf_metadata','first_page') OR doi_candidate IS NULL)
) STRICT;

CREATE TABLE document_page (
    document_id TEXT NOT NULL REFERENCES document(id),
    page_no     INTEGER NOT NULL CHECK (page_no >= 1),
    text        TEXT NOT NULL,
    char_count  INTEGER NOT NULL CHECK (char_count >= 0),
    PRIMARY KEY (document_id, page_no)
) STRICT;

CREATE TABLE document_doi (
    document_id TEXT NOT NULL REFERENCES document(id),
    page_no     INTEGER NOT NULL CHECK (page_no >= 1),
    doi         TEXT NOT NULL CHECK (doi = lower(doi)),
    PRIMARY KEY (document_id, page_no, doi)
) STRICT;

CREATE VIRTUAL TABLE page_fts USING fts5(
    text, content='document_page', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');

CREATE TRIGGER page_fts_ai AFTER INSERT ON document_page BEGIN
    INSERT INTO page_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER page_fts_ad AFTER DELETE ON document_page BEGIN
    INSERT INTO page_fts(page_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER page_fts_au AFTER UPDATE OF text ON document_page BEGIN
    INSERT INTO page_fts(page_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO page_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE INDEX document_doi_doi_idx ON document_doi (doi);
CREATE INDEX document_kind_idx ON document (kind);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('document','', 'A PDF ORPHEUS has read (article or author guidelines), linked to the snapshot of the exact file.'),
 ('document','doi_candidate', 'DOI read from the PDF metadata or first page. A candidate only: an article is linked after verification.'),
 ('document','text_status', 'ok = text on every page; partial = some pages empty; no_text = probably scanned, needs OCR.'),
 ('document_page','', 'Extracted text per page; evidence locations point to (document_id, page, char offsets) in this text.'),
 ('document_doi','', 'Every DOI string found on each page (mostly references); candidates for the citation graph, not verified.');
