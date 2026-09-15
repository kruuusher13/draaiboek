"""Credential loading. Secrets live in ~/.draaiboek/env, never in code, never
in the ledger, never in an agent's context."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read a KEY=VALUE file into os.environ without overwriting real env vars."""
    p = path or Path(os.environ.get("DRAAIBOEK_ENV", Path.home() / ".draaiboek" / "env"))
    found: dict[str, str] = {}
    if not p.exists():
        return found
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v:
            found[k] = v
            os.environ.setdefault(k, v)
    return found


def redact(value: str | None) -> str:
    if not value:
        return "(unset)"
    return f"{value[:4]}…{value[-3:]}" if len(value) > 10 else "(set)"
