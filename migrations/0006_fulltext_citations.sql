-- ORPHEUS migration 0006 — open-access full texts from Europe PMC (T-010) and verified reference lists (T-014)
-- Approved by the user on 2026-10-11 (interactive session, after the real counts of europepmc.cmd and
-- riferimenti.cmd): "Anche i testi (0006)" and "Sì, verificali". Plans in TASKS.md "Piano T-010" and "Piano T-014".
--
-- fulltext_check: one row per look-up of an article's DOI in Europe PMC. Append-only (never changed or deleted);
--   the latest row per article is the current state. The raw search answer is always kept; the JATS full text
--   is kept (fetch_snapshot_id, text_stored = 1) only when the JATS itself carries a Creative Commons licence.
-- article_section: the sections of a stored JATS full text (one row per section, abstract = ord 0), searchable
--   with section_fts. Derived data: replaced when a newer stored text arrives.
-- citation (table of 0001): new columns to keep each reference of an article's own list (source, position, key)
--   and the outcome of its deterministic check (rules version, reasons, per-field comparison) — ADR-0004.

CREATE TABLE fulltext_check (
    id                 TEXT PRIMARY KEY,
    article_id         TEXT NOT NULL REFERENCES article(id),
    doi                TEXT NOT NULL CHECK (doi = lower(doi)),
    source_id          TEXT NOT NULL REFERENCES source(id),
    outcome            TEXT NOT NULL CHECK (outcome IN ('found','not_found','mismatch','ambiguous')),
    availability       TEXT NOT NULL CHECK (availability IN ('open_access','free_not_oa','metadata_only')),
    pmcid              TEXT CHECK (pmcid IS NULL OR (pmcid GLOB 'PMC[1-9]*' AND substr(pmcid, 4) NOT GLOB '*[^0-9]*')),
    licence            TEXT,
    licence_source     TEXT CHECK (licence_source IS NULL OR licence_source IN ('jats','search')),
    text_stored        INTEGER NOT NULL DEFAULT 0 CHECK (text_stored IN (0,1)),
    reasons            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reasons)),
    details            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details)),
    rules_version      TEXT NOT NULL,
    search_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    fetch_snapshot_id  TEXT REFERENCES source_snapshot(id),
    checked_at         TEXT NOT NULL,
    CHECK (outcome = 'found' OR availability = 'metadata_only'),
    CHECK (availability = 'metadata_only' OR pmcid IS NOT NULL),
    CHECK (text_stored = 0 OR (fetch_snapshot_id IS NOT NULL AND licence_source = 'jats')),
    CHECK ((licence IS NULL) = (licence_source IS NULL))
) STRICT;

CREATE TRIGGER fulltext_check_no_update BEFORE UPDATE ON fulltext_check
BEGIN SELECT RAISE(ABORT, 'fulltext_check is append-only (UPDATE not allowed)'); END;
CREATE TRIGGER fulltext_check_no_delete BEFORE DELETE ON fulltext_check
BEGIN SELECT RAISE(ABORT, 'fulltext_check is append-only (DELETE not allowed)'); END;

CREATE INDEX fulltext_check_article_idx ON fulltext_check (article_id, checked_at);

CREATE TABLE article_section (
    article_id         TEXT NOT NULL REFERENCES article(id),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    ord                INTEGER NOT NULL CHECK (ord >= 0),
    level              INTEGER NOT NULL CHECK (level >= 1),
    sec_type           TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    text               TEXT NOT NULL,
    words              INTEGER NOT NULL CHECK (words >= 0),
    fulltext_check_id  TEXT NOT NULL REFERENCES fulltext_check(id),
    PRIMARY KEY (article_id, source_snapshot_id, ord)
) STRICT;

CREATE VIRTUAL TABLE section_fts USING fts5(
    title, text, content='article_section', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');

CREATE TRIGGER section_fts_ai AFTER INSERT ON article_section BEGIN
    INSERT INTO section_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
END;
CREATE TRIGGER section_fts_ad AFTER DELETE ON article_section BEGIN
    INSERT INTO section_fts(section_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
END;
CREATE TRIGGER section_fts_au AFTER UPDATE ON article_section BEGIN
    INSERT INTO section_fts(section_fts, rowid, title, text) VALUES ('delete', old.rowid, old.title, old.text);
    INSERT INTO section_fts(rowid, title, text) VALUES (new.rowid, new.title, new.text);
END;

ALTER TABLE citation ADD COLUMN ref_source TEXT CHECK (ref_source IS NULL OR ref_source IN ('crossref_deposit','jats','manual'));
ALTER TABLE citation ADD COLUMN ref_position INTEGER CHECK (ref_position IS NULL OR ref_position >= 1);
ALTER TABLE citation ADD COLUMN ref_key TEXT;
ALTER TABLE citation ADD COLUMN rules_version TEXT;
ALTER TABLE citation ADD COLUMN reasons TEXT CHECK (reasons IS NULL OR json_valid(reasons));
ALTER TABLE citation ADD COLUMN checks TEXT CHECK (checks IS NULL OR json_valid(checks));

CREATE UNIQUE INDEX citation_ref_unique ON citation (citing_article_id, ref_source, ref_position) WHERE ref_source IS NOT NULL;
CREATE INDEX citation_cited_doi_idx ON citation (cited_doi);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('fulltext_check','', 'One look-up of an article''s DOI in Europe PMC (T-010). Append-only; the latest row per article is the current state.'),
 ('fulltext_check','outcome', 'found = one work with the same DOI (rules epmc-v1); not_found; mismatch = no record with this DOI, or a PMID different from the one PubMed confirmed; ambiguous = several works with the DOI.'),
 ('fulltext_check','availability', 'open_access = full text in Europe PMC, open-access subset; free_not_oa = full text in Europe PMC but not open access (e.g. author manuscript); metadata_only.'),
 ('fulltext_check','licence', 'Normalized licence (e.g. CC BY 4.0): from the JATS full text when read, otherwise from the search record.'),
 ('fulltext_check','text_stored', '1 when the JATS full text is kept (only with a Creative Commons licence written in the JATS itself) and its sections are in article_section.'),
 ('fulltext_check','details', 'JSON: PMID and PMCID of Europe PMC, open-access flags, licence of the search record and of the JATS (with the first words of the licence text), counts of sections, words, figures, tables and references.'),
 ('fulltext_check','search_snapshot_id', 'The raw search answer (JSON).'),
 ('fulltext_check','fetch_snapshot_id', 'The JATS full text (XML), kept only when text_stored = 1.'),
 ('article_section','', 'Sections of a stored open-access full text (JATS), in reading order; ord 0 is the abstract. Searchable with section_fts.'),
 ('article_section','level', '1 for a top-level section, 2 for its subsections, and so on.'),
 ('article_section','text', 'Paragraph text of the section, without figure and table captions, one paragraph per line.'),
 ('citation','ref_source', 'Where the reference list comes from: crossref_deposit (the list the publisher deposited with Crossref), jats (Europe PMC full text) or manual.'),
 ('citation','ref_position', 'Position of the reference in the citing article''s list (1 = first).'),
 ('citation','ref_key', 'Key of the reference in the source list (Crossref "key" or JATS ref id).'),
 ('citation','rules_version', 'Version of the comparison rules (cite-v1).'),
 ('citation','reasons', 'JSON list of the reasons for the verification status, in Italian.'),
 ('citation','checks', 'JSON: per-field comparison with the authoritative record (year, first author, title: true / false / null when not comparable) and where the reference metadata came from.');
