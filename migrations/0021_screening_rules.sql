-- ORPHEUS migration 0021 — EURYDIKE: regole di massa dello screening (solo proposte) e tipi di pubblicazione
-- APPROVED by the user on 2026-11-03 14:49 (Europe/Rome): "Approvo" (text shown in the session before writing).
--
-- Solo aggiunte: una colonna nuova (vuota) e due tabelle nuove; nessuna riga esistente toccata. Mai DELETE.
-- review_record.pub_types: i tipi di pubblicazione dell'esportazione (PubMed PT, Scopus M3/Document Type, WoS DT),
--   lista JSON; per i record già importati si riempie solo con un pulsante dell'utente (rilettura dei file di ricerca).
-- screening_rule: una regola (parole nel titolo/abstract, anno, tipo di pubblicazione) che PROPONE «escludi» con un
--   criterio; mai una decisione. Togliere una regola = removed_at. Ogni regola ha la sua riga nel registro (review_log).
-- screening_rule_answer: la risposta di un revisore alla proposta su un record: confermata (= la sua decisione
--   «escludi», origine 'rule', in screening_decision) o rifiutata. Una nuova risposta sostituisce la vecchia.

ALTER TABLE review_record ADD COLUMN pub_types TEXT CHECK (pub_types IS NULL OR json_valid(pub_types));

CREATE TABLE screening_rule (
    id            TEXT PRIMARY KEY,                                        -- srl_
    review_id     TEXT NOT NULL REFERENCES review(id),
    kind          TEXT NOT NULL CHECK (kind IN ('words','year','pubtype')),
    params        TEXT NOT NULL CHECK (json_valid(params)),
    criterion_id  TEXT NOT NULL REFERENCES review_criterion(id),
    log_id        TEXT NOT NULL REFERENCES review_log(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    removed_at    TEXT
) STRICT;
CREATE INDEX screening_rule_review_idx ON screening_rule (review_id);

CREATE TABLE screening_rule_answer (
    id            TEXT PRIMARY KEY,                                        -- sra_
    rule_id       TEXT NOT NULL REFERENCES screening_rule(id),
    record_id     TEXT NOT NULL REFERENCES review_record(id),
    reviewer_id   TEXT NOT NULL REFERENCES review_reviewer(id),
    answer        TEXT NOT NULL CHECK (answer IN ('confirmed','rejected')),
    decision_id   TEXT REFERENCES screening_decision(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by TEXT REFERENCES screening_rule_answer(id),
    CHECK (answer <> 'confirmed' OR decision_id IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX screening_rule_answer_one ON screening_rule_answer (rule_id, record_id, reviewer_id) WHERE superseded_by IS NULL;

CREATE TRIGGER screening_rule_no_delete BEFORE DELETE ON screening_rule
BEGIN SELECT RAISE(ABORT, 'screening_rule rows are never deleted (set removed_at)'); END;
CREATE TRIGGER screening_rule_answer_no_delete BEFORE DELETE ON screening_rule_answer
BEGIN SELECT RAISE(ABORT, 'screening_rule_answer rows are never deleted (supersede them)'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('review_record','pub_types', 'Publication types of the search export (PubMed PT, Scopus M3 or Document Type, WoS DT) as a JSON list; NULL = not read.'),
 ('screening_rule','', 'A bulk rule of the screening (words in title/abstract, year, publication type) that PROPOSES exclude with a criterion; never a decision. A rule taken away keeps its row (removed_at).'),
 ('screening_rule_answer','', 'The answer of one reviewer to a rule proposal on one record: confirmed (his exclude decision, origin rule) or rejected; a new answer supersedes the old one.');
