"""Fase 4 — the queue of the work: `orpheus lavora` and lavora.cmd (rules engine-v1).

User's decisions of 2026-10-15 14:35: "Un solo doppio clic" (lavora.cmd does in order what is missing and, after an
interruption, starts again from where it stopped) and migration 0011 (task / workflow_run: step, executor, attempts,
heartbeat, commands, interrupted).

Steps, in this order. Each runs the command(s) of a double-click file as a separate process (`python -m orpheus ...`),
so every protection of the commands stays as it is: start-up check of the copy, backups before writing, checks:
  leggi, verifica_doi, riviste, verifica_pubmed, europepmc, citazioni, autori, schede, schede_riviste, segmenta,
  vettori, modello (0.24.1: the model of the search by meaning on a PC that has not it yet), affermazioni, report.
For each step ORPHEUS first counts what is missing (no network, nothing written). With nothing to do the step is
skipped and nothing is written, so a second run in a row writes nothing (idempotent). Otherwise:
  task          one per step with work (task.step, executor deterministic); reused while it is not done (failed or
                interrupted: the attempts go on), a new one after a done one;
  workflow_run  one per execution of the step (attempt n) with PC, process, heartbeat, the commands and their results;
  commands      one after the other; a command is tried again (3 times at most, after 15 s and 60 s) only when it gave no
                answer (crash, killed) or stopped because the network failed ("interrotto"); an error of the command
                (e-mail missing, source disabled) is not retried; exit code 4 (copy of the database older or divergent)
                stops the whole run;
  lock          a file in the PC's local folder (lavora.json): one run at a time on a PC; the lock of a process that no
                longer exists is taken over;
  interrupted   a run still "running" when a new run starts (PC switched off, window closed) is closed as failed with
                interrupted = 1 and its task starts again: the step is done again from what is still missing (every
                command skips what it already did).
A step that ended with errors of single items (code 3, e.g. HTTP 500 of a source) and still has the same amount to do
is not asked again for 6 hours ("rimandato", nothing written). The report step runs when the data changed after the
last report (audit events other than those of lavora).
The work for Claude and for the user (cards, claims, hand checks) is counted and listed, not run.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import (audit, card_store, claims, dedup, doi_search, europepmc_survey, journal_cards, journal_metrics,
               journal_table, layout,
               policy_changes, policy_versions, segments, semantic, verify_citations, verify_doi, verify_pubmed)
from .db import transaction
from .ids import new_id
from .ingest_pdf import sha256_file

RULES_VERSION = "engine-v1"
AGENT_NAME = "A1 Orchestrator"
LOCK_FILE = "lavora.json"
HASH_CACHE = "lavora_impronte.json"
MAX_ATTEMPTS = 3
WAITS = (15, 60)                       # seconds before the 2nd and the 3rd attempt of a command
HEARTBEAT_SECONDS = 15
LOCK_MAX_AGE_HOURS = 12                # a lock older than this is stale even if its process number is in use again
OTHER_HOST_MINUTES = 10
RETRY_AFTER_HOURS = 6                  # a step that ended with errors of single items (e.g. HTTP 500 of a source) and
                                       # has the same amount to do waits this long before it is asked again
OWN_ACTIONS = ("lavora_passo", "lavora_interrotto")
OUTCOMES = ("fatto", "fatto con errori", "non riuscito", "fermo")


@dataclass
class Env:
    conn: object
    home: Path
    project: object
    local_root: Path

    def journal_list(self) -> list[dict]:
        rows, _problems = layout.read_journal_list(self.home / self.project.storage.journals_csv)
        return rows


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    commands: tuple[tuple[str, ...], ...]
    pending: Callable[[Env], int]
    unit: str
    network: bool = False


# ---------------------------------------------------------------- what is missing (no network, nothing written)

def _hash_cache(env: Env) -> dict:
    try:
        return json.loads((env.local_root / HASH_CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_hash_cache(env: Env, cache: dict) -> None:
    try:
        env.local_root.mkdir(parents=True, exist_ok=True)
        tmp = env.local_root / (HASH_CACHE + ".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, env.local_root / HASH_CACHE)
    except OSError:
        pass


def pdfs_to_read(env: Env) -> int:
    """PDFs in the inbox whose content (SHA-256) is not in the database yet; the fingerprints are kept in the PC's local
    folder by path, size and modification time, so unchanged files are not read again."""
    items, _ignored = layout.plan_inbox(env.home, env.project.storage.inbox_dir)
    known = {r[0] for r in env.conn.execute("SELECT file_sha256 FROM document")}
    cache, fresh, n = _hash_cache(env), {}, 0
    for item in items:
        st = item.path.stat()
        key = str(item.path)
        hit = cache.get(key)
        sha = hit[2] if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns else sha256_file(item.path)
        fresh[key] = [st.st_size, st.st_mtime_ns, sha]
        n += sha not in known
    if fresh != cache:
        _save_hash_cache(env, fresh)
    return n


def dois_to_verify(env: Env) -> int:
    checked = set(verify_doi.latest_checks(env.conn))
    printed = {d["id"] for d in verify_doi.candidates(env.conn)}
    todo, _impossible, _skipped = doi_search.to_search(env.conn, env.journal_list(), "nuovi")
    return len(printed - checked) + len(todo)


def journals_to_write(env: Env) -> int:
    rows = env.journal_list()
    if not rows:
        return 0
    p = journal_table.plan(env.conn, env.home, rows)
    n = p["articoli_da_collegare"] + sum(1 for r in p["riviste"] if r["azione"] == "nuova")
    n += sum(len(r.get("tipi_da_completare") or []) for r in p["riviste"])
    locator = layout.PdfLocator(env.home, env.project.storage.inbox_dir, env.local_root)
    return n + policy_versions.plan(env.conn, rows, home=env.home, locator=locator)["da_scrivere"]


def journals_to_fix(env: Env) -> int:
    """T-054 passo 3: siglas, folders and URLs of the journal list to fix (sistema-riviste)."""
    from . import journal_folders as jf
    from .journal_list_write import JournalList, ListError
    try:
        jl = JournalList.load(env.home / env.project.storage.journals_csv)
    except ListError:
        return 0
    if not jl.data_rows():
        return 0
    loc = layout.PdfLocator(env.home, env.project.storage.inbox_dir, env.local_root)
    n = jf.plan_fix(env.conn, env.home, env.project.storage.inbox_dir, jl, loc)["da_fare"]
    loc.save()
    return n


def journals_to_recognise(env: Env) -> int:
    """T-054 passo 4: journals of confirmed articles not in the list, and PDFs of articoli_vari to move."""
    from . import journal_folders as jf
    from .journal_list_write import JournalList, ListError
    try:
        jl = JournalList.load(env.home / env.project.storage.journals_csv)
    except ListError:
        return 0
    inbox = env.project.storage.inbox_dir
    n = len(jf.plan_recognise(env.conn, env.home, jl, inbox_dir=inbox)["nuove"])
    loc = layout.PdfLocator(env.home, inbox, env.local_root)
    n += jf.plan_moves(env.conn, env.home, inbox, jl.data_rows(), loc)["da_fare"]
    loc.save()
    return n


def pubmed_to_ask(env: Env) -> int:
    return len(verify_pubmed.select(env.conn, "nuovi")[0])


def europepmc_to_ask(env: Env) -> int:
    return len(europepmc_survey.select(env.conn, "nuovi")[0])


def citations_to_verify(env: Env) -> int:
    return verify_citations.plan(env.conn, env.home)["articoli_da_verificare"]


def authors_to_write(env: Env) -> int:
    records, _problems = dedup.crossref_records(env.conn, env.home)
    return len(dedup.propose_authors(env.conn, records)["collegamenti"])


def waiting_files(folder: Path) -> tuple[int, int]:
    """(files to send, files refused and not changed since) in a da_inviare folder."""
    send = refused = 0
    for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        reasons = p.with_name(p.stem + ".motivi.txt")
        if reasons.exists() and reasons.stat().st_mtime >= p.stat().st_mtime:
            refused += 1
        else:
            send += 1
    return send, refused


def cards_to_send(env: Env) -> int:
    return waiting_files(card_store.to_send_dir(env.home))[0]


def journal_cards_to_send(env: Env) -> int:
    return waiting_files(journal_cards.to_send_dir(env.home))[0]


def metrics_to_write(env: Env) -> int:
    """Journals with SCImago metrics still to write (from the files in inbox/metriche) or without any OpenAlex metric
    (2026-10-17: metriche_riviste.cmd becomes a step, so that "Aggiorna riviste" does everything). No network."""
    js = journal_metrics.journals(env.conn)
    if not js:
        return 0
    files = []
    for p in journal_metrics.scimago_files(env.home, env.project.storage.inbox_dir):
        try:
            files.append(journal_metrics.read_scimago(p))
        except (OSError, journal_metrics.MetricsFileError):
            continue
    todo = {x["journal_id"] for x in journal_metrics.plan_scimago(env.conn, files, js) if x.get("nuove")}
    with_oa = {r[0] for r in env.conn.execute("SELECT DISTINCT journal_id FROM journal_metric WHERE provider = ?",
                                              (journal_metrics.OPENALEX_PROVIDER,))}
    return len(todo | {j["id"] for j in js if j["id"] not in with_oa})


def segments_to_write(env: Env) -> int:
    actions = segments.summary(segments.plan(env.conn))["azioni"]
    return actions["nuovi"] + actions["da rifare"]


def vectors_to_compute(env: Env) -> int:
    return len(semantic.missing_segments(env.conn))


def model_to_download(env: Env) -> int:
    """1 when the database has vectors (the search by meaning is in use) but this PC has not the model yet (the model
    lives in each PC's local folder: a second PC needs its own copy for cerca.cmd)."""
    if (semantic.model_dir(env.local_root) / semantic.MODEL_FILE).exists():
        return 0
    return int(env.conn.execute("SELECT EXISTS (SELECT 1 FROM segment_vector)").fetchone()[0])


def claims_to_send(env: Env) -> int:
    return waiting_files(claims.to_send_dir(env.home))[0] + claims.control_pending(env.conn, env.home)["da_fare"]


def data_seq(conn) -> int:
    """Last audit event that changed the data (lavora's own events do not count)."""
    marks = ",".join("?" for _ in OWN_ACTIONS)
    return conn.execute(f"SELECT coalesce(max(seq), 0) FROM audit_event WHERE action NOT IN ({marks})",
                        OWN_ACTIONS).fetchone()[0]


def report_to_write(env: Env) -> int:
    seq = data_seq(env.conn)
    if not seq:
        return 0
    row = env.conn.execute("""SELECT w.parameters FROM workflow_run w JOIN task t ON t.id = w.task_id
                              WHERE t.step = 'report' AND w.status = 'succeeded'
                              ORDER BY w.finished_at DESC, w.rowid DESC LIMIT 1""").fetchone()
    last = json.loads(row["parameters"]).get("dati_fino_a", 0) if row else None
    return int(last is None or seq > last)


STEPS: tuple[Step, ...] = (
    Step("sistema_riviste", "Sigle e cartelle delle riviste", (("sistema-riviste",),), journals_to_fix,
         "sigle, cartelle o indirizzi"),
    Step("leggi", "Lettura dei PDF nuovi", (("ingest-inbox",), ("separa-non-completi",)), pdfs_to_read, "PDF nuovi"),
    Step("verifica_doi", "Verifica dei DOI con Crossref",
         (("verify-dois",), ("cerca-doi",), ("verify-dois", "--rivaluta"), ("separa-non-completi",)),
         dois_to_verify, "PDF", network=True),
    Step("riconosci", "Riviste riconosciute dai PDF e articoli nelle cartelle delle riviste",
         (("riconosci-riviste",), ("sposta-articoli",)), journals_to_recognise, "riviste o PDF", network=True),
    Step("riviste", "Riviste e linee guida", (("scrivi-riviste",), ("linee-guida",)), journals_to_write,
         "articoli o linee guida"),
    Step("verifica_pubmed", "Verifica con PubMed", (("verifica-pubmed",),), pubmed_to_ask, "articoli", network=True),
    Step("europepmc", "Testi aperti di Europe PMC", (("europepmc", "--salva"),), europepmc_to_ask, "articoli",
         network=True),
    Step("citazioni", "Verifica delle bibliografie", (("verifica-citazioni",),), citations_to_verify, "articoli",
         network=True),
    Step("autori", "Autori", (("scrivi-autori",),), authors_to_write, "collegamenti"),
    Step("schede", "Schede degli articoli scritte da Claude", (("submit",),), cards_to_send, "file"),
    Step("schede_riviste", "Schede delle riviste scritte da Claude", (("schede-riviste",),), journal_cards_to_send,
         "file"),
    Step("metriche", "Metriche delle riviste (SCImago e OpenAlex)", (("metriche-riviste",),), metrics_to_write,
         "riviste", network=True),
    Step("segmenta", "Pezzi di testo degli articoli completi", (("segmenta",),), segments_to_write, "articoli"),
    Step("vettori", "Vettori per la ricerca per significato", (("vettori",),), vectors_to_compute, "pezzi"),
    Step("modello", "Modello per la ricerca per significato su questo PC (una volta, circa 0,5 GB)",
         (("vettori", "--solo-scarica"),), model_to_download, "modello", network=True),
    Step("affermazioni", "Affermazioni scritte da Claude e controllo a mano",
         (("affermazioni",), ("affermazioni-controllo",)), claims_to_send, "file o controlli"),
    Step("report", "Report HTML", (("report",),), report_to_write, "report"),
)
BY_KEY = {s.key: s for s in STEPS}

# Groups of steps in the order of use (2026-10-17, user: "unisci le funzioni create in base alla logica di utilizzo";
# buttons of the dashboard and double-click commands). "tutto" is lavora.cmd. Every group runs with the same engine:
# backup first, only what is missing, resumable.
GROUPS: dict[str, dict] = {
    "tutto": {"titolo": "Tutto quello che manca", "passi": tuple(s.key for s in STEPS)},
    "articoli": {"titolo": "Articoli nuovi: lettura e verifiche",
                 "passi": ("leggi", "verifica_doi", "riconosci", "riviste", "verifica_pubmed", "europepmc", "citazioni", "autori",
                           "segmenta", "vettori", "modello", "report")},
    "riviste": {"titolo": "Riviste: elenco, linee guida, schede e metriche",
                "passi": ("sistema_riviste", "riviste", "schede_riviste", "metriche", "report")},
    "conoscenza": {"titolo": "Lavoro di Claude: schede, affermazioni e ricerca",
                   "passi": ("schede", "segmenta", "vettori", "affermazioni", "report")},
}


# ---------------------------------------------------------------- lock (one run at a time on a PC)

class Busy(RuntimeError):
    def __init__(self, info: dict):
        super().__init__("lavora è già in corso su questo PC")
        self.info = info


def _age_seconds(stamp: str | None) -> float:
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds()


def local_time(stamp: str | None) -> str | None:
    """'2026-10-15T15:32:58.718Z' -> '15/10/2026 17:32' in the PC's time zone (PowerShell would turn ISO dates into
    month/day dates)."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone() \
            .strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return stamp


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":                                   # os.kill would terminate the process on Windows
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259             # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_alive(info: dict | None) -> bool:
    if not info:
        return False
    age = _age_seconds(info.get("battito"))
    if info.get("host") != socket.gethostname():
        return age < OTHER_HOST_MINUTES * 60
    return pid_alive(info.get("pid")) and age < LOCK_MAX_AGE_HOURS * 3600


class Lock:
    def __init__(self, root: Path, giro: str, owner_pid: int):
        self.path = Path(root) / LOCK_FILE
        self.giro, self.owner_pid = giro, owner_pid
        self._mutex = threading.Lock()

    def read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, info: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def acquire(self) -> dict | None:
        """Take the lock for this run; returns the stale lock that was taken over (None if there was none)."""
        old = self.read()
        if old and old.get("giro") != self.giro and lock_alive(old):
            raise Busy(old)
        now = audit.utc_now()
        started = old.get("iniziato") if old and old.get("giro") == self.giro else now
        self._write({"giro": self.giro, "pid": self.owner_pid, "host": socket.gethostname(), "iniziato": started,
                     "battito": now, "passo": None, "regole": RULES_VERSION})
        return old if old and old.get("giro") != self.giro else None

    def beat(self, step: str | None = None) -> None:
        with self._mutex:
            info = self.read() or {}
            if info.get("giro") not in (None, self.giro):
                return
            info.update({"giro": self.giro, "pid": info.get("pid", self.owner_pid), "host": socket.gethostname(),
                         "battito": audit.utc_now(), "passo": step})
            try:
                self._write(info)
            except OSError:
                pass

    def release(self) -> bool:
        info = self.read()
        if info and info.get("giro") == self.giro:
            self.path.unlink(missing_ok=True)
            return True
        return False


class _Heartbeat:
    def __init__(self, lock: Lock | None, step: str, every: float = HEARTBEAT_SECONDS):
        self.lock, self.step, self.every = lock, step, every
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if self.lock is not None:
            self.lock.beat(self.step)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.every):
            self.lock.beat(self.step)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False


# ---------------------------------------------------------------- running the commands

def run_orpheus(argv: list[str]) -> tuple[int, str, str]:
    """Run `python -m orpheus ARGV` as a separate process (the same Python as this one)."""
    env = dict(os.environ, PYTHONUTF8="1")
    p = subprocess.run([sys.executable, "-m", "orpheus", *argv], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, p.stdout, p.stderr


@dataclass
class CommandResult:
    argv: list[str]
    code: int | None = None
    data: dict | None = None
    attempts: int = 0
    seconds: float = 0.0
    stderr: str = ""
    raw: str = ""

    @property
    def failed(self) -> bool:
        return self.data is None or "errore" in self.data or "interrotto" in self.data

    def brief(self) -> dict:
        out: dict = {"comando": " ".join(self.argv), "codice": self.code, "tentativi": self.attempts,
                     "secondi": round(self.seconds, 1)}
        d = self.data or {}
        if self.data is None:
            out["errore"] = "nessuna risposta del comando" + (f": {self.stderr.strip()[-300:]}" if self.stderr.strip() else "")
        for k in ("errore", "interrotto"):
            if d.get(k):
                out[k] = str(d[k])[:500]
        numbers = {}
        for group in ("summary", "riepilogo", "scritto"):
            if isinstance(d.get(group), dict):
                numbers |= {k: v for k, v in d[group].items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for k in ("accettati", "rifiutati", "accettate", "rifiutate", "spostati", "da_scrivere", "righe", "file_trovati"):
            if isinstance(d.get(k), int) and not isinstance(d.get(k), bool):
                numbers[k] = d[k]
        if isinstance(d.get("errori"), list) and d["errori"]:
            numbers["errori"] = len(d["errori"])
        if numbers:
            out["numeri"] = numbers
        for k in ("file_html", "file_csv", "file_da_salvare", "modo"):
            if d.get(k):
                out[k] = d[k]
        return out


def _retry(res: CommandResult) -> bool:
    return res.code != 4 and (res.data is None or "interrotto" in res.data)


def run_command(argv: list[str], *, runner=run_orpheus, waits=WAITS, max_attempts: int = MAX_ATTEMPTS,
                sleep=time.sleep) -> CommandResult:
    res = CommandResult(list(argv))
    start = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            sleep(waits[min(attempt - 2, len(waits) - 1)] if waits else 0)
        res.attempts = attempt
        code, out, err = runner(list(argv))
        res.code, res.stderr, res.raw = code, err or "", (out or "")[-2000:]
        try:
            data = json.loads(out) if out and out.strip() else None
        except ValueError:
            data = None
        res.data = data if isinstance(data, dict) else None
        if not _retry(res):
            break
    res.seconds = time.monotonic() - start
    return res


def outcome(results: list[CommandResult]) -> str:
    if any(r.code == 4 for r in results):
        return "fermo"
    if any(r.failed for r in results):
        return "non riuscito"
    if any(r.code not in (0, None) for r in results):
        return "fatto con errori"
    return "fatto"


# ---------------------------------------------------------------- database bookkeeping

def _ensure_agent(conn) -> str:
    row = conn.execute("SELECT id FROM agent WHERE name = ?", (AGENT_NAME,)).fetchone()
    if row:
        return row[0]
    aid = new_id("agent")
    conn.execute("""INSERT INTO agent (id, name, layer, role, contract_version, status, config)
                    VALUES (?, ?, 'A', ?, ?, 'implemented', ?)""",
                 (aid, AGENT_NAME, "Passi di `orpheus lavora` in ordine: cosa manca, tentativi, ripresa dopo un'interruzione",
                  RULES_VERSION, json.dumps({"executor": "deterministic", "modulo": "orpheus.engine"})))
    return aid


def _task_for(conn, step: Step, project_id: str, agent_id: str, now: str) -> dict:
    row = conn.execute("""SELECT * FROM task WHERE step = ? AND status NOT IN ('done', 'cancelled')
                          ORDER BY created_at DESC, rowid DESC LIMIT 1""", (step.key,)).fetchone()
    if row:
        return dict(row)
    tid = new_id("task")
    conn.execute("""INSERT INTO task (id, project_id, title, priority, status, owner_agent_id, expected_output,
                                      acceptance_criteria, idempotency_key, step, executor, attempts, max_attempts,
                                      updated_at)
                    VALUES (?, ?, ?, 2, 'ready', ?, ?, ?, ?, ?, 'deterministic', 0, ?, ?)""",
                 (tid, project_id, f"lavora: {step.title}", agent_id,
                  f"{' + '.join('orpheus ' + ' '.join(c) for c in step.commands)} senza errori bloccanti",
                  f"dopo il passo non resta niente da fare ({step.unit}), salvo gli errori registrati dai comandi "
                  f"({RULES_VERSION})", f"lavora:{step.key}:{tid}", step.key, MAX_ATTEMPTS, now))
    return dict(conn.execute("SELECT * FROM task WHERE id = ?", (tid,)).fetchone())


def open_runs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT w.id, w.task_id, w.started_at, w.heartbeat_at, w.host, w.attempt, t.step FROM workflow_run w
           JOIN task t ON t.id = w.task_id WHERE w.status = 'running' AND t.step IS NOT NULL ORDER BY w.started_at""")]


def close_interrupted(conn, *, actor: str = "orpheus lavora") -> list[dict]:
    """Close the runs of lavora left 'running' (PC switched off, window closed): failed, interrupted = 1; their task
    is ready again. Call it only while holding the lock."""
    runs = open_runs(conn)
    if not runs:
        return []
    now = audit.utc_now()
    out = []
    with transaction(conn):
        for r in runs:
            title = BY_KEY[r["step"]].title if r["step"] in BY_KEY else r["step"]
            why = (f"interrotto: il passo non è finito (iniziato {r['started_at']}, ultimo segno di vita "
                   f"{r['heartbeat_at'] or r['started_at']}; PC spento o finestra chiusa)")
            conn.execute("""UPDATE workflow_run SET status = 'failed', interrupted = 1, error = ?, validation_status = 'fail',
                            finished_at = max(?, started_at) WHERE id = ?""", (why, now, r["id"]))
            conn.execute("UPDATE task SET status = 'ready', last_error = ?, updated_at = ? WHERE id = ?",
                         (why, now, r["task_id"]))
            out.append({"passo": r["step"], "titolo": title, "iniziato": r["started_at"], "quando": local_time(r["started_at"]),
                        "pc": r["host"], "tentativo": r["attempt"]})
        audit.record(conn, actor=actor, action="lavora_interrotto", entity_type="workflow_run",
                     payload={"regole": RULES_VERSION, "passi": out}, in_transaction=True)
    return out


def run_step(env: Env, step: Step, *, giro: str, lock: Lock | None = None, runner=run_orpheus, waits=WAITS,
             max_attempts: int = MAX_ATTEMPTS, sleep=time.sleep, actor: str = "orpheus lavora",
             retry_after_hours: float = RETRY_AFTER_HOURS) -> dict:
    """Run one step if something is missing; record task and workflow_run; return what happened."""
    conn = env.conn
    base = {"passo": step.key, "titolo": step.title, "regole": RULES_VERSION}
    try:
        todo = step.pending(env)
    except Exception as exc:                                         # noqa: BLE001 - reported, nothing written
        return {**base, "esito": "non riuscito", "errore": f"conteggio di cosa manca non riuscito: {exc}"}
    if not todo:
        return {**base, "esito": "niente da fare", "da_fare": 0}
    last = conn.execute("""SELECT w.status, w.validation_status, w.finished_at, w.parameters FROM workflow_run w
                           JOIN task t ON t.id = w.task_id WHERE t.step = ? AND w.status != 'running'
                           ORDER BY w.started_at DESC, w.rowid DESC LIMIT 1""", (step.key,)).fetchone()
    if (last is not None and last["status"] == "succeeded" and last["validation_status"] == "fail"
            and json.loads(last["parameters"]).get("da_fare") == todo
            and _age_seconds(last["finished_at"]) < retry_after_hours * 3600):
        again = datetime.strptime(last["finished_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) \
            .astimezone() + timedelta(hours=retry_after_hours)
        return {**base, "esito": "rimandato", "da_fare": todo, "unita": step.unit,
                "motivo": f"l'ultima volta ({local_time(last['finished_at'])}) sono rimasti gli stessi {todo} {step.unit} "
                          f"con errori: si riprova dopo le {again.strftime('%H:%M')} del {again.strftime('%d/%m')}"}
    now = audit.utc_now()
    seq = data_seq(conn)
    with transaction(conn):
        project = card_store._ensure_project(conn, "ORPHEUS", "lavora")
        agent = _ensure_agent(conn)
        task = _task_for(conn, step, project, agent, now)
        attempt = task["attempts"] + 1
        run_id = new_id("workflow_run")
        params = {"regole": RULES_VERSION, "giro": giro, "passo": step.key, "da_fare": todo, "dati_fino_a": seq}
        conn.execute("""INSERT INTO workflow_run (id, task_id, agent_id, executor, started_at, status, parameters,
                                                  validation_status, attempt, host, pid, heartbeat_at, commands)
                        VALUES (?, ?, ?, 'deterministic', ?, 'running', ?, 'pending', ?, ?, ?, ?, '[]')""",
                     (run_id, task["id"], agent, now, json.dumps(params, ensure_ascii=False, sort_keys=True), attempt,
                      socket.gethostname(), os.getpid(), now))
        conn.execute("UPDATE task SET status = 'running', attempts = ?, updated_at = ? WHERE id = ?",
                     (attempt, now, task["id"]))
    results: list[CommandResult] = []
    with _Heartbeat(lock, step.key):
        for argv in step.commands:
            res = run_command(list(argv), runner=runner, waits=waits, max_attempts=max_attempts, sleep=sleep)
            results.append(res)
            conn.execute("UPDATE workflow_run SET heartbeat_at = ?, commands = ? WHERE id = ?",
                         (audit.utc_now(), json.dumps([r.brief() for r in results], ensure_ascii=False), run_id))
            if res.code == 4:
                break
    result = outcome(results)
    errors = [b.get("errore") or b.get("interrotto") for b in (r.brief() for r in results)
              if b.get("errore") or b.get("interrotto")]
    end = audit.utc_now()
    ok = result in ("fatto", "fatto con errori")
    with transaction(conn):
        conn.execute("""UPDATE workflow_run SET status = ?, finished_at = max(?, started_at), error = ?,
                        validation_status = ?, heartbeat_at = ?, commands = ? WHERE id = ?""",
                     ("succeeded" if ok else "failed", end, "; ".join(errors)[:2000] or None,
                      "pass" if result == "fatto" else "fail", end,
                      json.dumps([r.brief() for r in results], ensure_ascii=False), run_id))
        conn.execute("UPDATE task SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
                     ("done" if ok else "failed", "; ".join(errors)[:2000] or None, end, task["id"]))
        audit.record(conn, actor=actor, action="lavora_passo", entity_type="task", entity_id=task["id"],
                     payload={"regole": RULES_VERSION, "giro": giro, "passo": step.key, "esito": result,
                              "tentativo": attempt, "da_fare": todo, "workflow_run": run_id,
                              "comandi": [{"comando": " ".join(r.argv), "codice": r.code, "tentativi": r.attempts}
                                          for r in results]},
                     in_transaction=True)
    try:
        after = step.pending(env)                                    # after the run is closed (the report step looks at it)
    except Exception:                                                # noqa: BLE001
        after = None
    return {**base, "esito": result, "da_fare": todo, "da_fare_dopo": after, "unita": step.unit, "tentativo": attempt,
            "task_id": task["id"], "workflow_run_id": run_id, "riassunto": summary_text(results),
            "errori": errors, "comandi": [r.brief() for r in results],
            "risultati": [{"comando": " ".join(r.argv), "codice": r.code, "risposta": trim(r.data)} for r in results]}


_LABELS = {"ingested": "letti", "duplicate": "già presenti", "completed": "completati (ricevuti da un collega)", "error": "errori"}
MAX_LIST = 50                         # long lists of a command's answer are cut in lavora's output (Windows PowerShell
                                      # 5.1 cannot read JSON answers of more than 2 MB)


def trim(obj, max_list: int = MAX_LIST):
    """A command's answer for lavora's output: lists longer than max_list keep their first items and a note."""
    if isinstance(obj, dict):
        return {k: trim(v, max_list) for k, v in obj.items()}
    if isinstance(obj, list):
        head = [trim(v, max_list) for v in obj[:max_list]]
        return head + ([f"... altri {len(obj) - max_list} (lista tagliata da lavora)"] if len(obj) > max_list else [])
    return obj


def summary_text(results: list[CommandResult]) -> str:
    parts = []
    for r in results:
        b = r.brief()
        nums = b.get("numeri") or {}
        shown = ", ".join(f"{_LABELS.get(k, k.replace('_', ' '))} {v}" for k, v in list(nums.items())[:6])
        tail = f" ({b['tentativi']} tentativi)" if b["tentativi"] > 1 else ""
        what = "non riuscito" if r.failed else (shown or "ok")
        parts.append(f"{b['comando']}: {what}{tail}")
    return "; ".join(parts)


def run_all(env: Env, *, giro: str, lock: Lock, **kw) -> dict:
    """Every step in order (used by `orpheus lavora` without options). The caller holds the lock."""
    out = []
    for step in STEPS:
        res = run_step(env, step, giro=giro, lock=lock, **kw)
        out.append(res)
        if res["esito"] == "fermo":
            break
    return {"giro": giro, "passi": out}


# ---------------------------------------------------------------- plan, work for Claude and for the user

def plan(env: Env, *, counts: bool = True, group: str = "tutto") -> dict:
    lock = Lock(env.local_root, "", 0).read()
    keys = GROUPS[group]["passi"]
    chosen = [s for s in STEPS if s.key in keys]
    out = {"regole": RULES_VERSION, "gruppo": group, "titolo_gruppo": GROUPS[group]["titolo"],
           "ordine": [s.key for s in chosen],
           "titoli": {s.key: s.title for s in STEPS},
           "in_corso": {**lock, "quando": local_time(lock.get("iniziato"))} if lock_alive(lock) else None,
           "interrotti": [] if lock_alive(lock) else [
               {**r, "titolo": BY_KEY[r["step"]].title if r["step"] in BY_KEY else r["step"],
                "quando": local_time(r["started_at"])} for r in open_runs(env.conn)],
           "servizi_a_pagamento": bool(env.project.limits.paid_services_allowed)}
    if counts:
        steps = []
        for s in chosen:
            try:
                steps.append({"passo": s.key, "titolo": s.title, "da_fare": s.pending(env), "unita": s.unit})
            except Exception as exc:                                 # noqa: BLE001
                steps.append({"passo": s.key, "titolo": s.title, "da_fare": None, "errore": str(exc)})
        out["passi"] = steps
    return out


def work_for_others(env: Env) -> dict:
    """What lavora does not do: the work of Claude (in a session) and of the user (hand checks), with the counts."""
    conn = env.conn
    complete = card_store._complete_documents(conn)
    with_card = {a for a in complete if card_store.current_card(conn, a) is not None}
    by_lang = {lang: {r[0] for r in conn.execute(
        """SELECT DISTINCT article_id FROM scientific_claim WHERE status = 'current' AND coalesce(language, 'it') = ?""",
        (lang,))} for lang in claims.LANGUAGES}
    sigla = {jid: sg.upper() for sg, jid in policy_versions.journal_by_sigla(conn, env.journal_list()).items()}
    refused = sum(waiting_files(f)[1] for f in (card_store.to_send_dir(env.home), journal_cards.to_send_dir(env.home),
                                                claims.to_send_dir(env.home)))
    claude = [
        {"lavoro": "schede degli articoli completi senza scheda", "quanti": len(complete) - len(with_card),
         "come": "pacchetti.cmd, poi Claude in sessione; le schede si inviano con lavora.cmd"},
        {"lavoro": "affermazioni in inglese (articoli con scheda senza affermazioni)",
         "quanti": len(with_card - by_lang["en"]), "come": "Claude in sessione; si inviano con lavora.cmd"},
        {"lavoro": "schede delle riviste da rifare (linee guida cambiate)",
         "quanti": len(policy_changes.alerts(conn, sigla)), "come": "Claude in sessione"},
        {"lavoro": "file rifiutati da correggere (vedi i .motivi.txt)", "quanti": refused, "come": "Claude in sessione"},
    ]
    cp = claims.control_pending(conn, env.home)
    user = [{"lavoro": "affermazioni da controllare a mano (giusto S/N)", "quanti": cp["da_controllare"],
             "file": cp["file_da_compilare"],
             "come": "compila il file, salvalo con _compilato alla fine del nome, poi lavora.cmd"}]
    return {"per_claude": [x for x in claude if x["quanti"]], "per_te": [x for x in user if x["quanti"]]}


def giro_summary(conn, giro: str) -> list[dict]:
    rows = conn.execute("""SELECT t.step, w.status, w.attempt, w.started_at, w.finished_at, w.error, w.parameters
                           FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.step IS NOT NULL AND json_extract(w.parameters, '$.giro') = ?
                           ORDER BY w.started_at, w.rowid""", (giro,)).fetchall()
    return [{"passo": r["step"], "titolo": BY_KEY[r["step"]].title if r["step"] in BY_KEY else r["step"],
             "stato": r["status"], "tentativo": r["attempt"], "errore": r["error"]} for r in rows]
