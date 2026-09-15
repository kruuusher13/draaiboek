"""Render a DocView to HTML, so a document can be looked at without Google."""

from __future__ import annotations

import base64
import html
from functools import lru_cache
from pathlib import Path

# The wordmark from Larissa's own documents (word/media/image1.png), embedded so
# the page is a single self-contained file.
LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "leeuwenbergh-logo.png"


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    try:
        return ("data:image/png;base64,"
                + base64.b64encode(LOGO_PATH.read_bytes()).decode())
    except OSError:
        return ""

from .model import DocView

BAND = "#2d3c4f"      # taken from her document
HEADER_BG = "#eff0f1"
CSS = """
:root { color-scheme: light; }
body { margin:0; padding:32px; background:#f3f4f6;
       font-family:Calibri,'Segoe UI',system-ui,sans-serif; color:#111; }
/* Landscape A4: she asked for horizontal, never vertical (13 Sep 2026). */
.sheet { width:100%; max-width:1400px; margin:0 auto; background:#fff;
         padding:40px 44px 52px; box-shadow:0 1px 3px rgba(0,0,0,.14); }
@media (max-width:760px){ .sheet{ padding:22px 16px 30px; } }
.brand { margin-bottom:14px; }
.brand img { height:26px; width:auto; display:block; }
.brand .fallback { font-size:11px; letter-spacing:.2em; text-transform:uppercase;
                   color:#6b7280; }
h1 { font-size:26px; margin:0 0 4px; font-weight:600; }
.sub { color:#6b7280; font-size:13px; margin-bottom:26px; }
/* Every chapter spans the full width of the page. */
table { width:100%; border-collapse:collapse; margin-bottom:18px; font-size:13px;
        table-layout:fixed; }
td { border:1px solid #d7dae0; padding:5px 8px; vertical-align:top;
     word-wrap:break-word; }
tr.band td { background:__BAND__; color:#fff; font-weight:600; letter-spacing:.06em;
             text-transform:uppercase; font-size:12px; padding:7px 8px; }
tr.header td { background:__HEADER__; font-weight:600; font-size:11px; color:#374151;
               letter-spacing:.05em; text-transform:uppercase; }
.legend { display:flex; gap:18px; flex-wrap:wrap; font-size:12px; color:#4b5563;
          border-top:1px solid #e5e7eb; padding-top:14px; }
.legend span { display:flex; align-items:center; gap:7px; }
.chip { width:13px; height:13px; border:1px solid #c9ccd2; display:inline-block; }
.empty { color:#9ca3af; font-style:italic; }
""".replace("__BAND__", BAND).replace("__HEADER__", HEADER_BG)

LEGEND = [("Techniek & Media", "#f4cccc"), ("Hospitality & Catering", "#d9ead3"),
          ("Inrichting & Logistiek", "#cfe2f3"), ("Algemeen", "#ffffff")]


def _bg(row) -> str:
    colours = {c.background for c in row.cells if c.background}
    for c in colours:
        if c and c.lower() not in ("#ffffff", "#000000"):
            return c
    return ""


def render_html(view: DocView, subtitle: str = "") -> str:
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{html.escape(view.title or 'Draaiboek')}</title>",
        f"<style>{CSS}</style><div class='sheet'>",
        ("<div class='brand'><img alt='Leeuwenbergh' src='%s'></div>" % logo_data_uri())
        if logo_data_uri() else
        "<div class='brand'><span class='fallback'>Leeuwenbergh Utrecht</span></div>",
        f"<h1>{html.escape(view.title or 'Draaiboek')}</h1>",
        f"<div class='sub'>{html.escape(subtitle or view.revision_id)}</div>",
    ]
    for t in view.tables:
        parts.append("<table>")
        for r in t.rows:
            cls = r.kind if r.kind in ("band", "header") else ""
            style = ""
            if r.kind == "data":
                bg = _bg(r)
                if bg:
                    style = f" style='background:{html.escape(bg)}'"
            parts.append(f"<tr class='{cls}'>")
            span = len(r.cells) if r.kind == "band" else 1
            if r.kind == "band":
                parts.append(f"<td colspan='{span}'>{html.escape(r.cells[0].text)}</td>")
            else:
                for c in r.cells:
                    txt = html.escape(c.text).replace("\n", "<br>")
                    parts.append(f"<td{style}>{txt}</td>")
            parts.append("</tr>")
        parts.append("</table>")

    parts.append("<div class='legend'>")
    for label, colour in LEGEND:
        parts.append(f"<span><i class='chip' style='background:{colour}'></i>"
                     f"{html.escape(label)}</span>")
    parts.append("</div></div>")
    return "".join(parts)


def write_preview(view: DocView, home: Path, subtitle: str = "") -> Path:
    out = home / "preview"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in view.doc_id)[:60]
    p = out / f"{safe}.html"
    p.write_text(render_html(view, subtitle))
    return p
