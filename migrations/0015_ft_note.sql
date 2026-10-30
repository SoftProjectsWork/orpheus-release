-- ORPHEUS migration 0015 — EURYDIKE: the user's own reason for a full-text decision (T-051c)
-- APPROVED by the user on 2026-10-20 01:05 (Europe/Rome), manual session: "approvo" (proposal shown at 01:05 as
-- EURYDIKE_MIGRAZIONE_0015.sql, asked by the user at 01:01).
--
-- Only an addition: one nullable column. The criterion of the protocol stays mandatory for exclude / not_retrieved
-- (CHECK of 0014); ft_note is the free text written by the user next to it (in English, R11).

ALTER TABLE review_study ADD COLUMN ft_note TEXT CHECK (ft_note IS NULL OR length(trim(ft_note)) > 0);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('review_study','ft_note', 'Free-text reason written by the user for the full-text decision, next to the criterion of the protocol (ft_criterion_id); NULL when none. Claude''s reasons are in ft_proposal.');
