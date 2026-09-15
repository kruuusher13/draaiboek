"""The call list -- the draaiboek's open points, grouped by who to phone.

Larissa, 15 Sep 2026, 15:13:
    "Ik wil ze kunnen bellen, met deze punten in het draaiboek als to do lijst"

She already builds this by hand. Her own wedding draaiboek ends with a
"BELLEN - LEVERANCIERS" block: a supplier, their number, and beneath it the
questions to ask that person. This derives the same thing from the document,
so it is never out of step with the schedule above it.

A contact with no number is not skipped -- getting the number IS the task, and
it is the one she chases most ("altijd checken of ik de telefoonnummers heb
ontvangen van de ceremoniemeester(s)").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import DocView, Row

PHONE = re.compile(r"\+?\d[\d\s\-().]{6,}\d")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# "nog opvragen", "[nog bevestigen]", "nog geen antwoord", "nog in afwachting"
PENDING = re.compile(
    r"(?i)nog\s+(?:op\s*te\s*vragen|opvragen|bevestigen|ontvangen|inplannen|"
    r"afstemmen|te\s+bepalen|niet\s+zeker|in\s+afwachting)"
    r"|\[\s*nog[^\]]*\]|nog\s+geen\s+antwoord|\bonbekend\b|\?\s*$"
)

# Role words that tie a question to a supplier even when no name is written.
GROUP_LABEL = {
    "cateraar": "Cateraar", "bloemist": "Bloemist / decoratie",
    "technicus": "Techniek", "band": "Band / muziek", "dj": "DJ",
    "videograaf": "Videograaf", "fotograaf": "Fotograaf",
    "ceremoniemeester": "Ceremoniemeester", "bruidspaar": "Bruidspaar",
    "leverancier": "Leveranciers",
}

ROLES = {
    "cateraar": ("cateraar", "catering", "traiteur"),
    "bloemist": ("bloemist", "bloemen", "decoteam", "decoratie", "styliste", "stylist"),
    "technicus": ("technicus", "techniek", "geluid", "licht", "soundcheck"),
    "band": ("band", "muziek", "musici", "musicus", "bandleider", "invictus"),
    "dj": ("dj",),
    "videograaf": ("videograaf", "video", "film"),
    "fotograaf": ("fotograaf", "fotoshoot", "foto"),
    # "ceremonie" alone is too loose -- it also matches "Bandleider ceremonie".
    "ceremoniemeester": ("ceremoniemeester", "weddingplanner"),
    "bruidspaar": ("bruidspaar", "bruid", "bruidegom", "opdrachtgever", "klant"),
    "leverancier": ("triade", "levering", "leverancier"),
}

# Already dealt with -- an answered question is not a call to make.
RESOLVED = re.compile(
    r"(?i)akkoord\s+ontvangen|\bafgehandeld\b|\bbevestigd\b|\bgeregeld\b|"
    r"\bontvangen\s+op\b|\bbesteld\b|^\s*(?:ok|gedaan|klaar)\b|✓"
)

UNASSIGNED = "Nog uit te zoeken"


@dataclass
class Task:
    text: str
    row_id: str | None = None
    section: str | None = None
    kind: str = "vraag"          # vraag | nummer | herinnering

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "row_id": self.row_id,
                "section": self.section, "kind": self.kind}


@dataclass
class Target:
    name: str
    role: str = ""
    phone: str = ""
    note: str = ""
    email: str = ""
    row_id: str | None = None
    tasks: list[Task] = field(default_factory=list)
    members: list[dict[str, str]] = field(default_factory=list)

    @property
    def callable(self) -> bool:
        return bool(self.phone) and not PENDING.search(self.phone)

    @property
    def is_bucket(self) -> bool:
        return self.name == UNASSIGNED

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role,
                "phone": self.phone if self.callable else "",
                "phone_pending": not self.callable and not self.is_bucket,
                "bucket": self.is_bucket,
                "email": self.email, "note": self.note, "row_id": self.row_id,
                "members": self.members,
                "tasks": [t.to_dict() for t in self.tasks]}


def _tokens(*parts: str) -> set[str]:
    out: set[str] = set()
    for p in parts:
        for tok in re.split(r"[^\wÀ-ÿ]+", (p or "").lower()):
            if len(tok) >= 4:
                out.add(tok)
    return out


def _contacts(view: DocView) -> list[Target]:
    """Read the call sheet. Columns vary, so find them by header name."""
    out: list[Target] = []
    for table in view.tables:
        low = [h.strip().lower() for h in table.headers]
        if not ({"naam"} & set(low)):
            continue
        def col(*names: str, default: int | None = None) -> int | None:
            for n in names:
                if n in low:
                    return low.index(n)
            return default
        c_name = col("naam", default=0)
        c_role = col("rol", "functie")
        c_tel = col("telefoon", "telefoonnummer", "tel")
        c_info = col("overige info", "aanwezig", "opmerkingen", "info")

        for row in table.rows:
            if row.kind != "data" or not row.values:
                continue
            def cell(i: int | None) -> str:
                return row.values[i].strip() if i is not None and i < len(row.values) else ""
            name = cell(c_name)
            if not name:
                continue
            blob = " ".join(row.values)
            phone = cell(c_tel)
            if not phone or PENDING.search(phone):
                found = PHONE.search(blob)
                phone = found.group(0).strip() if found else phone
            mail = EMAIL.search(blob)
            out.append(Target(
                name=name, role=cell(c_role), phone=phone.strip(),
                note=cell(c_info), email=mail.group(0) if mail else "",
                row_id=row.row_id,
            ))
    return out


def _contact_row_ids(view: DocView) -> set[str]:
    """Call-sheet rows are people, not questions. A contact whose number is
    still missing already becomes its own task; it must not also arrive as an
    open point."""
    ids: set[str] = set()
    for table in view.tables:
        low = [h.strip().lower() for h in table.headers]
        if "naam" in low:
            ids.update(r.row_id for r in table.rows if r.kind == "data")
    return ids


def _open_items(view: DocView) -> list[tuple[Row, str]]:
    """Every unresolved thing in the document, wherever it sits.

    Open Punten is the obvious place, but half of what she still has to chase
    is written inline as "[nog opvragen]" next to the thing it belongs to.
    """
    items: list[tuple[Row, str]] = []
    seen: set[str] = set()

    section = view.section_by_name("Open Punten")
    open_ids = set(section.row_ids) if section else set()
    contacts = _contact_row_ids(view)

    for row in view.data_rows():
        text = row.joined().strip()
        if not text or text in seen or row.row_id in contacts:
            continue
        if RESOLVED.search(text):
            continue
        if row.row_id in open_ids or PENDING.search(text):
            seen.add(text)
            items.append((row, text))
    return items


def _question_text(row: Row) -> str:
    """The readable question: the longest cell, with the label kept as context."""
    cells = [c.strip() for c in row.values if c.strip()]
    if not cells:
        return ""
    if len(cells) == 1:
        return cells[0]
    body = max(cells[1:], key=len) if len(cells) > 1 else cells[0]
    label = cells[0]
    return f"{label} — {body}" if label and label.lower() not in body.lower() else body


def build(view: DocView, reminders: list[str] | None = None) -> list[dict[str, Any]]:
    """Group everything still open under the person who can answer it."""
    targets = _contacts(view)
    index: list[tuple[Target, set[str], set[str]]] = []
    for t in targets:
        index.append((t, _tokens(t.name), _tokens(t.role) | _role_words(t)))

    for target, _, _ in index:
        if not target.callable:
            target.tasks.append(Task(
                text=f"Telefoonnummer van {target.name} opvragen",
                row_id=target.row_id, kind="nummer",
            ))

    unassigned = Target(name=UNASSIGNED)

    groups: dict[str, Target] = {}

    def assign(text: str) -> Target:
        """Name beats role.

        When a role matches several people -- a couple, or two bands -- the
        question goes to a group holding both numbers rather than being guessed
        onto one of them. She can ring either; putting the evening band's rider
        in front of the ceremony bandleader wastes a call.
        """
        toks = _tokens(text)
        for target, name_toks, _ in index:
            if name_toks & toks:
                return target
        low = text.lower()
        hits: list[tuple[Target, str]] = []
        for target, _, _ in index:
            for group, synonyms in ROLES.items():
                blob = f"{target.role} {target.name}".lower()
                if any(s in blob for s in synonyms) and any(s in low for s in synonyms):
                    hits.append((target, group))
                    break
        if not hits:
            return unassigned
        if len(hits) == 1:
            return hits[0][0]
        group = hits[0][1]
        if not all(g == group for _, g in hits):
            return unassigned
        label = GROUP_LABEL.get(group, group.title())
        if label not in groups:
            groups[label] = Target(
                name=label, role="",
                phone=next((t.phone for t, _ in hits if t.callable), ""),
                members=[{"name": t.name, "phone": t.phone if t.callable else ""}
                         for t, _ in hits],
            )
        return groups[label]

    for row, text in _open_items(view):
        assign(text).tasks.append(
            Task(text=_question_text(row), row_id=row.row_id, section=row.section))

    for r in reminders or []:
        assign(r).tasks.append(Task(text=r, kind="herinnering"))

    # The unassigned bucket is a list of things to place, not a person to ring.
    unassigned.phone = ""
    out = [t for t in targets if t.tasks] + [g for g in groups.values() if g.tasks]
    if unassigned.tasks:
        out.append(unassigned)
    # Callable people with real questions first; number-chasing next; the
    # unassigned bucket last, because she cannot act on it by phone.
    out.sort(key=lambda t: (t.name == UNASSIGNED, not t.callable, -len(t.tasks)))
    return [t.to_dict() for t in out]


def _role_words(t: Target) -> set[str]:
    blob = f"{t.role} {t.name}".lower()
    words: set[str] = set()
    for _, synonyms in ROLES.items():
        if any(s in blob for s in synonyms):
            words.update(synonyms)
    return words
