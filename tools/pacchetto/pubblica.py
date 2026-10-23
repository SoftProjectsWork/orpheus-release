"""Copies the public code of ORPHEUS into the clone of the public repository (T-053).

    python tools/pacchetto/pubblica.py /percorso/orpheus-release

Only the files of costruisci.PUBLIC_FILES (code, migrations, configuration, double-click commands, package tools);
never data, documents of the project, tests, the owner's installer or the log upload. Files that are no longer public
are removed from the clone. Then, in the clone: git add -A, commit, push to main: the workflow builds the Windows zip
with Python, tests it and, when the version of pyproject.toml is new, publishes it in Releases with the tag vX.Y.Z
(aggiorna.cmd of the colleagues downloads it from there).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import costruisci  # noqa: E402

HERE = Path(__file__).resolve().parent
README = """# ORPHEUS

ORPHEUS — Open Research, Publication & High-Quality Editorial Understanding System: raccolta e verifica di articoli
scientifici, schede delle riviste e degli articoli, controllo dei manoscritti (scienze motorie e biomeccanica).

Questo repository contiene **solo il codice** distribuito ai colleghi: nessun dato, nessuna scheda, nessun articolo.

## Installare (Windows)

1. Apri **[Releases](../../releases/latest)** e scarica `ORPHEUS_<versione>_windows.zip` (Python incluso).
2. Estrai tutto, poi doppio clic su `installa.cmd` e segui le domande.
3. Per gli aggiornamenti: doppio clic su `C:\\ORPHEUS\\aggiorna.cmd`.

Guida illustrata: `GUIDA_ORPHEUS.html` nello zip (anche in `tools/pacchetto/`). Istruzioni in breve:
`tools/pacchetto/LEGGIMI.txt`.

Versione: {version}
"""
GITIGNORE = "__pycache__/\n*.pyc\ndist/\nprova/\nprova_pc/\npython-embed.zip\n.env\n"


def publish(repo: Path, dest: Path) -> dict:
    dest = Path(dest)
    if not (dest / ".git").exists():
        raise SystemExit(f"{dest} non è un clone git del repository pubblico")
    files = costruisci.public_files(repo)
    problems = costruisci.check_public(repo, files)
    if problems:
        raise SystemExit("Codice NON pubblicabile:\n  " + "\n  ".join(problems))
    wanted = {f.as_posix() for f in files} | {"README.md", ".gitignore", ".github/workflows/release.yml"}
    removed = []
    for p in sorted(dest.rglob("*"), reverse=True):
        rel = p.relative_to(dest).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        if p.is_file() and rel not in wanted:
            p.unlink()
            removed.append(rel)
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    for rel in files:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, dest / rel)
    v = costruisci.version(repo)
    (dest / "README.md").write_text(README.format(version=v), encoding="utf-8")
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    wf = dest / ".github/workflows/release.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "release.yml", wf)
    return {"versione": v, "file": len(files) + 3, "tolti": removed}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(publish(costruisci.REPO, Path(sys.argv[1])))
