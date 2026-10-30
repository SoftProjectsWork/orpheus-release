"""Builds the public Windows package of ORPHEUS with Python included (T-053, decisions of 2026-10-23).

    python tools/pacchetto/costruisci.py --python-zip python-3.12.10-embed-amd64.zip --out dist

Result: dist/ORPHEUS_<version>_windows.zip with
    installa.cmd, installa.ps1, LEGGIMI.txt          what the colleague double-clicks
    app/python/                                      Python "embeddable" for Windows + the libraries ORPHEUS needs
    app/orpheus/                                     the code (only the files of PUBLIC_FILES: no data, no documents)
No git, no account, no internet needed to install. The same file is used for updates (aggiorna.cmd downloads it).

Run by the workflow of the public repository (SoftProjectsWork/orpheus-release, .github/workflows/release.yml) on a
Windows runner; also works on Linux for the tests (pip downloads the Windows wheels with --platform).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PY_TAG = "312"                                  # Python 3.12 (python312._pth); same minor as the embeddable zip
EXTRAS = ("semantica", "cruscotto")             # local search by meaning, dashboard in the browser
ALSO = ("tzdata",)                              # time zones for zoneinfo on Windows

# What goes into the public code (also used by pubblica.py). Patterns relative to the repository root.
PUBLIC_FILES = [
    "pyproject.toml",
    "src/orpheus/**/*",              # "**" alone matches only folders before Python 3.13
    "migrations/*.sql",
    "config/*.yaml",
    "templates/riviste_prioritarie.csv",
    "tools/windows/*.cmd",
    "tools/windows/orpheus_comandi.ps1",
    "tools/windows/ORPHEUS.ico",
    "tools/windows/ORPHEUS.svg",
    "tools/pacchetto/*",
]
# Never public, even if a pattern above matched: the owner's installer (git) and the log upload to GitHub.
PRIVATE = ["tools/windows/installa_orpheus.*", "tools/windows/pubblica_registro.ps1", "*/__pycache__/*", "*.pyc"]
# Text that must never appear in a public file.
FORBIDDEN = [r"[A-Za-z0-9._%+-]+@gmail\.com", r"ghp_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}"]
# Personal words, kept only as SHA-256 of the lower-case word (so the public code does not contain them).
FORBIDDEN_WORDS = {"a2934ae84c46f3abee622d7ce00ea2d55854c69fbd296d133d3277b59ff69506", "b49e768e5556bfb154327b123947b274e9a814acd13d127b321fa4f711a79acb"}


def version(repo: Path = REPO) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def requirements(repo: Path = REPO) -> list[str]:
    p = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = [r.split(";")[0].split("#")[0].strip() for r in p["dependencies"]]
    for extra in EXTRAS:
        reqs += [r.split(";")[0].strip() for r in p["optional-dependencies"][extra]]
    return [r for r in reqs if r] + list(ALSO)


def public_files(repo: Path = REPO) -> list[Path]:
    """Relative paths of the files of the public code, sorted."""
    out = set()
    for pattern in PUBLIC_FILES:
        for p in repo.glob(pattern):
            if p.is_file():
                rel = p.relative_to(repo).as_posix()
                if not any(fnmatch.fnmatch(rel, x) for x in PRIVATE):
                    out.add(Path(rel))
    return sorted(out)


def check_public(root: Path, files: list[Path]) -> list[str]:
    """Problems: forbidden text in a public file."""
    problems = []
    for rel in files:
        data = (root / rel).read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in FORBIDDEN:
            if re.search(pattern, text):
                problems.append(f"{rel.as_posix()}: testo vietato ({pattern})")
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}
        if any(hashlib.sha256(w.encode()).hexdigest() in FORBIDDEN_WORDS for w in words):
            problems.append(f"{rel.as_posix()}: contiene un nome personale")
    return problems


def copy_code(repo: Path, dest: Path) -> list[Path]:
    files = public_files(repo)
    problems = check_public(repo, files)
    if problems:
        raise SystemExit("Codice NON pubblicabile:\n  " + "\n  ".join(problems))
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target)
    (dest / "VERSIONE.txt").write_text(version(repo) + "\n", encoding="utf-8")
    return files


def setup_python(python_zip: Path, dest: Path) -> None:
    """Embeddable Python: unzip, then let it find the libraries (Lib/site-packages) and the code (../orpheus/src)."""
    with zipfile.ZipFile(python_zip) as z:
        z.extractall(dest)
    pth = dest / f"python{PY_TAG}._pth"
    if not pth.exists():
        raise SystemExit(f"{python_zip.name} non è il Python embeddable {PY_TAG[0]}.{PY_TAG[1:]} (manca {pth.name})")
    pth.write_text(f"python{PY_TAG}.zip\n.\nLib\\site-packages\n..\\orpheus\\src\nimport site\n", encoding="utf-8")
    (dest / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":                 # C++ runtime that onnxruntime needs, not in the embeddable zip
        system32 = Path(r"C:\Windows\System32")
        for dll in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcomp140.dll"):
            if (system32 / dll).exists() and not (dest / dll).exists():
                shutil.copy2(system32 / dll, dest / dll)


def install_libraries(reqs: list[str], site: Path, *, pip_extra: list[str] | None = None) -> None:
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
           "--target", str(site), "--only-binary=:all:", "--platform", "win_amd64",
           "--python-version", f"{PY_TAG[0]}.{PY_TAG[1:]}", "--implementation", "cp", *(pip_extra or []), *reqs]
    subprocess.run(cmd, check=True)


def build(python_zip: Path, out: Path, *, repo: Path = REPO, pip_extra: list[str] | None = None,
          libraries: bool = True) -> Path:
    v = version(repo)
    name = f"ORPHEUS_{v}_windows"
    stage = out / name
    if stage.exists():
        shutil.rmtree(stage)
    app = stage / "app"
    copy_code(repo, app / "orpheus")
    setup_python(python_zip, app / "python")
    if libraries:
        install_libraries(requirements(repo), app / "python" / "Lib" / "site-packages", pip_extra=pip_extra)
    for f in ("installa.cmd", "installa.ps1", "LEGGIMI.txt", "GUIDA_ORPHEUS.html"):
        shutil.copy2(HERE / f, stage / f)
    zpath = out / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, Path(name) / p.relative_to(stage))
    return zpath


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--python-zip", required=True, type=Path, help="python-3.12.x-embed-amd64.zip da python.org")
    ap.add_argument("--out", default=Path("dist"), type=Path)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    z = build(a.python_zip, a.out)
    print(f"{z} ({z.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
