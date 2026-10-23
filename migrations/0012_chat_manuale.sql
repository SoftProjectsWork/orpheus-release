-- orpheus-migration: foreign_keys=off
-- ORPHEUS migration 0012 — executor chat_manuale (ADR-0011, T-043)
-- Approved by the user on 2026-10-17 00:51 (manual session): "Accetto la modifica" of ADR-0006 (point 2: language
-- models also as chat_manuale = a free chat, no API and no paid subscription, used by hand by the user on a work packet;
-- the answer passes the same validators). Draft manuscripts are excluded from this executor.
--
-- SQLite cannot change a CHECK constraint in place, so `model` and `workflow_run` are rebuilt with the same columns,
-- the same rows and the wider CHECK (official procedure, https://sqlite.org/lang_altertable.html section 7: foreign keys
-- off for the rebuild, PRAGMA foreign_key_check before COMMIT; the first line of this file tells `orpheus migrate` to do
-- so). Tables that reference them (workflow_run -> model; validation_result, article_card -> workflow_run) keep their
-- references by name. Nothing is deleted or changed in the rows.

CREATE TABLE model_new (
    id          TEXT PRIMARY KEY,
    executor    TEXT NOT NULL CHECK (executor IN ('claude_session','local_llm','chat_manuale')),
    model_ref   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('candidate','verified','deprecated')),
    verified_at TEXT,
    UNIQUE (executor, model_ref)
) STRICT;
INSERT INTO model_new (id, executor, model_ref, status, verified_at)
    SELECT id, executor, model_ref, status, verified_at FROM model;
DROP TABLE model;
ALTER TABLE model_new RENAME TO model;

CREATE TABLE workflow_run_new (
    id                TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES task(id),
    agent_id          TEXT REFERENCES agent(id),
    executor          TEXT NOT NULL CHECK (executor IN ('deterministic','claude_session','local_llm','chat_manuale')),
    model_id          TEXT REFERENCES model(id),
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','timeout','cancelled')),
    parameters        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(parameters)),
    error             TEXT,
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK (validation_status IN ('pending','pass','fail','needs_human_review')),
    attempt           INTEGER CHECK (attempt IS NULL OR attempt >= 1),
    host              TEXT,
    pid               INTEGER,
    heartbeat_at      TEXT,
    commands          TEXT CHECK (commands IS NULL OR json_valid(commands)),
    interrupted       INTEGER NOT NULL DEFAULT 0 CHECK (interrupted IN (0,1)),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (executor = 'deterministic' OR model_id IS NOT NULL)
) STRICT;
INSERT INTO workflow_run_new (id, task_id, agent_id, executor, model_id, started_at, finished_at, status, parameters,
                              error, validation_status, attempt, host, pid, heartbeat_at, commands, interrupted)
    SELECT id, task_id, agent_id, executor, model_id, started_at, finished_at, status, parameters,
           error, validation_status, attempt, host, pid, heartbeat_at, commands, interrupted FROM workflow_run;
DROP TABLE workflow_run;
ALTER TABLE workflow_run_new RENAME TO workflow_run;
CREATE INDEX workflow_run_task_idx ON workflow_run (task_id, status);

UPDATE schema_doc SET description =
    'claude_session = Claude in an interactive session (user subscription); local_llm = a free model running on the PC; chat_manuale = a free chat used by hand by the user (ADR-0011). No paid APIs (ADR-0006).'
    WHERE table_name = 'model' AND column_name = 'executor';
UPDATE schema_doc SET description =
    'deterministic = code; claude_session / local_llm / chat_manuale = language model, whose output must pass the task validators (chat_manuale: ADR-0011, migration 0012).'
    WHERE table_name = 'workflow_run' AND column_name = 'executor';
