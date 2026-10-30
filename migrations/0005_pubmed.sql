-- ORPHEUS migration 0005 — PubMed identity and indexing of the confirmed articles (T-009)
-- Approved by the user on 2026-10-11 ("0005: sì", plan in TASKS.md "Piano T-009").
-- pubmed_check: one row per check of an article's DOI against PubMed (ESearch, then EFetch). Rows are never
-- changed or deleted: a later check adds a new row and the latest row per article is the current state.
-- Both raw answers are kept as source_snapshot rows (the EFetch answer may hold up to 200 records).
-- article.pmid, article_mesh and article_publication_type are written only by a 'confirmed' check
-- (rules pubmed-v1: exactly one PMID, and the PubMed record carries the same DOI as the article).

CREATE TABLE pubmed_check (
    id                 TEXT PRIMARY KEY,
    article_id         TEXT NOT NULL REFERENCES article(id),
    doi                TEXT NOT NULL CHECK (doi = lower(doi)),
    pmid               TEXT CHECK (pmid IS NULL OR (length(pmid) BETWEEN 1 AND 9 AND pmid NOT GLOB '*[^0-9]*' AND pmid NOT GLOB '0*')),
    outcome            TEXT NOT NULL CHECK (outcome IN ('confirmed','not_found','mismatch','ambiguous')),
    reasons            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(reasons)),
    details            TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details)),
    rules_version      TEXT NOT NULL,
    source_id          TEXT NOT NULL REFERENCES source(id),
    search_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    fetch_snapshot_id  TEXT REFERENCES source_snapshot(id),
    checked_at         TEXT NOT NULL,
    CHECK (outcome NOT IN ('confirmed','mismatch') OR (pmid IS NOT NULL AND fetch_snapshot_id IS NOT NULL)),
    CHECK (outcome NOT IN ('not_found','ambiguous') OR pmid IS NULL)
) STRICT;

CREATE TRIGGER pubmed_check_no_update BEFORE UPDATE ON pubmed_check
BEGIN SELECT RAISE(ABORT, 'pubmed_check is append-only (UPDATE not allowed)'); END;
CREATE TRIGGER pubmed_check_no_delete BEFORE DELETE ON pubmed_check
BEGIN SELECT RAISE(ABORT, 'pubmed_check is append-only (DELETE not allowed)'); END;

CREATE INDEX pubmed_check_article_idx ON pubmed_check (article_id, checked_at);

CREATE TABLE article_mesh (
    article_id         TEXT NOT NULL REFERENCES article(id),
    descriptor_ui      TEXT NOT NULL CHECK (descriptor_ui GLOB 'D[0-9]*' AND substr(descriptor_ui, 2) NOT GLOB '*[^0-9]*'),
    descriptor_name    TEXT NOT NULL,
    major_topic        INTEGER NOT NULL CHECK (major_topic IN (0,1)),
    qualifiers         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(qualifiers)),
    pubmed_check_id    TEXT NOT NULL REFERENCES pubmed_check(id),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    PRIMARY KEY (article_id, descriptor_ui)
) STRICT;

CREATE INDEX article_mesh_descriptor_idx ON article_mesh (descriptor_ui);

CREATE TABLE article_publication_type (
    article_id         TEXT NOT NULL REFERENCES article(id),
    type_ui            TEXT NOT NULL CHECK (type_ui GLOB 'D[0-9]*' AND substr(type_ui, 2) NOT GLOB '*[^0-9]*'),
    type_name          TEXT NOT NULL,
    pubmed_check_id    TEXT NOT NULL REFERENCES pubmed_check(id),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshot(id),
    PRIMARY KEY (article_id, type_ui)
) STRICT;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('pubmed_check','', 'One check of an article''s DOI against PubMed (NCBI E-utilities). Append-only; the latest row per article is the current state.'),
 ('pubmed_check','outcome', 'confirmed = one PMID whose PubMed record has the same DOI (article.pmid, MeSH and publication types written); not_found = PubMed has no PMID for the DOI; mismatch = the only PMID found has no DOI or another DOI; ambiguous = more than one PMID, none taken.'),
 ('pubmed_check','pmid', 'The PMID found (confirmed or mismatch); NULL for not_found and ambiguous.'),
 ('pubmed_check','reasons', 'JSON list of the reasons for the outcome, in Italian, as shown to the user.'),
 ('pubmed_check','details', 'JSON: the PMIDs returned by ESearch, the DOIs of the PubMed record, the title, journal and year read, the PubMed correction notices (retraction, erratum, expression of concern).'),
 ('pubmed_check','rules_version', 'Version of the rules (pubmed-v1).'),
 ('pubmed_check','search_snapshot_id', 'The raw ESearch answer (JSON).'),
 ('pubmed_check','fetch_snapshot_id', 'The raw EFetch answer (XML) holding the record; NULL when no record was fetched (not_found, ambiguous).'),
 ('article_mesh','', 'MeSH headings of an article as indexed in PubMed, from the latest confirmed pubmed_check (replaced when a newer confirmed check arrives).'),
 ('article_mesh','major_topic', '1 when the descriptor or one of its qualifiers is a major topic (PubMed [majr]).'),
 ('article_mesh','qualifiers', 'JSON list of {ui, name, major} subheadings attached to the descriptor.'),
 ('article_publication_type','', 'PubMed publication types of an article (Journal Article, Review, Randomized Controlled Trial, Retracted Publication …), from the latest confirmed pubmed_check.');
