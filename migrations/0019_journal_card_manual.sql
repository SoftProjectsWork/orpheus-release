-- ORPHEUS migration 0019 — T-052: schede delle riviste modificabili e inseribili a mano dal cruscotto
-- APPROVED by the user on 2026-10-28 (Europe/Rome), manual session of 00:10: "Approvo" (proposal shown as
-- MIGRAZIONE_0019_PROPOSTA.sql, tested on a copy of the PC database before the installation).
--
-- Solo aggiunte: una tabella nuova, nessuna riga esistente toccata. Le schede scritte dai PDF (journal_card,
-- journal_card_value) restano come sono, con pagina e frase. Un valore scritto dall'utente nel cruscotto:
--   - corregge o completa un campo di una scheda (vale al posto dei valori del PDF con lo stesso campo e tipo di articolo),
--   - oppure aggiunge un campo a una rivista che non ha ancora il PDF delle linee guida (scheda solo manuale),
--   - ha sempre la fonte (indirizzo della pagina, PDF e pagina, o nota) e la data in cui l'utente l'ha letta.
-- Correggere di nuovo = riga nuova (la vecchia 'superseded'); togliere = riga 'withdrawn'. Mai DELETE.

CREATE TABLE journal_card_manual (
    id            TEXT PRIMARY KEY,                                   -- jcm_<ULID>
    journal_id    TEXT NOT NULL REFERENCES journal(id),
    field         TEXT NOT NULL,                                      -- campo di journal-cards-v1
    article_type  TEXT,                                               -- tipo di articolo; 'tutti i tipi'; NULL = campo della rivista
    item          INTEGER NOT NULL DEFAULT 1 CHECK (item >= 1),       -- più valori dello stesso campo (es. altri_elementi)
    action        TEXT NOT NULL CHECK (action IN ('set','not_available')),
    value         TEXT,
    number        REAL,
    unit          TEXT,
    source        TEXT NOT NULL CHECK (length(trim(source)) > 0),     -- dove l'utente l'ha letto
    read_on       TEXT NOT NULL CHECK (read_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    note          TEXT,
    replaces_card_id TEXT REFERENCES journal_card(id),                -- la scheda dal PDF che corregge (se c'è)
    status        TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','superseded','withdrawn')),
    supersedes_id TEXT REFERENCES journal_card_manual(id),
    actor         TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (action = 'not_available' OR (value IS NOT NULL AND length(trim(value)) > 0)),
    CHECK (action <> 'not_available' OR (value IS NULL AND number IS NULL))
) STRICT;

CREATE UNIQUE INDEX journal_card_manual_one_current
    ON journal_card_manual (journal_id, field, coalesce(article_type, ''), item) WHERE status = 'current';
CREATE INDEX journal_card_manual_journal_idx ON journal_card_manual (journal_id, status);

CREATE TRIGGER journal_card_manual_no_delete BEFORE DELETE ON journal_card_manual
BEGIN SELECT RAISE(ABORT, 'journal_card_manual rows are never deleted (superseded or withdrawn instead)'); END;

-- dopo la scrittura cambia solo lo stato (current -> superseded / withdrawn)
CREATE TRIGGER journal_card_manual_only_status BEFORE UPDATE ON journal_card_manual
WHEN NEW.id IS NOT OLD.id OR NEW.journal_id IS NOT OLD.journal_id OR NEW.field IS NOT OLD.field
  OR NEW.article_type IS NOT OLD.article_type OR NEW.item IS NOT OLD.item OR NEW.action IS NOT OLD.action
  OR NEW.value IS NOT OLD.value OR NEW.number IS NOT OLD.number OR NEW.unit IS NOT OLD.unit
  OR NEW.source IS NOT OLD.source OR NEW.read_on IS NOT OLD.read_on OR NEW.note IS NOT OLD.note
  OR NEW.replaces_card_id IS NOT OLD.replaces_card_id OR NEW.supersedes_id IS NOT OLD.supersedes_id
  OR NEW.actor IS NOT OLD.actor OR NEW.created_at IS NOT OLD.created_at OR OLD.status <> 'current'
BEGIN SELECT RAISE(ABORT, 'journal_card_manual: only current -> superseded/withdrawn can change'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('journal_card_manual','', 'Value of a journal card written by the user in the dashboard (T-052): corrects or completes a field of the card from the PDF, or fills a card for a journal without guideline PDF. Always with source and date read; never deleted (superseded / withdrawn).'),
 ('journal_card_manual','field', 'Field of journal-cards-v1 (same names as journal_card_value.field).'),
 ('journal_card_manual','article_type', 'Article type the value refers to; "tutti i tipi" for every type; NULL for a field of the journal.'),
 ('journal_card_manual','item', 'Number of the value when a field has more than one (e.g. altri_elementi); 1 otherwise.'),
 ('journal_card_manual','action', 'set = the value below; not_available = the user says the journal does not state it.'),
 ('journal_card_manual','value', 'The value as written by the user (short); NULL for not_available.'),
 ('journal_card_manual','number', 'First number of the value for the numeric fields (word, reference, figure limits).'),
 ('journal_card_manual','unit', 'Unit of the value (e.g. words).'),
 ('journal_card_manual','source', 'Where the user read it: web address, PDF and page, or a note.'),
 ('journal_card_manual','read_on', 'Date (YYYY-MM-DD) the user read the source.'),
 ('journal_card_manual','note', 'Note of the user, if any.'),
 ('journal_card_manual','replaces_card_id', 'Current journal_card (from the PDF) the value corrects, when there is one.'),
 ('journal_card_manual','status', 'current; superseded by a newer value of the same field; withdrawn by the user.'),
 ('journal_card_manual','supersedes_id', 'The manual value this one replaces, if any.'),
 ('journal_card_manual','actor', 'Who wrote it (dashboard).');
