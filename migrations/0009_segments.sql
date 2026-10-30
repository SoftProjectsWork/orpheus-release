-- ORPHEUS migration 0009 — text segments and their vectors (Fase 3, T-030/T-031)
-- Approved by the user on 2026-10-15 13:40 (manual session): "Migrazione 0009", multilingual local model,
-- benchmark "Claude propone, io controllo", claims and evidence after the search.
--
-- segment: a piece of the text of a COMPLETE article (its PDF), about one paragraph (rules segment-v1: running
--   headers/footers and page numbers left out, the bibliography left out, words split by line breaks or ligatures
--   joined, pieces of ~60-220 words that do not cross a section heading), with the pages it comes from and the section
--   heading it is under. Derived data: rebuilt when the rules change (a new rules value), never edited by hand.
-- segment_fts: full-text index of the segments (BM25), kept in step by triggers.
-- segment_vector: the embedding of a segment for one model (float32, little endian, L2-normalised), computed on the
--   PC with a free local model (no service, no cost).

CREATE TABLE segment (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES document(id),
    article_id   TEXT REFERENCES article(id),
    rules        TEXT NOT NULL,
    ord          INTEGER NOT NULL CHECK (ord >= 1),
    page_from    INTEGER NOT NULL CHECK (page_from >= 1),
    page_to      INTEGER NOT NULL CHECK (page_to >= page_from),
    section      TEXT,
    text         TEXT NOT NULL CHECK (length(text) > 0),
    words        INTEGER NOT NULL CHECK (words >= 1),
    text_sha256  TEXT NOT NULL CHECK (length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (document_id, rules, ord)
) STRICT;

CREATE INDEX segment_article_idx ON segment (article_id, rules);

CREATE VIRTUAL TABLE segment_fts USING fts5(
    text, content='segment', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');

CREATE TRIGGER segment_fts_ai AFTER INSERT ON segment BEGIN
    INSERT INTO segment_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER segment_fts_ad AFTER DELETE ON segment BEGIN
    INSERT INTO segment_fts(segment_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER segment_fts_au AFTER UPDATE OF text ON segment BEGIN
    INSERT INTO segment_fts(segment_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO segment_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE segment_vector (
    segment_id  TEXT NOT NULL REFERENCES segment(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL CHECK (dim BETWEEN 8 AND 4096),
    vector      BLOB NOT NULL CHECK (length(vector) = dim * 4),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (segment_id, model)
) STRICT;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('segment','', 'A piece of the text of a complete article (about one paragraph), with its pages and section; derived from document_page by rules segment-v1 (Fase 3, T-030). Searchable with segment_fts (BM25) and by meaning with segment_vector.'),
 ('segment','document_id', 'The PDF the text comes from (the complete-article PDF of the article).'),
 ('segment','article_id', 'The article of the PDF.'),
 ('segment','rules', 'Segmentation rules, e.g. segment-v1 (src/orpheus/segments.py); segments of older rules are replaced.'),
 ('segment','ord', 'Order of the segment in the document, from 1.'),
 ('segment','page_from', 'Page where the segment starts.'),
 ('segment','page_to', 'Page where the segment ends.'),
 ('segment','section', 'Section heading the segment is under (Introduction, Methods, Results, Discussion...), NULL before the first heading.'),
 ('segment','text', 'Text of the segment: whitespace collapsed, running headers, page numbers and the bibliography left out.'),
 ('segment','words', 'Number of words of the text.'),
 ('segment','text_sha256', 'SHA-256 of the text (a vector is recomputed only when the text changes).'),
 ('segment_vector','', 'Embedding of a segment for one model (float32 little endian, L2-normalised), computed on the PC with a free local model (T-031).'),
 ('segment_vector','model', 'Model id, e.g. intfloat/multilingual-e5-small (onnx/model.onnx).'),
 ('segment_vector','dim', 'Number of dimensions.'),
 ('segment_vector','vector', 'dim float32 values, little endian.');
