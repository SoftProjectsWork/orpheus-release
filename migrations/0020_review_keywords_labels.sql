-- ORPHEUS migration 0020 — EURYDIKE: parole chiave della revisione ed etichette dei record (screening, «come Rayyan»)
-- APPROVED by the user on 2026-11-03 (Europe/Rome), session of 14:14: "Approvo" (text shown in the session before writing).
--
-- Solo aggiunte: due tabelle nuove, nessuna riga esistente toccata.
-- review_keyword: parole che l'utente vuole vedere evidenziate nel titolo e nell'abstract (verde = per includere,
--   rosso = per escludere) e usare come filtri. Togliere una parola = removed_at (la riga resta nella storia).
-- record_label: etichette libere dell'utente su un record (es. «da rileggere», «pilota»). Togliere = removed_at.
-- Mai DELETE.

CREATE TABLE review_keyword (
    id          TEXT PRIMARY KEY,                                          -- rkw_
    review_id   TEXT NOT NULL REFERENCES review(id),
    kind        TEXT NOT NULL CHECK (kind IN ('include','exclude')),
    word        TEXT NOT NULL CHECK (length(trim(word)) BETWEEN 1 AND 80),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    removed_at  TEXT
) STRICT;
CREATE UNIQUE INDEX review_keyword_one ON review_keyword (review_id, kind, lower(word)) WHERE removed_at IS NULL;

CREATE TABLE record_label (
    id          TEXT PRIMARY KEY,                                          -- rlb_
    record_id   TEXT NOT NULL REFERENCES review_record(id),
    label       TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 40),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    removed_at  TEXT
) STRICT;
CREATE UNIQUE INDEX record_label_one ON record_label (record_id, lower(label)) WHERE removed_at IS NULL;
CREATE INDEX record_label_record_idx ON record_label (record_id);

CREATE TRIGGER review_keyword_no_delete BEFORE DELETE ON review_keyword
BEGIN SELECT RAISE(ABORT, 'review_keyword rows are never deleted (set removed_at)'); END;
CREATE TRIGGER record_label_no_delete BEFORE DELETE ON record_label
BEGIN SELECT RAISE(ABORT, 'record_label rows are never deleted (set removed_at)'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('review_keyword','', 'Words of a review highlighted in titles and abstracts during the screening (include = green, exclude = red) and used as filters; a word taken away keeps its row (removed_at). Never a decision.'),
 ('record_label','', 'Free labels of the user on one record (e.g. to re-read, pilot); a label taken away keeps its row (removed_at). Never a decision.');
