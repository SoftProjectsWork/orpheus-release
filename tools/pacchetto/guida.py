"""Builds GUIDA_ORPHEUS.html (one file, fonts embedded, works offline) from guida_modello.html (T-053).

    python tools/pacchetto/guida.py          -> tools/pacchetto/GUIDA_ORPHEUS.html
The guide goes into the Windows package (next to installa.cmd and in C:\\ORPHEUS) and is published as an artifact."""

from __future__ import annotations

import base64
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FONTS = REPO / "src" / "orpheus" / "dashboard_static" / "fonts"
OUT = HERE / "GUIDA_ORPHEUS.html"


def build(version: str, when: date | None = None, *, standalone: bool = True) -> str:
    """standalone: with <!doctype html> and <html lang>, for the file opened from the disk; without, for the artifact
    (its viewer adds the skeleton)."""
    html = (HERE / "guida_modello.html").read_text(encoding="utf-8")
    for key, name in (("__SANS400__", "atkinson-hyperlegible-latin-400-normal.woff2"),
                      ("__SANS700__", "atkinson-hyperlegible-latin-700-normal.woff2"),
                      ("__SERIF600__", "source-serif-4-latin-600-normal.woff2")):
        html = html.replace(key, base64.b64encode((FONTS / name).read_bytes()).decode("ascii"))
    when = when or date.today()
    html = html.replace("__VERSION__", version).replace("__DATE__", when.strftime("%d/%m/%Y"))
    return f'<!doctype html>\n<html lang="it">\n{html}</html>\n' if standalone else html


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    import costruisci
    OUT.write_text(build(costruisci.version()), encoding="utf-8")
    print(OUT, OUT.stat().st_size)
    if len(sys.argv) > 1:                      # artifact version
        Path(sys.argv[1]).write_text(build(costruisci.version(), standalone=False), encoding="utf-8")
