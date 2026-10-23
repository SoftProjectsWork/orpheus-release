"""Optional OCR for scanned PDFs (T-020) — free tools only, read-only, no network.

A scanned PDF has no text layer (`ingest-pdf` marks it `no_text`): its pages are pictures. If the free program
Tesseract is installed on the PC, this module reads those pictures and returns the text page by page; if it is
not installed, nothing breaks: the result says "saltato" with the reason.

How the pictures are obtained (first available):
  1. `pdftoppm` (Poppler, free), which draws every page at 300 dpi;
  2. otherwise the biggest picture embedded in each page, taken with pypdf (a scan is one picture per page).

Nothing here touches the database: the caller decides what to do with the text (the CLI writes a .txt in
rapporti/ocr/). OCR text can contain reading errors: it is always labelled as OCR.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdf

OCR_VERSION = "ocr-v1"
DPI = 300
PAGE_TIMEOUT_S = 180
# Usual install folders on Windows (the installer of UB Mannheim does not always add Tesseract to PATH).
_WINDOWS_TESSERACT = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
_IMAGE_EXT_OK = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pnm", ".jp2"}


def find_tesseract() -> str | None:
    """Path of the tesseract program, or None. ORPHEUS_TESSERACT overrides the search."""
    env = os.environ.get("ORPHEUS_TESSERACT")
    if env:
        return env if Path(env).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        for p in _WINDOWS_TESSERACT + [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]:
            if Path(p).is_file():
                return p
    return None


def find_pdftoppm() -> str | None:
    return shutil.which("pdftoppm")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=PAGE_TIMEOUT_S, check=False)


def _render_pages(pdf: Path, pdftoppm: str, work: Path, n_pages: int) -> dict[int, Path]:
    """Page number -> PNG drawn by pdftoppm (missing pages are simply absent)."""
    out: dict[int, Path] = {}
    for n in range(1, n_pages + 1):
        prefix = work / f"p{n:04d}"
        r = _run([pdftoppm, "-r", str(DPI), "-gray", "-png", "-f", str(n), "-l", str(n), "-singlefile",
                  str(pdf), str(prefix)])
        png = prefix.with_suffix(".png")
        if r.returncode == 0 and png.is_file():
            out[n] = png
    return out


def _embedded_images(reader: pypdf.PdfReader, work: Path) -> dict[int, Path]:
    """Page number -> the biggest picture embedded in that page, saved as is (needs no extra library for JPEG)."""
    out: dict[int, Path] = {}
    for n, page in enumerate(reader.pages, start=1):
        best = None
        try:
            for img in page.images:
                ext = Path(img.name).suffix.lower()
                if ext not in _IMAGE_EXT_OK:
                    continue
                data = img.data
                if best is None or len(data) > len(best[1]):
                    best = (ext, data)
        except Exception:   # undecodable image (e.g. a filter that needs Pillow): this page is skipped
            best = None
        if best:
            path = work / f"p{n:04d}{best[0]}"
            path.write_bytes(best[1])
            out[n] = path
    return out


def ocr_pdf(pdf_path: str | Path, lang: str = "eng") -> dict:
    """OCR of every page. Returns {"stato": "fatto"|"saltato", "motivo", "metodo", "pagine": [{pagina, testo}]}."""
    pdf = Path(pdf_path)
    base = {"file": str(pdf), "versione": OCR_VERSION, "lingua": lang, "pagine": []}
    tesseract = find_tesseract()
    if not tesseract:
        return {**base, "stato": "saltato", "metodo": None,
                "motivo": "Tesseract non è installato su questo PC (programma gratuito, facoltativo)"}
    try:
        reader = pypdf.PdfReader(str(pdf))
        n_pages = len(reader.pages)
    except Exception as exc:
        return {**base, "stato": "saltato", "metodo": None, "motivo": f"PDF non leggibile: {type(exc).__name__}"}
    with tempfile.TemporaryDirectory(prefix="orpheus_ocr_") as tmp:
        work = Path(tmp)
        pdftoppm = find_pdftoppm()
        if pdftoppm:
            method, images = "pdftoppm", _render_pages(pdf, pdftoppm, work, n_pages)
        else:
            method, images = "immagini_incorporate", _embedded_images(reader, work)
        if not images:
            return {**base, "stato": "saltato", "metodo": method,
                    "motivo": "nessuna immagine di pagina leggibile (installare Poppler pdftoppm aiuta)"}
        pages = []
        for n in range(1, n_pages + 1):
            if n not in images:
                pages.append({"pagina": n, "testo": "", "nota": "pagina senza immagine leggibile"})
                continue
            r = _run([tesseract, str(images[n]), "stdout", "-l", lang])
            if r.returncode != 0:
                err = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
                pages.append({"pagina": n, "testo": "", "nota": f"errore di Tesseract: {err[0][:200]}"})
                continue
            pages.append({"pagina": n, "testo": r.stdout.decode("utf-8", "replace").strip()})
    letti = sum(1 for p in pages if p["testo"])
    return {**base, "stato": "fatto" if letti else "saltato", "metodo": method, "pagine": pages,
            "pagine_lette": letti, "motivo": None if letti else "Tesseract non ha letto testo in nessuna pagina"}


def as_text(result: dict) -> str:
    """Plain text with a header that says it comes from OCR (possible reading errors)."""
    head = (f"# Testo da OCR ({result['versione']}, {result.get('metodo')}, lingua {result['lingua']}) — "
            f"possibili errori di lettura\n# File: {result['file']}\n")
    body = "\n".join(f"\n=== pagina {p['pagina']} ===\n{p['testo'] or '[' + p.get('nota', 'vuota') + ']'}"
                     for p in result["pagine"])
    return head + body + "\n"
