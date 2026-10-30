-- ORPHEUS migration 0003 — database identity and journal folder hint (ADR-0007)
-- Every knowledge base gets a random, immutable identity at creation. Copies made by
-- backup or export keep it, so ORPHEUS can tell "another copy of this database"
-- (compare their audit chains) from "a different database" (never mix them).

CREATE TABLE db_instance (
    singleton  INTEGER PRIMARY KEY CHECK (singleton = 1),
    id         TEXT NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

INSERT INTO db_instance (singleton, id) VALUES (1, lower(hex(randomblob(16))));

CREATE TRIGGER db_instance_no_update BEFORE UPDATE ON db_instance
BEGIN SELECT RAISE(ABORT, 'db_instance is immutable'); END;
CREATE TRIGGER db_instance_no_delete BEFORE DELETE ON db_instance
BEGIN SELECT RAISE(ABORT, 'db_instance is immutable'); END;

-- Folder name of the journal the PDF was filed under (inbox/riviste/<sigla>/...).
-- A hint from the user's filing, not a verified fact: the journal is confirmed from ISSN/DOI later.
ALTER TABLE document ADD COLUMN journal_hint TEXT
    CHECK (journal_hint IS NULL OR (length(journal_hint) BETWEEN 1 AND 40 AND journal_hint NOT GLOB '*[^A-Za-z0-9_-]*'));

CREATE INDEX document_journal_hint_idx ON document (journal_hint);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('db_instance','', 'Random identity of this knowledge base, fixed at creation and carried by every backup and export copy.'),
 ('document','journal_hint', 'Sigla of the journal folder the PDF was filed under (inbox/riviste/<sigla>/...); a hint, not a verified fact.'),
 ('document','original_path', 'Where the PDF was read from: relative to ORPHEUS_HOME when inside it, otherwise absolute.'),
 ('source_snapshot','storage_path', 'Archived copy of the content, relative to ORPHEUS_HOME (portable across PCs and drive letters).');
