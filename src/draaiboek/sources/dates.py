"""Date parsing for searches. Larissa asks for "het draaiboek voor 26 september";
people also type "26th september", "september 26", "26/9" or "2026/09/26"."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2,
    "maart": 3, "mrt": 3, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "mei": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
    "aug": 8, "augustus": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oct": 10, "oktober": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
NL_MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
             "september", "oktober", "november", "december"]
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORD = r"(?:e|ste|de|st|nd|rd|th)?"


def _safe(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _no_year(today: date, month: int, day: int) -> date | None:
    """No year given: this year, unless that is well past -- then next year."""
    d = _safe(today.year, month, day)
    if d and (today - d).days > 180:
        d = _safe(today.year + 1, month, day)
    return d


def find_date(text: str, today: date | None = None) -> tuple[date | None, str]:
    """(date, the text with the date taken out). Never guesses: (None, text)."""
    raw = text or ""
    t = raw.lower()
    today = today or date.today()

    def hit(d, m):
        return (d, (raw[:m.start()] + " " + raw[m.end():]).strip()) if d else None

    for word, delta in (("vandaag", 0), ("today", 0), ("morgen", 1), ("tomorrow", 1),
                        ("gisteren", -1), ("yesterday", -1)):
        m = re.search(rf"\b{word}\b", t)
        if m:
            return hit(today + timedelta(days=delta), m)

    patterns = [
        # 2026/09/26, 2026-09-26
        (r"\b(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\b",
         lambda m: _safe(int(m[1]), int(m[2]), int(m[3]))),
        # 26-09-2026, 26/09/2026
        (r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b",
         lambda m: _safe(int(m[3]), int(m[2]), int(m[1]))),
        # 26 september (2026), 26th sept, 26e september
        (rf"\b(\d{{1,2}}){_ORD}\s*(?:of\s+)?({_MONTH})\b\.?(?:,?\s+(20\d{{2}}))?",
         lambda m: _safe(int(m[3]), MONTHS[m[2]], int(m[1])) if m[3]
         else _no_year(today, MONTHS[m[2]], int(m[1]))),
        # september 26 (2026), sept 26th
        (rf"\b({_MONTH})\.?\s+(\d{{1,2}}){_ORD}\b(?:,?\s+(20\d{{2}}))?",
         lambda m: _safe(int(m[3]), MONTHS[m[1]], int(m[2])) if m[3]
         else _no_year(today, MONTHS[m[1]], int(m[2]))),
        # 26/9, 26-09
        (r"\b(\d{1,2})[/-](\d{1,2})\b(?![/-]\d)",
         lambda m: _no_year(today, int(m[2]), int(m[1])) if int(m[2]) <= 12 else None),
    ]
    for pattern, make in patterns:
        m = re.search(pattern, t)
        if m:
            found = hit(make(m), m)
            if found:
                return found
    return None, raw.strip()


def parse_date(text: str, today: date | None = None) -> date | None:
    """Find an event date in free text. Returns None rather than guessing."""
    return find_date(text, today)[0]


def date_variants(d: date) -> list[str]:
    """How that date is written in task names, labels, mail and quotes."""
    month = NL_MONTHS[d.month - 1]
    return [
        f"{d.year}/{d.month:02d}/{d.day:02d}",      # ClickUp task names, Missive labels
        f"{d.day} {month}", f"{d.day}e {month}", f"{d.day} {month[:3]}",
        f"{d.day} {d.strftime('%B').lower()}", f"{d.strftime('%B').lower()} {d.day}",
        f"{d.day:02d}-{d.month:02d}-{d.year}", f"{d.day}-{d.month}-{d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}",
        f"{d.year}-{d.month:02d}-{d.day:02d}",
    ]


def mentions(text: str, d: date) -> bool:
    low = re.sub(r"\s+", " ", (text or "").lower())
    return any(v in low for v in dict.fromkeys(date_variants(d)))


def day_bounds_ms(d: date) -> tuple[int, int]:
    """Epoch-millisecond bounds for a whole local day, for ClickUp filters."""
    start = datetime(d.year, d.month, d.day)
    return (int(start.timestamp() * 1000) - 1,
            int((start + timedelta(days=1)).timestamp() * 1000))


def from_task_name(title: str) -> date | None:
    """The event date out of "YYYY/MM/DD · Client".

    Larissa names every event task this way, and that date is the event. The
    ClickUp due date often is not -- it drifts, or it marks when somebody has
    to act on the task. Read the name first, everywhere, or two parts of this
    system will disagree about when an event is.
    """
    m = re.match(r"\s*(\d{4})/(\d{1,2})/(\d{1,2})", title or "")
    return _safe(int(m[1]), int(m[2]), int(m[3])) if m else None


def from_epoch_ms(ms) -> date | None:
    try:
        return date.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError, OSError, OverflowError):
        return None
