"""`orpheus salute` (T-045, Fase 8 parte 1): the health of ORPHEUS on this PC, contract health-v1.

READ-ONLY on the database. Each check says what it looked at (fonte), when (data) and an esito:
  ok        nothing to do;
  attenzione something to look at soon (old backup, restore test never recorded, little disk space, failed steps);
  errore    something wrong now (paid service enabled, migration modified or unknown, almost no disk space).
The overall esito is the worst of the checks. Nothing here is demo data: a check without data says so.

The restore test (`orpheus prova-ripristino`) writes its last result in the local folder of the PC
(prove_ripristino.json, next to state.json), never in the database: that file is what the check reads.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, db, lineage
from .localstate import LocalState, _atomic_write_json, _read_json, parse_utc, utc_now

CONTRACT = "health-v1"
RESTORE_FILE = "prove_ripristino.json"
KEEP_RESTORES = 20

BACKUP_WARN_DAYS = 7          # newest local backup older than this → attenzione
RESTORE_WARN_DAYS = 30        # last successful restore test older than this → attenzione
RUNS_DAYS = 7                 # window of the runs of `orpheus lavora` looked at
DISK_WARN_GB = 5.0
DISK_ERROR_GB = 1.0

ORDER = {"ok": 0, "attenzione": 1, "errore": 2}


# ---------------------------------------------------------------- restore tests (local file)

def record_restore_test(state: LocalState, result: dict) -> None:
    """Keep the outcome of a restore test in the PC's local folder (not in the database)."""
    path = state.root / RESTORE_FILE
    entries = _read_json(path, [])
    failed = [k for k, c in (result.get("controlli") or {}).items() if not c.get("ok")]
    entries.append({"data": utc_now(), "esito": result.get("esito"), "backup": result.get("backup"),
                    "backup_creato_il": result.get("creato_il"), "controlli_falliti": failed})
    _atomic_write_json(path, entries[-KEEP_RESTORES:])


def restore_tests(state: LocalState) -> list[dict]:
    data = _read_json(state.root / RESTORE_FILE, [])
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------- checks

def _check(nome: str, esito: str, messaggio: str, fonte: str, data=None, **detail) -> dict:
    return {"nome": nome, "esito": esito, "messaggio": messaggio, "fonte": fonte, "data": data, **detail}


def _age_days(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        return (now - parse_utc(stamp)).total_seconds() / 86400
    except ValueError:
        return None


def _days(value: float) -> str:
    return "meno di un giorno" if value < 1 else ("1 giorno" if value < 2 else f"{int(value)} giorni")


def check_runs(conn, now: datetime) -> dict:
    fonte = "workflow_run e task (passi di orpheus lavora)"
    names = {r[0] for r in conn.execute("SELECT name FROM pragma_table_info('task')")}
    if "step" not in names:
        return _check("lavora", "attenzione", "Il database non ha ancora la coda del lavoro (migrazione 0011).", fonte)
    last = conn.execute("""SELECT w.started_at, w.finished_at, w.status, w.interrupted, w.error, t.step,
                                  json_extract(w.parameters, '$.giro') AS giro
                           FROM workflow_run w JOIN task t ON t.id = w.task_id WHERE t.step IS NOT NULL
                           ORDER BY w.started_at DESC, w.rowid DESC LIMIT 1""").fetchone()
    if last is None:
        return _check("lavora", "attenzione", "lavora.cmd non è mai stato eseguito su questo database.", fonte)
    since = (now - timedelta(days=RUNS_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = conn.execute("""SELECT t.step, w.status, w.interrupted, w.error, w.started_at
                           FROM workflow_run w JOIN task t ON t.id = w.task_id
                           WHERE t.step IS NOT NULL AND w.started_at >= ? ORDER BY w.started_at DESC""",
                        (since,)).fetchall()
    bad = [r for r in rows if r["status"] in ("failed", "timeout") or r["interrupted"]]
    running = [r for r in rows if r["status"] == "running"]
    errori = [{"passo": r["step"], "quando": r["started_at"], "errore": (r["error"] or "")[:200],
               "interrotto": bool(r["interrupted"])} for r in bad[:5]]
    age = _age_days(last["started_at"], now)
    detail = {"ultimo_giro": last["giro"], "ultimo_passo": last["step"], "ultimo_avvio": last["started_at"],
              f"passi_ultimi_{RUNS_DAYS}_giorni": len(rows), "passi_non_riusciti": len(bad),
              "in_corso": len(running), "errori": errori}
    if bad:
        return _check("lavora", "attenzione",
                      f"{len(bad)} passi non riusciti o interrotti negli ultimi {RUNS_DAYS} giorni "
                      f"(l'ultimo: {bad[0]['step']}).", fonte, last["started_at"], **detail)
    when = _days(age) if age is not None else "?"
    return _check("lavora", "ok", f"Ultimo giro {when} fa, nessun errore negli ultimi {RUNS_DAYS} giorni.",
                  fonte, last["started_at"], **detail)


def check_backups(conn, state: LocalState, now: datetime) -> dict:
    fonte = "cartella locale del PC (backups/index.json)"
    head = lineage.head(conn)
    if head.database_id is None:
        return _check("copie_di_sicurezza", "attenzione", "Database senza identità: esegui orpheus migrate.", fonte)
    newest = state.newest_backup(head.database_id)
    if newest is None:
        return _check("copie_di_sicurezza", "attenzione",
                      "Nessuna copia di sicurezza di questo database su questo PC.", fonte, numero=0)
    age = _age_days(newest.get("created_at"), now)
    n = len(state.backups(head.database_id, include_superseded=True))
    detail = {"ultima": newest.get("file"), "numero": n, "posizione_ultima": newest.get("head_seq"),
              "posizione_attuale": head.seq}
    if age is None or age > BACKUP_WARN_DAYS:
        return _check("copie_di_sicurezza", "attenzione",
                      f"L'ultima copia ha {_days(age) if age is not None else '?'}: fanne una (backup).", fonte,
                      newest.get("created_at"), **detail)
    return _check("copie_di_sicurezza", "ok", f"Ultima copia {_days(age)} fa ({n} su questo PC).", fonte,
                  newest.get("created_at"), **detail)


def check_restore(state: LocalState, now: datetime) -> dict:
    fonte = f"cartella locale del PC ({RESTORE_FILE}, scritto da orpheus prova-ripristino)"
    tests = restore_tests(state)
    if not tests:
        return _check("prova_ripristino", "attenzione",
                      "Nessuna prova di ripristino registrata su questo PC: esegui orpheus prova-ripristino.", fonte)
    last = tests[-1]
    ok = [t for t in tests if t.get("esito") == "riuscita"]
    if last.get("esito") != "riuscita":
        return _check("prova_ripristino", "errore",
                      "L'ultima prova di ripristino è fallita: " + ", ".join(last.get("controlli_falliti") or ["?"]),
                      fonte, last.get("data"), ultima=last)
    age = _age_days(last.get("data"), now)
    if age is None or age > RESTORE_WARN_DAYS:
        return _check("prova_ripristino", "attenzione",
                      f"L'ultima prova di ripristino ha {_days(age) if age is not None else '?'}: ripetila.",
                      fonte, last.get("data"), ultima=last, riuscite=len(ok))
    return _check("prova_ripristino", "ok", f"Ultima prova riuscita {_days(age)} fa.", fonte, last.get("data"),
                  ultima=last, riuscite=len(ok))


def check_disk(paths: dict[str, Path], now: datetime, usage=shutil.disk_usage) -> dict:
    fonte = "spazio libero dei dischi (sistema operativo)"
    dischi, esito = [], "ok"
    for name, path in paths.items():
        target = path if path.exists() else path.parent
        try:
            u = usage(target)
        except OSError as exc:
            dischi.append({"cartella": name, "percorso": str(path), "errore": str(exc)})
            esito = max(esito, "attenzione", key=ORDER.get)
            continue
        free_gb = round(u.free / 1e9, 1)
        dischi.append({"cartella": name, "percorso": str(path), "liberi_gb": free_gb,
                       "totale_gb": round(u.total / 1e9, 1)})
        level = "errore" if free_gb < DISK_ERROR_GB else "attenzione" if free_gb < DISK_WARN_GB else "ok"
        esito = max(esito, level, key=ORDER.get)
    low = [d for d in dischi if d.get("liberi_gb") is None or d["liberi_gb"] < DISK_WARN_GB]
    msg = ("Spazio sufficiente: " + ", ".join(f"{d['cartella']} {d['liberi_gb']} GB liberi" for d in dischi)
           if not low else "Poco spazio: " + ", ".join(f"{d['cartella']} {d.get('liberi_gb', '?')} GB" for d in low))
    return _check("disco", esito, msg, fonte, now.strftime("%Y-%m-%dT%H:%M:%SZ"), dischi=dischi)


def check_migrations(conn) -> dict:
    fonte = "schema_migrations e cartella migrations del codice"
    states = db.status(conn)
    pending = [m["version"] for m in states if m["state"] == "pending"]
    modified = [m["version"] for m in states if m["state"] == "MODIFIED"]
    unknown = db.unknown_applied(conn)
    applied = len(states) - len(pending)
    detail = {"applicate": applied, "da_applicare": pending, "modificate": modified, "sconosciute": unknown}
    if modified or unknown:
        return _check("migrazioni", "errore", "Migrazioni modificate o sconosciute: non usare questo codice "
                      "(vedi orpheus status).", fonte, **detail)
    if pending:
        return _check("migrazioni", "attenzione", f"Migrazioni da applicare: {', '.join(pending)} "
                      "(installa_orpheus.cmd le applica, con una copia di sicurezza prima).", fonte, **detail)
    return _check("migrazioni", "ok", f"Tutte le {applied} migrazioni applicate.", fonte, **detail)


def check_paid(project, sources=None, models=None) -> dict:
    fonte = "config/orpheus.yaml, config/sources.yaml, config/models.yaml"
    sources = config.load_sources() if sources is None else sources
    if models is None:
        try:
            models = config.load_models()
        except (OSError, KeyError, ValueError):
            models = None
    limits = project.limits
    allowed = bool(getattr(limits, "paid_services_allowed", False))
    budget = getattr(limits, "monthly_budget_usd", 0) or 0
    paid = [s.id for s in sources if s.enabled and s.cost == "paid"]
    paid += [e.id for e in (models.executors if models else []) if e.enabled and e.cost == "paid"]
    detail = {"servizi_a_pagamento_permessi": allowed, "budget_mensile_usd": budget, "a_pagamento_abilitati": paid}
    if paid or allowed or budget:
        return _check("servizi_a_pagamento", "errore",
                      "Servizi a pagamento abilitati o budget diverso da zero: la regola è budget zero.", fonte, **detail)
    return _check("servizi_a_pagamento", "ok", "Nessun servizio a pagamento (budget zero).", fonte, **detail)


def check(conn, *, state: LocalState, project, db_path: Path, home: Path, now: datetime | None = None,
          sources=None, models=None, usage=shutil.disk_usage) -> dict:
    """All the checks, read-only. `now` (UTC) is for the tests."""
    now = now or datetime.now(timezone.utc)
    disks = {"dati (ORPHEUS_HOME)": home, "cartella locale del PC": state.root}
    checks = [check_runs(conn, now), check_backups(conn, state, now), check_restore(state, now),
              check_disk(disks, now, usage), check_migrations(conn), check_paid(project, sources, models)]
    worst = max((c["esito"] for c in checks), key=ORDER.get)
    return {"contratto": CONTRACT, "esito": worst, "controllato_il": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "database": str(db_path), "controlli": checks,
            "da_guardare": [c["messaggio"] for c in checks if c["esito"] != "ok"]}

