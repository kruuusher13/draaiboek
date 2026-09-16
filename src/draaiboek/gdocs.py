"""Google API access. Thin, lazy, and the only module that touches the network."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import Config

# Docs and Drive are accessed as the service account itself -- it owns or is
# shared on the documents. Gmail is different: a service account has no mailbox,
# so reading mail requires domain-wide delegation and impersonation. Mixing the
# two breaks Docs, because an impersonated token is rejected outright when DWD
# is not configured for that scope.
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def blob_client_id(secret: Path) -> str:
    try:
        return json.loads(secret.read_text()).get("client_id", "(unknown)")
    except Exception:  # noqa: BLE001
        return "(unknown)"


class AuthError(RuntimeError):
    """Raised with a runnable remedy, never a bare stack trace.

    Token expiry used to surface to Larissa as "ik kan de doc niet aanpassen"
    with no idea what to do. The message here is the fix.
    """


class Google:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._docs = None
        self._drive = None
        self._delegation: dict[str, bool] = {}

    # -- credentials -------------------------------------------------------
    def _credentials(self, scopes: list[str] | None = None, subject: str | None = None):
        from google.auth.exceptions import RefreshError
        from google.oauth2.credentials import Credentials
        from google.oauth2 import service_account

        secret = self.cfg.client_secret
        if not secret.exists():
            raise AuthError(
                f"No Google credentials at {secret}. Put the OAuth client secret (or a "
                f"service-account key) there, or set DRAAIBOEK_CLIENT_SECRET."
            )

        # service account: no interactive flow, no expiry -- preferred on the Mac Mini
        try:
            blob = json.loads(secret.read_text())
        except json.JSONDecodeError as e:
            raise AuthError(f"{secret} is not valid JSON: {e}") from e
        if blob.get("type") == "service_account":
            return self._sa_credentials(secret, scopes or SCOPES, subject)

        # installed-app OAuth
        creds = None
        if self.cfg.token.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.cfg.token), (scopes or SCOPES) + GMAIL_SCOPES)
            except (ValueError, json.JSONDecodeError):
                creds = None
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            try:
                creds.refresh(Request())
                self.cfg.token.write_text(creds.to_json())
                return creds
            except RefreshError as e:
                raise AuthError(
                    f"Google token refresh failed ({e}). Re-authorise with:\n"
                    f"    draaiboek auth"
                ) from e
        raise AuthError(
            "Not authorised with Google yet. Run:\n    draaiboek auth\n"
            "(or switch to a service-account key, which does not expire)"
        )

    def authorise_interactive(self) -> Path:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(self.cfg.client_secret), SCOPES)
        creds = flow.run_local_server(port=0)
        self.cfg.token.write_text(creds.to_json())
        self.cfg.token.chmod(0o600)
        return self.cfg.token

    def _sa_credentials(self, secret: Path, scopes: list[str], subject: str | None):
        """Service-account credentials, impersonating the mailbox owner when
        domain-wide delegation allows it.

        Delegation is detected, not configured: we try impersonation once and
        fall back to the bare service account if Workspace rejects it. So the
        day DWD is switched on, documents start resolving as Larissa with no
        code or config change -- and until then, Docs keeps working on whatever
        is shared directly.
        """
        from google.auth.exceptions import GoogleAuthError
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        key = ",".join(sorted(scopes))
        plain = service_account.Credentials.from_service_account_file(
            str(secret), scopes=scopes
        )
        if not subject:
            return plain
        if self._delegation.get(key) is False:
            return plain

        delegated = service_account.Credentials.from_service_account_file(
            str(secret), scopes=scopes
        ).with_subject(subject)
        if self._delegation.get(key) is True:
            return delegated
        try:
            delegated.refresh(Request())
            self._delegation[key] = True
            return delegated
        except GoogleAuthError as e:
            if "gmail" in key:
                # No mailbox without delegation -- there is nothing to fall back to.
                raise AuthError(
                    f"Cannot read {subject}'s mailbox: domain-wide delegation is not "
                    f"authorised for this service account.\n\n"
                    f"In Google Workspace admin -> Security -> Access and data control "
                    f"-> API controls -> Domain-wide delegation, add client ID "
                    f"{blob_client_id(secret)} with scope:\n"
                    f"    https://www.googleapis.com/auth/gmail.readonly\n\n"
                    f"Underlying error: {e}"
                ) from e
            self._delegation[key] = False
            return plain

    def gmail_credentials(self):
        """Impersonated credentials for one mailbox. Requires domain-wide
        delegation to be granted for the Gmail scope in Workspace admin."""
        import os
        subject = os.environ.get("GOOGLE_IMPERSONATE", "").strip()
        if not subject:
            raise AuthError("GOOGLE_IMPERSONATE is not set; cannot read a mailbox.")
        return self._credentials(scopes=GMAIL_SCOPES, subject=subject)

    # -- services ----------------------------------------------------------
    @property
    def docs(self):
        if self._docs is None:
            from googleapiclient.discovery import build
            self._docs = build("docs", "v1", credentials=self._credentials(),
                               cache_discovery=False)
        return self._docs

    @property
    def drive(self):
        if self._drive is None:
            from googleapiclient.discovery import build
            self._drive = build("drive", "v3", credentials=self._credentials(),
                                cache_discovery=False)
        return self._drive

    # -- operations --------------------------------------------------------
    def get_document(self, doc_id: str) -> dict[str, Any]:
        return self._retrying(
            lambda: self.docs.documents().get(documentId=doc_id).execute())

    def batch_update(self, doc_id: str, requests: list[dict]) -> dict[str, Any]:
        if not requests:
            return {}
        return self._retrying(
            lambda: self.docs.documents().batchUpdate(
                documentId=doc_id, body={"requests": requests}).execute())

    @staticmethod
    def _retrying(call, attempts: int = 6):
        """Google allows sixty writes a minute per user, and a document built
        table by table runs through that. Rate limits and brief unavailability
        are waits, not failures."""
        from googleapiclient.errors import HttpError

        delay = 2.0
        for n in range(attempts):
            try:
                return call()
            except HttpError as e:
                status = getattr(e, "status_code", None) or getattr(e.resp, "status", 0)
                if status not in (429, 500, 503) or n == attempts - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30)
        raise RuntimeError("unreachable")

    def copy_document(self, template_id: str, name: str, folder_id: str | None) -> str:
        body: dict[str, Any] = {"name": name}
        if folder_id:
            body["parents"] = [folder_id]
        created = self.drive.files().copy(
            fileId=template_id, body=body, supportsAllDrives=True,
            fields="id",
        ).execute()
        return created["id"]

    def share(self, file_id: str, email: str, role: str = "writer") -> None:
        self.drive.permissions().create(
            fileId=file_id, sendNotificationEmail=False,
            body={"type": "user", "role": role, "emailAddress": email},
            supportsAllDrives=True,
        ).execute()

    def get_revision(self, doc_id: str) -> str:
        return self.docs.documents().get(
            documentId=doc_id, fields="revisionId"
        ).execute().get("revisionId", "")

    def wait_for_rows(self, doc_id: str, table: int, expected: int,
                      attempts: int = 6, delay: float = 0.35) -> dict[str, Any]:
        """Poll until the structural change is visible.

        Replaces the old `sleep(1.5)`: it waits for the actual condition, and
        fails loudly instead of writing text against stale indices.
        """
        from .reader import parse_document

        doc = self.get_document(doc_id)
        for _ in range(attempts):
            view = parse_document(doc)
            if table < len(view.tables) and len(view.tables[table].rows) == expected:
                return doc
            time.sleep(delay)
            doc = self.get_document(doc_id)
        raise RuntimeError(
            f"Table {table} still has "
            f"{len(parse_document(doc).tables[table].rows)} rows, expected {expected}. "
            f"Structural change did not settle; no text was written."
        )
