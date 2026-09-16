"""What "normal" looks like for a recurring format, and what to order.

Three questions she asks that nothing could answer:

    "Wat is de normale bestelling voor deze show wat betreft glazen e.d.?"
    "Bij Triade heb je 120 en 2 meter tafels. Ik heb 11 meter tafel nodig.
     Hoeveel van welke moet ik dan bestellen?"
    "12 fusion borden moeten dus 12 x 8 sushi stukken besteld worden."

All three are arithmetic over things that were never written down: what a
format normally needs, and what shapes a supplier sells in. Recorded, they
answer themselves.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import yaml


def _load(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def identify(title: str, formats: dict[str, Any]) -> str | None:
    """Which recurring format this event is, if any."""
    low = (title or "").lower()
    for key, spec in (formats.get("formats") or {}).items():
        for needle in spec.get("match", []):
            if needle.lower() in low:
                return key
    return None


def pack(needed_m: float, lengths_m: list[float]) -> list[dict[str, Any]]:
    """Which tables to order for a given run of metres.

    Returns whole-unit combinations that reach the length, longest first --
    fewest pieces to carry -- and never short.
    """
    lengths = sorted({float(x) for x in lengths_m if x}, reverse=True)
    if not lengths or needed_m <= 0:
        return []
    out: list[dict[str, Any]] = []
    for big in lengths:
        n_big = int(needed_m // big)
        rest = round(needed_m - n_big * big, 3)
        combo = {f"{big} m": n_big} if n_big else {}
        for small in [x for x in lengths if x < big]:
            if rest <= 0:
                break
            n = math.ceil(rest / small)
            combo[f"{small} m"] = combo.get(f"{small} m", 0) + n
            rest = 0
        if rest > 0:
            combo[f"{big} m"] = combo.get(f"{big} m", 0) + 1
        total = sum(float(k.split()[0]) * v for k, v in combo.items())
        if combo and combo not in [o["order"] for o in out]:
            out.append({"order": combo, "total_m": round(total, 2),
                        "pieces": sum(combo.values())})
    out.sort(key=lambda o: (o["pieces"], o["total_m"]))
    return out[:3]


class Formats:
    def __init__(self, rules_dir: Path):
        self.dir = rules_dir

    @property
    def formats(self) -> dict[str, Any]:
        return _load(self.dir / "formats.yaml")

    @property
    def suppliers(self) -> dict[str, Any]:
        return _load(self.dir / "suppliers.yaml")

    def standard_for(self, title: str, guests: int | None = None) -> dict[str, Any]:
        f = self.formats
        key = identify(title, f)
        spec = (f.get("formats") or {}).get(key, {}) if key else {}
        out: dict[str, Any] = {
            "format": key,
            "name": spec.get("name"),
            "known": bool(key),
            "standard": {k: v for k, v in spec.items()
                         if k not in ("match", "per_head", "fixed_order")},
            "order": [],
            "reminders": list(spec.get("reminders", [])),
        }
        if not key:
            out["note"] = (
                "No recurring format recognised. Everything has to come from the "
                "sources for this event -- there is no standard to lean on.")
            return out

        for line in spec.get("fixed_order", []):
            out["order"].append(dict(line))

        if guests:
            for item, ratio in (spec.get("per_head") or {}).items():
                out["order"].append({
                    "supplier": spec.get("partner") or "—",
                    "items": [f"{math.ceil(guests * float(ratio))} x {item}"],
                    "note": f"{ratio} per gast bij {guests} gasten — "
                            f"controleren tegen de kaartverkoop",
                })
            for t in f.get("thresholds", []):
                if guests > t.get("over", 10 ** 9):
                    action = t.get("order") or t.get("arrange")
                    out["reminders"].append(
                        f"Meer dan {t['over']} gasten ({guests}): {action}.")
        return out

    def tables_for(self, metres: float) -> dict[str, Any]:
        """Answering the Triade question directly."""
        triade = next((s for s in self.suppliers.get("suppliers", [])
                       if s["name"].lower().startswith("triade")), None)
        lengths: list[float] = []
        if triade:
            for row in triade.get("catalogue", []):
                lengths += [float(x) for x in row.get("lengths_m", [])]
        return {"needed_m": metres, "lengths_available_m": sorted(set(lengths)),
                "options": pack(metres, lengths)}
