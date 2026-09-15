"""Text out of attachments, so what is inside them can be quoted.

An attachment is usually the most load-bearing document an event has: the
floor plan, the technical rider, the caterer's menu, the signed quote. If the
agent can see only the filename, every fact in them has to be retyped by hand
or invented -- and inventing is the failure this system exists to prevent.

Extracted text is stored as evidence in its own right, under the parent's ref
plus the attachment index, so a row sourced from page 2 of a rider verifies
exactly like one sourced from an email body.
"""

from __future__ import annotations

import csv
import io
import subprocess
import tempfile
from pathlib import Path

from .sources.base import AttachmentFile, html_to_text

# Anything larger is almost certainly a photo or a video: downloading it costs
# time and it has no text to give.
MAX_BYTES = 25 * 1024 * 1024

TEXTUTIL_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
}

PLAIN_PREFIXES = ("text/plain", "text/markdown", "text/csv", "application/json")


class ExtractError(RuntimeError):
    pass


def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # noqa: F841
        raise ExtractError(
            "PDF support is not installed. Run: pip install pypdf"
        ) from None
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"Could not open the PDF: {e}") from e
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise ExtractError("The PDF is password protected.") from None
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            pages.append(f"--- pagina {i} ---\n{text}")
    if not pages:
        raise ExtractError(
            "This PDF holds no selectable text -- it is most likely a scan or a "
            "drawing. Someone has to read it and type in what matters; do not "
            "guess at its contents."
        )
    return "\n\n".join(pages)


def _textutil(data: bytes, suffix: str) -> str:
    """macOS converts Word and RTF without any Python dependency."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"file.{suffix}"
        src.write_bytes(data)
        try:
            out = subprocess.run(
                ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(src)],
                capture_output=True, timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ExtractError(f"textutil failed: {e}") from e
        if out.returncode != 0:
            raise ExtractError(
                f"Could not convert this {suffix} file: "
                f"{out.stderr.decode('utf-8', 'replace')[:160]}"
            )
        return out.stdout.decode("utf-8", "replace").strip()


def _csv(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000])
    except csv.Error:
        return text
    rows = list(csv.reader(io.StringIO(text), dialect))
    return "\n".join(" | ".join(c.strip() for c in row) for row in rows if any(row))


def extract(f: AttachmentFile) -> str:
    """Plain text from one attachment. Raises ExtractError with a readable
    reason -- never a silent empty string, which reads as 'nothing in it'."""
    mime = (f.mime or "").split(";")[0].strip().lower()
    name = (f.filename or "").lower()

    if len(f.data) > MAX_BYTES:
        raise ExtractError(
            f"{f.filename} is {len(f.data) // 1024 // 1024} MB -- too large to read here."
        )

    if mime == "application/pdf" or name.endswith(".pdf"):
        return _pdf(f.data)

    suffix = TEXTUTIL_TYPES.get(mime)
    if suffix is None:
        for ext in ("docx", "doc", "rtf"):
            if name.endswith("." + ext):
                suffix = ext
                break
    if suffix:
        return _textutil(f.data, suffix)

    if mime in ("text/csv", "application/csv") or name.endswith((".csv", ".tsv")):
        return _csv(f.data)

    if mime.startswith("text/html") or name.endswith((".html", ".htm")):
        return html_to_text(f.data.decode("utf-8", "replace"))

    if mime.startswith(PLAIN_PREFIXES) or name.endswith((".txt", ".md", ".json")):
        return f.data.decode("utf-8", "replace").strip()

    if mime.startswith("image/"):
        raise ExtractError(
            f"{f.filename} is an image. Open it and read it yourself -- there is no "
            f"text to pull out, and its contents must not be guessed."
        )

    raise ExtractError(f"No text reader for {mime or 'unknown type'} ({f.filename}).")


def readable(mime: str, filename: str = "") -> bool:
    """Whether extract() has any chance, used to label the UI honestly."""
    mime = (mime or "").split(";")[0].strip().lower()
    name = (filename or "").lower()
    return (
        mime == "application/pdf"
        or mime in TEXTUTIL_TYPES
        or mime.startswith(PLAIN_PREFIXES)
        or mime in ("text/csv", "application/csv")
        or mime.startswith("text/")
        or name.endswith((".pdf", ".docx", ".doc", ".rtf", ".txt", ".md",
                          ".csv", ".tsv", ".json", ".html", ".htm"))
    )
