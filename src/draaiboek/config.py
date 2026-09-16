"""Configuration. Everything is env-driven; nothing is hardcoded to a machine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .secrets import load_env

_PKG_ROOT = Path(__file__).resolve().parent.parent.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ[env]).expanduser() if os.environ.get(env) else default


@dataclass(frozen=True)
class Config:
    # --- state: ledger, snapshots, tombstones -------------------------------
    home: Path
    # --- rules: human-editable, hot-reloaded on every call ------------------
    rules_dir: Path
    # --- google auth --------------------------------------------------------
    client_secret: Path
    token: Path
    # --- documents ----------------------------------------------------------
    template_doc_id: str | None
    sandbox_doc_id: str | None
    drive_folder_id: str | None
    # --- workspace ----------------------------------------------------------
    # Where Larissa reaches `draaiboek ui`. Hermes puts this in the Telegram
    # message, so in production it is the public address, not localhost.
    public_url: str = "http://127.0.0.1:8765"
    # Required before the workspace accepts connections from other machines.
    ui_key: str | None = None
    # Hermes proposes; only Larissa's deploy writes. DRAAIBOEK_DIRECT_APPLY=1
    # lets the MCP apply_edits tool write without her (operator use only).
    direct_apply: bool = False
    # A person can also say yes in the conversation instead of the workspace.
    # DRAAIBOEK_CHAT_DEPLOY=1 allows deploy_proposal, which still requires an
    # existing proposal, a named approver and their words quoted literally.
    # This is not direct_apply: nothing here lets the agent write unreviewed.
    chat_deploy: bool = False

    @property
    def ledger_path(self) -> Path:
        return self.home / "ledger.jsonl"

    @property
    def snapshots_dir(self) -> Path:
        return self.home / "snapshots"

    @property
    def tombstones_dir(self) -> Path:
        return self.home / "tombstones"

    @property
    def house_rules_path(self) -> Path:
        return self.rules_dir / "house_rules.md"

    @property
    def venue_path(self) -> Path:
        return self.rules_dir / "venue.yaml"

    @property
    def guards_path(self) -> Path:
        return self.rules_dir / "guards.yaml"

    def ensure_dirs(self) -> None:
        for d in (self.home, self.snapshots_dir, self.tombstones_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    # The env file must land before anything reads os.environ.
    load_env()
    home = _path("DRAAIBOEK_HOME", Path.home() / ".draaiboek")
    cfg = Config(
        home=home,
        rules_dir=_path("DRAAIBOEK_RULES", _PKG_ROOT / "rules"),
        client_secret=_path(
            "DRAAIBOEK_CLIENT_SECRET",
            _path("GOOGLE_CREDENTIALS", home / "client_secret.json"),
        ),
        token=_path("DRAAIBOEK_TOKEN", home / "token.json"),
        template_doc_id=os.environ.get("DRAAIBOEK_TEMPLATE_DOC_ID") or None,
        sandbox_doc_id=os.environ.get("DRAAIBOEK_SANDBOX_DOC_ID") or None,
        drive_folder_id=os.environ.get("DRAAIBOEK_DRIVE_FOLDER_ID") or None,
        public_url=(os.environ.get("DRAAIBOEK_PUBLIC_URL") or "http://127.0.0.1:8765").rstrip("/"),
        ui_key=os.environ.get("DRAAIBOEK_UI_KEY") or None,
        direct_apply=os.environ.get("DRAAIBOEK_DIRECT_APPLY", "").strip().lower()
        in ("1", "true", "yes"),
        chat_deploy=os.environ.get("DRAAIBOEK_CHAT_DEPLOY", "").strip().lower()
        in ("1", "true", "yes"),
    )
    cfg.ensure_dirs()
    return cfg
