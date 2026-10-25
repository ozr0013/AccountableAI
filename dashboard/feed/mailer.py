"""Emails the middleman for the demo: chaser reminders and the anomaly detector's statement note.

    python dashboard/feed/mailer.py --sign-in      # once: Google sign-in, send-only permission

Sends from the owner's Gmail with the gmail.send scope only (it can't read mail), using the OAuth
client in agreement_agent/credentials.json. The token is agreement_agent/send-token.json
(gitignored). Without that token nothing is sent and the session carries on as before.
MIDDLEMAN_EMAIL sets the recipient; FEED_SEND_EMAIL=0 turns sending off.
"""

from __future__ import annotations

import base64
import logging
import os
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREDS = ROOT / "agreement_agent" / "credentials.json"
TOKEN = ROOT / "agreement_agent" / "send-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
MIDDLEMAN_EMAIL = os.getenv("MIDDLEMAN_EMAIL", "jkanishkha0305@gmail.com")
FOOTER = ("\n\n--\nSent by AccountableAI for Oakline Homes. Demo: the companies and invoices are "
          "fictional.")

log = logging.getLogger(__name__)


def enabled() -> bool:
    if "PYTEST_CURRENT_TEST" in os.environ:       # never from a test run
        return False
    return os.getenv("FEED_SEND_EMAIL", "1") != "0" and TOKEN.exists()


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send(subject: str, body: str, to: str = MIDDLEMAN_EMAIL) -> str:
    """Send one plain-text email; returns the Gmail message id."""
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body + FOOTER)
    raw = base64.urlsafe_b64encode(bytes(msg)).decode()
    return _service().users().messages().send(userId="me", body={"raw": raw}).execute()["id"]


def sign_in() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES).run_local_server(
        host="127.0.0.1", port=0, open_browser=True,
        authorization_prompt_message="Allow AccountableAI to send email (send only): {url}")
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    print(f"Signed in. Middleman emails go to {MIDDLEMAN_EMAIL}.")


if __name__ == "__main__":
    if "--sign-in" in sys.argv:
        sign_in()
    else:
        print(__doc__)
