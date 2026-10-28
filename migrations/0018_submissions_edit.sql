-- ORPHEUS migration 0018 — T-056b: correzioni del diario delle sottomissioni
-- APPROVED by the user on 2026-10-27 18:49 (Europe/Rome), manual session: "Approvo tutto" (proposal shown at 18:48
-- as MIGRAZIONE_0018_PROPOSTA.sql, tested on a copy of the PC database).
--
-- Richiesta dell'utente (2026-10-27 18:44, provando il cruscotto): poter modificare i passi inseriti per errore; le
-- righe della lettera non sono tutte commenti: i commenti (le motivazioni) li scrive l'utente, uno per volta (+), oppure
-- dice che la rivista non ha fatto commenti (-).
--
-- Solo aggiunte di colonne (nessuna riga cancellata, i trigger "no delete" restano). Un passo o un commento tolto
-- dall'utente resta nel database con la data in cui è stato tolto (removed_at) e non si vede più né si conta.

-- da dove viene un commento: 'auto' = diviso dal codice (0.57.0), 'user' = scritto dall'utente
ALTER TABLE reviewer_comment ADD COLUMN source TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto','user'));
ALTER TABLE reviewer_comment ADD COLUMN removed_at TEXT;
-- la rivista non ha fatto commenti in questa lettera (scelta dell'utente con "-")
ALTER TABLE submission_letter ADD COLUMN no_comments INTEGER NOT NULL DEFAULT 0 CHECK (no_comments IN (0, 1));
-- un passo inserito per errore e tolto dall'utente
ALTER TABLE submission_event ADD COLUMN removed_at TEXT;

-- I commenti divisi automaticamente dalla 0.57.0 e non ancora classificati si tolgono (decisione dell'utente: non tutte
-- le righe della lettera sono commenti); la lettera intera resta.
UPDATE reviewer_comment SET removed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
 WHERE source = 'auto' AND NOT EXISTS (SELECT 1 FROM comment_label l WHERE l.comment_id = reviewer_comment.id);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('reviewer_comment','source', 'auto = split by code (0.57.0, retired); user = written by the user (one reason or comment at a time).'),
 ('reviewer_comment','removed_at', 'When the user removed the comment (never deleted); NULL = current.'),
 ('submission_letter','no_comments', '1 when the user said the journal made no comment in this letter.'),
 ('submission_event','removed_at', 'When the user removed a step entered by mistake (never deleted); NULL = current.');
