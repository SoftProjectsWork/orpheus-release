-- ORPHEUS migration 0010 — claims and evidence from the articles (Fase 3, T-033)
-- Approved by the user on 2026-10-15 14:05 (manual session): claims "Dalle 30 schede", appraisal "Livello semplice",
-- "Migrazione 0010", hand check of "Tutte, con CSV".
--
-- scientific_claim (table of 0001) gets: the article it was written from, the contract (claims-v1), language, order,
--   status current/superseded (claims are never deleted: a new file for the same article supersedes them), the
--   submitted file's SHA-256, the model, and the hand check (S/N).
-- evidence_record (table of 0001) gets: the PDF, page and verbatim quote (found on that page by the validator), the
--   segment (Fase 3) that contains the quote, the level of evidence of the study by the rules appraisal-v1
--   (alto / medio / basso, from design, number of participants and comparison group of the article card), the
--   rules and their reasons, and the hand check.

ALTER TABLE scientific_claim ADD COLUMN article_id TEXT REFERENCES article(id);
ALTER TABLE scientific_claim ADD COLUMN claim_version TEXT;
ALTER TABLE scientific_claim ADD COLUMN language TEXT;
ALTER TABLE scientific_claim ADD COLUMN ord INTEGER CHECK (ord IS NULL OR ord >= 1);
ALTER TABLE scientific_claim ADD COLUMN status TEXT NOT NULL DEFAULT 'current' CHECK (status IN ('current','superseded'));
ALTER TABLE scientific_claim ADD COLUMN file_sha256 TEXT CHECK (file_sha256 IS NULL OR (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'));
ALTER TABLE scientific_claim ADD COLUMN model_ref TEXT;
ALTER TABLE scientific_claim ADD COLUMN verified_by_human INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_human IN (0,1));
ALTER TABLE scientific_claim ADD COLUMN human_verdict TEXT CHECK (human_verdict IS NULL OR human_verdict IN ('right','wrong'));

ALTER TABLE evidence_record ADD COLUMN document_id TEXT REFERENCES document(id);
ALTER TABLE evidence_record ADD COLUMN page_no INTEGER CHECK (page_no IS NULL OR page_no >= 1);
ALTER TABLE evidence_record ADD COLUMN quote TEXT;
ALTER TABLE evidence_record ADD COLUMN segment_id TEXT REFERENCES segment(id) ON DELETE SET NULL;
ALTER TABLE evidence_record ADD COLUMN appraisal_level TEXT CHECK (appraisal_level IS NULL OR appraisal_level IN ('alto','medio','basso'));
ALTER TABLE evidence_record ADD COLUMN appraisal_rules TEXT;
ALTER TABLE evidence_record ADD COLUMN appraisal_reasons TEXT CHECK (appraisal_reasons IS NULL OR json_valid(appraisal_reasons));
ALTER TABLE evidence_record ADD COLUMN human_verdict TEXT CHECK (human_verdict IS NULL OR human_verdict IN ('right','wrong'));

CREATE INDEX scientific_claim_article_idx ON scientific_claim (article_id, status);
CREATE INDEX evidence_record_claim_idx ON evidence_record (claim_id);

CREATE TRIGGER scientific_claim_no_delete BEFORE DELETE ON scientific_claim
BEGIN SELECT RAISE(ABORT, 'scientific_claim rows are never deleted (status superseded instead)'); END;
CREATE TRIGGER evidence_record_no_delete BEFORE DELETE ON evidence_record
BEGIN SELECT RAISE(ABORT, 'evidence_record rows are never deleted'); END;

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('scientific_claim','article_id', 'The article the claim was written from (claims-v1: the claims of an article''s own results and conclusions).'),
 ('scientific_claim','claim_version', 'Contract of the claim file, e.g. claims-v1 (src/orpheus/claims.py).'),
 ('scientific_claim','language', 'Language of the statement (it).'),
 ('scientific_claim','ord', 'Order of the claim in its file.'),
 ('scientific_claim','status', 'current, or superseded by a newer file of the same article (never deleted).'),
 ('scientific_claim','file_sha256', 'SHA-256 of the submitted claims file.'),
 ('scientific_claim','model_ref', 'The model that wrote the claim (e.g. claude-opus-5-5).'),
 ('scientific_claim','verified_by_human', '1 when the user checked the claim by hand (T-033 check of all claims).'),
 ('scientific_claim','human_verdict', 'right / wrong, from the hand check.'),
 ('evidence_record','document_id', 'The PDF of the article where the quote is.'),
 ('evidence_record','page_no', 'Page of the PDF where the quote is (found there by the validator).'),
 ('evidence_record','quote', 'Continuous text copied from that page that supports (or contradicts) the claim.'),
 ('evidence_record','segment_id', 'The segment (Fase 3, segment-v1) that contains the quote, if any.'),
 ('evidence_record','appraisal_level', 'Level of evidence of the study by appraisal-v1: alto, medio, basso.'),
 ('evidence_record','appraisal_rules', 'Rules of the level, e.g. appraisal-v1 (src/orpheus/claims.py).'),
 ('evidence_record','appraisal_reasons', 'JSON: design read, participants, comparison group, steps applied.'),
 ('evidence_record','human_verdict', 'right / wrong, from the hand check of the claim.');
