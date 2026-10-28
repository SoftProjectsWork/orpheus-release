-- ORPHEUS migration 0017 — T-056: diario delle sottomissioni e lezioni dai revisori
-- APPROVED by the user on 2026-10-27 17:20 (Europe/Rome), manual session: "Approvo" (proposal shown at 17:19 as
-- MIGRAZIONE_0017_PROPOSTA.sql).
--
-- Decisioni dell'utente (2026-10-27): b) sottomissioni e decisioni si registrano nel cruscotto (finestre "Nuova
-- sottomissione", "Aggiungi decisione" con la lettera incollata); c) Claude in sessione classifica i commenti, l'utente
-- conferma o corregge nel cruscotto; d) lettere e commenti interi si conservano solo sul PC (R10); e) anche le
-- sottomissioni passate, a ritroso.
--
-- R10: lettere e commenti restano nel database del PC: mai su GitHub, nei registri, nel Project, in una chat esterna, né
-- nel pacchetto dei colleghi (sharing.py non li legge; un test lo verifica). Solo aggiunte: sei tabelle nuove, nessuna
-- modifica a tabelle o righe esistenti. Nessuna riga si cancella (trigger); una correzione è una riga nuova che
-- sostituisce la vecchia (superseded_by), come in card_item.

-- Un articolo tuo (il lavoro), che può passare per più riviste.
CREATE TABLE my_manuscript (
    id           TEXT PRIMARY KEY,                                          -- msr_
    label        TEXT NOT NULL UNIQUE CHECK (length(trim(label)) BETWEEN 1 AND 120),  -- il nome che usi tu
    title        TEXT,
    article_kind TEXT CHECK (article_kind IS NULL OR article_kind IN ('originale','revisione','protocollo','altro')),
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- Una sottomissione a una rivista (anche a ritroso).
CREATE TABLE submission (
    id                TEXT PRIMARY KEY,                                     -- sub_
    manuscript_id     TEXT NOT NULL REFERENCES my_manuscript(id),
    journal_id        TEXT REFERENCES journal(id),                          -- NULL se la rivista non è nell'elenco
    journal_title     TEXT NOT NULL CHECK (length(trim(journal_title)) > 0),
    article_type      TEXT,                                                 -- come lo chiama la rivista
    manuscript_number TEXT,                                                 -- numero di protocollo della rivista
    submitted_on      TEXT NOT NULL CHECK (submitted_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
    previous_id       TEXT REFERENCES submission(id),                       -- da quale sottomissione viene (rifiuto, trasferimento)
    note              TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by     TEXT REFERENCES submission(id)
) STRICT;
CREATE INDEX submission_manuscript_idx ON submission (manuscript_id);
CREATE INDEX submission_journal_idx ON submission (journal_id);

-- La lettera di decisione, intera, come incollata (solo sul PC).
CREATE TABLE submission_letter (
    id            TEXT PRIMARY KEY,                                         -- sle_
    submission_id TEXT NOT NULL REFERENCES submission(id),
    received_on   TEXT NOT NULL CHECK (received_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
    text          TEXT NOT NULL CHECK (length(trim(text)) > 0),
    text_sha256   TEXT NOT NULL CHECK (length(text_sha256) = 64 AND text_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (submission_id, text_sha256)
) STRICT;

-- La storia della sottomissione: un evento per riga, con la data.
CREATE TABLE submission_event (
    id            TEXT PRIMARY KEY,                                         -- sev_
    submission_id TEXT NOT NULL REFERENCES submission(id),
    event         TEXT NOT NULL CHECK (event IN ('inviato','rifiutato_senza_revisione','in_revisione',
                                                 'revisione_maggiore','revisione_minore','revisione_inviata',
                                                 'accettato','rifiutato','ritirato','trasferito')),
    event_on      TEXT NOT NULL CHECK (event_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),
    round         INTEGER NOT NULL DEFAULT 1 CHECK (round >= 1),            -- giro di revisione
    letter_id     TEXT REFERENCES submission_letter(id),
    due_on        TEXT CHECK (due_on IS NULL OR due_on GLOB '[12][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]'),  -- scadenza della revisione
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by TEXT REFERENCES submission_event(id)
) STRICT;
CREATE INDEX submission_event_sub_idx ON submission_event (submission_id, event_on);

-- I commenti della lettera, divisi dal codice (parse_comments, come in response-v1): testo esatto.
CREATE TABLE reviewer_comment (
    id          TEXT PRIMARY KEY,                                           -- rvc_
    letter_id   TEXT NOT NULL REFERENCES submission_letter(id),
    ord         INTEGER NOT NULL CHECK (ord >= 1),
    reviewer    TEXT NOT NULL CHECK (reviewer = 'ED' OR reviewer GLOB 'R[1-9]*'),  -- editor o revisore n
    comment_key TEXT NOT NULL,                                              -- es. R1.3
    text        TEXT NOT NULL CHECK (length(trim(text)) > 0),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (letter_id, ord)
) STRICT;

-- La classificazione di un commento (contratto feedback-v1): Claude propone (ai), tu confermi o correggi.
CREATE TABLE comment_label (
    id              TEXT PRIMARY KEY,                                       -- cml_
    comment_id      TEXT NOT NULL REFERENCES reviewer_comment(id),
    contract        TEXT NOT NULL DEFAULT 'feedback-v1',
    category        TEXT NOT NULL CHECK (category IN ('novita_rilevanza','disegno','campione_potenza','statistica',
                                         'presentazione_risultati','discussione_limiti','scrittura_lingua','riferimenti',
                                         'linee_guida_reporting','etica_registrazione','fuori_ambito','formato_rivista',
                                         'altro')),
    severity        TEXT NOT NULL CHECK (severity IN ('bloccante','importante','minore')),
    followed        TEXT CHECK (followed IS NULL OR followed IN ('si','in_parte','no','non_ancora')),
    quote           TEXT NOT NULL CHECK (length(trim(quote)) > 0),          -- pezzo esatto del commento
    state           TEXT NOT NULL CHECK (state IN ('ai','confirmed','edited')),
    actor           TEXT NOT NULL CHECK (actor IN ('human','claude_session')),
    workflow_run_id TEXT REFERENCES workflow_run(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    superseded_by   TEXT REFERENCES comment_label(id),
    CHECK ((state = 'ai') = (actor = 'claude_session')),
    CHECK (state <> 'ai' OR workflow_run_id IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX comment_label_one_current ON comment_label (comment_id) WHERE superseded_by IS NULL;

CREATE TRIGGER my_manuscript_no_delete BEFORE DELETE ON my_manuscript BEGIN SELECT RAISE(ABORT, 'my_manuscript rows are never deleted'); END;
CREATE TRIGGER submission_no_delete BEFORE DELETE ON submission BEGIN SELECT RAISE(ABORT, 'submission rows are never deleted (supersede them)'); END;
CREATE TRIGGER submission_letter_no_delete BEFORE DELETE ON submission_letter BEGIN SELECT RAISE(ABORT, 'submission_letter rows are never deleted'); END;
CREATE TRIGGER submission_event_no_delete BEFORE DELETE ON submission_event BEGIN SELECT RAISE(ABORT, 'submission_event rows are never deleted (supersede them)'); END;
CREATE TRIGGER reviewer_comment_no_delete BEFORE DELETE ON reviewer_comment BEGIN SELECT RAISE(ABORT, 'reviewer_comment rows are never deleted'); END;
CREATE TRIGGER comment_label_no_delete BEFORE DELETE ON comment_label BEGIN SELECT RAISE(ABORT, 'comment_label rows are never deleted (supersede them)'); END;
CREATE TRIGGER submission_letter_text_fixed BEFORE UPDATE OF text, text_sha256 ON submission_letter BEGIN SELECT RAISE(ABORT, 'the text of a letter never changes: add a new letter'); END;
CREATE TRIGGER reviewer_comment_text_fixed BEFORE UPDATE OF text ON reviewer_comment BEGIN SELECT RAISE(ABORT, 'the text of a comment never changes'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('my_manuscript','', 'T-056: one of the user''s own papers (label chosen by the user); it can go through several journals. PC only (R10).'),
 ('submission','', 'T-056: a submission of a paper to a journal (also entered afterwards), with date and journal number; previous_id links a resubmission or transfer. PC only (R10).'),
 ('submission_letter','', 'T-056: the whole decision letter as pasted by the user, with its SHA-256; never changed, never shared (R10).'),
 ('submission_event','', 'T-056: one step of a submission (sent, desk reject, under review, major/minor revision, revision sent, accepted, rejected, withdrawn, transferred) with date, round and due date.'),
 ('reviewer_comment','', 'T-056: one comment of a decision letter (editor or reviewer n), split by code (parse_comments), exact text. PC only (R10).'),
 ('comment_label','', 'T-056: classification of a comment (contract feedback-v1): category, severity, followed or not, exact quote; proposed by Claude in a session (ai) and confirmed or corrected by the user.');
