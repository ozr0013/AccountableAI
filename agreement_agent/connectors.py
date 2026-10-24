"""Where documents come from: a Gmail inbox, or a local folder (tests and offline demos).

Gmail is read-only (scope gmail.readonly): it lists messages that match a query and pulls their
PDF / text attachments. Setup, once:
  1. Google Cloud console: enable the Gmail API, create an OAuth client ("Desktop app"), download
     it as agreement_agent/credentials.json (gitignored).
  2. First run opens a browser to sign in; the token is saved to agreement_agent/token.json.
"""

from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import json
import mimetypes
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from .models import SourceDoc

HERE = Path(__file__).resolve().parent
# Scopes are deliberately separate. The dashboard connection is a normal Google sign-in: it
# receives only the user's basic profile and never grants inbox access. The optional document
# scanner uses Gmail read-only access and must be enabled as a distinct product feature.
PROFILE_SCOPES = ["openid", "email", "profile"]
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
WANTED = {"application/pdf", "text/plain"}
DEFAULT_QUERY = "has:attachment (filename:pdf OR filename:txt) (agreement OR contract OR invoice OR terms)"


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Local midnight to midnight, as aware datetimes."""
    start = datetime.combine(day, time.min).astimezone()
    return start, start + timedelta(days=1)


def _id(*parts: str) -> str:
    return "doc-" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _wanted(filename: str, mime: str) -> bool:
    return mime in WANTED or filename.lower().endswith((".pdf", ".txt"))


class LocalFolder:
    """PDFs and .txt files in a folder; .eml files contribute their attachments."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self, limit: int | None = None) -> Iterator[SourceDoc]:
        files = sorted(self.path.iterdir()) if self.path.is_dir() else [self.path]
        n = 0
        for f in files:
            if limit is not None and n >= limit:
                return
            if f.suffix.lower() == ".eml":
                for d in from_eml(f.read_bytes(), source="local"):
                    n += 1
                    yield d
            elif _wanted(f.name, mimetypes.guess_type(f.name)[0] or ""):
                n += 1
                yield SourceDoc(id=_id("local", str(f)), source="local", filename=f.name,
                                mime=mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                                data=f.read_bytes(),
                                received_at=datetime.fromtimestamp(f.stat().st_mtime, UTC))


def from_eml(raw: bytes, source: str = "local") -> list[SourceDoc]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    out = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "attachment"
        mime = part.get_content_type()
        if _wanted(name, mime):
            out.append(SourceDoc(id=_id(source, msg.get("Message-ID", ""), name), source=source,
                                 filename=name, mime=mime, data=part.get_payload(decode=True) or b"",
                                 sender=msg.get("From"), subject=msg.get("Subject")))
    return out


class Gmail:
    def __init__(self, credentials: str | Path = HERE / "credentials.json",
                 token: str | Path = HERE / "token.json", service=None, interactive: bool = True):
        """interactive=False (the server): never open a browser sign-in; raise instead, and the
        server sends the user through its Sign in with Google button."""
        self.credentials, self.token = Path(credentials), Path(token)
        self.interactive = interactive
        self._service = service              # tests pass a stand-in
        self._credentials = None

    def _has_current_scopes(self) -> bool:
        """The credential library reflects requested scopes; the token file records granted ones."""
        if not self.token.exists():
            return False
        try:
            granted = set(json.loads(self.token.read_text()).get("scopes") or [])
        except (OSError, ValueError):
            return False
        return set(SCOPES).issubset(granted)

    def service(self):
        if self._service is None:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
            creds = None
            if self.token.exists():
                creds = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            if not creds or not creds.valid or not self._has_current_scopes():
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    if not self._has_current_scopes():
                        creds = None
                elif not self.interactive:
                    raise PermissionError("not signed in to Google")
                if not creds or not creds.valid or not self._has_current_scopes():
                    if not self.interactive:
                        raise PermissionError("Google profile permission needs approval. Sign in again.")
                    if not self.credentials.exists():
                        raise FileNotFoundError(
                            f"Gmail OAuth client not found at {self.credentials}. See the setup "
                            "notes at the top of agreement_agent/connectors.py.")
                    creds = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials), SCOPES).run_local_server(
                        host="127.0.0.1", port=0, open_browser=True,
                        authorization_prompt_message="Sign in to Gmail (read-only): {url}")
                self.token.write_text(creds.to_json())
            self._credentials = creds
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def credentials_for_profile(self):
        """Return current OAuth credentials after validating/refreshing them."""
        self.service()
        return self._credentials

    def fetch(self, query: str = DEFAULT_QUERY, limit: int = 20,
              sent_on: date | None = None) -> Iterator[SourceDoc]:
        """sent_on: only mail that arrived on that (local) day. Gmail's search is bounded to the
        day, and each message's own timestamp is checked again, so nothing older slips through."""
        svc = self.service().users()
        if sent_on is not None:
            start, end = day_bounds(sent_on)
            query = f"{query} after:{int(start.timestamp())} before:{int(end.timestamp())}"
        res = svc.messages().list(userId="me", q=query, maxResults=limit).execute()
        seen: set[str] = set()
        for ref in res.get("messages", []):
            msg = svc.messages().get(userId="me", id=ref["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            when = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, UTC)
            if sent_on is not None and not (start <= when < end):
                continue
            for part in _parts(msg.get("payload", {})):
                name, mime = part.get("filename") or "", part.get("mimeType", "")
                if not name or not _wanted(name, mime):
                    continue
                body = part.get("body", {})
                if "attachmentId" in body:
                    att = svc.messages().attachments().get(userId="me", messageId=ref["id"],
                                                           id=body["attachmentId"]).execute()
                    raw = att.get("data", "")
                else:
                    raw = body.get("data", "")
                doc_id = _id("gmail", ref["id"], name, part.get("partId", ""))
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                yield SourceDoc(id=doc_id, source="gmail", filename=name,
                                mime=mime or "application/octet-stream",
                                data=base64.urlsafe_b64decode(raw.encode() + b"==="),
                                sender=headers.get("from"), subject=headers.get("subject"),
                                received_at=when, message_id=ref["id"])


def _parts(payload: dict) -> Iterator[dict]:
    yield payload
    for p in payload.get("parts", []) or []:
        yield from _parts(p)
