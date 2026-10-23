-- ORPHEUS migration 0011 — the queue of the work (Fase 4, T-035)
-- Approved by the user on 2026-10-15 14:35 (manual session): Fase 4 "Un solo doppio clic" (lavora.cmd does in order what
-- is missing and resumes after an interruption), "Migrazione 0011" (new columns in task and workflow_run).
--
-- task (table of 0001) gets: the step of `orpheus lavora` it belongs to (NULL for other tasks, e.g. the card packets),
--   who does it (executor: deterministic = ORPHEUS on the PC, claude_session, local_llm, human), how many times it was
--   started, the limit of attempts, the last error and when it changed.
-- workflow_run (table of 0001) gets: the attempt number, the PC and the process that ran it, the heartbeat (last sign of
--   life), the commands run with their exit codes and short results, and whether it was interrupted (PC switched off,
--   window closed): an interrupted run is closed as failed with interrupted = 1 and its task is started again.
-- Nothing is deleted.

ALTER TABLE task ADD COLUMN step TEXT;
ALTER TABLE task ADD COLUMN executor TEXT CHECK (executor IS NULL OR executor IN ('deterministic','claude_session','local_llm','human'));
ALTER TABLE task ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0);
ALTER TABLE task ADD COLUMN max_attempts INTEGER CHECK (max_attempts IS NULL OR max_attempts >= 1);
ALTER TABLE task ADD COLUMN last_error TEXT;
ALTER TABLE task ADD COLUMN updated_at TEXT;

ALTER TABLE workflow_run ADD COLUMN attempt INTEGER CHECK (attempt IS NULL OR attempt >= 1);
ALTER TABLE workflow_run ADD COLUMN host TEXT;
ALTER TABLE workflow_run ADD COLUMN pid INTEGER;
ALTER TABLE workflow_run ADD COLUMN heartbeat_at TEXT;
ALTER TABLE workflow_run ADD COLUMN commands TEXT CHECK (commands IS NULL OR json_valid(commands));
ALTER TABLE workflow_run ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0 CHECK (interrupted IN (0,1));

CREATE INDEX task_step_idx ON task (step, status);
CREATE INDEX workflow_run_task_idx ON workflow_run (task_id, status);

INSERT INTO schema_doc (table_name, column_name, description) VALUES
 ('task','step', 'Step of `orpheus lavora` (engine-v1, src/orpheus/engine.py), e.g. leggi, verifica_doi, vettori; NULL for other tasks.'),
 ('task','executor', 'Who does the task: deterministic (ORPHEUS on the PC), claude_session, local_llm, human.'),
 ('task','attempts', 'How many runs of the task were started (retries and resumptions included).'),
 ('task','max_attempts', 'Attempts of each command of a run before the step is reported as failed (engine-v1).'),
 ('task','last_error', 'Error of the last failed or interrupted run.'),
 ('task','updated_at', 'When the status of the task last changed.'),
 ('workflow_run','attempt', 'Number of this run among the runs of its task (1 = first).'),
 ('workflow_run','host', 'Name of the PC that ran it.'),
 ('workflow_run','pid', 'Process of `orpheus lavora` that ran it.'),
 ('workflow_run','heartbeat_at', 'Last sign of life while the commands of the step were running.'),
 ('workflow_run','commands', 'JSON: the commands run, exit code, attempts and a short result of each.'),
 ('workflow_run','interrupted', '1 when the run was found unfinished (PC switched off, window closed): closed as failed, the task starts again.');
