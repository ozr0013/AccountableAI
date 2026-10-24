"""Google profile connector for the AccountableAI dashboard.

    python -m agreement_agent.server          # http://127.0.0.1:8771

Sign in with Google requests basic identity scopes. It never reads the inbox. The profile token
is saved to agreement_agent/profile-token.json (gitignored), separately from any scanner token.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import quote
import json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from agreement_agent.connectors import DEFAULT_QUERY, HERE, PROFILE_SCOPES

PORT = int(os.getenv("AGREEMENT_PORT", "8771"))
REDIRECT = f"http://127.0.0.1:{PORT}/"
CREDS, TOKEN = HERE / "credentials.json", HERE / "profile-token.json"
DASHBOARD = os.getenv("DASHBOARD_URL", "http://localhost:3000")

log = logging.getLogger("agreement_agent.server")
logging.getLogger("pypdf").setLevel(logging.ERROR)
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

app = FastAPI(title="Agreement agent")
app.add_middleware(CORSMiddleware, allow_origins=[DASHBOARD, "http://127.0.0.1:3000"],
                   allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

_pending: dict[str, tuple[object, str, float]] = {}     # state -> (flow, return-to url, created)
_lock = threading.Lock()
_last: dict = {"reviews": [], "skipped": [], "query": None, "sent_on": None, "at": None, "cost": 0.0}


# -- Sign in with Google -------------------------------------------------------------------
def _flow():
    from google_auth_oauthlib.flow import Flow
    if not CREDS.exists():
        raise FileNotFoundError("agreement_agent/credentials.json is missing (Google OAuth client)")
    return Flow.from_client_secrets_file(str(CREDS), scopes=PROFILE_SCOPES, redirect_uri=REDIRECT,
                                         autogenerate_code_verifier=True)


def _account() -> dict:
    """Who is signed in. This uses OpenID profile data only, never the Gmail API."""
    if not TOKEN.exists():
        return {"signed_in": False}
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2.credentials import Credentials

        granted = set(json.loads(TOKEN.read_text()).get("scopes") or [])
        if not set(PROFILE_SCOPES).issubset(granted):
            return {"signed_in": False, "error": "Connect Google again to share your profile with AccountableAI."}
        creds = Credentials.from_authorized_user_file(str(TOKEN), PROFILE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            TOKEN.write_text(creds.to_json())
            TOKEN.chmod(0o600)
        if not creds.valid:
            return {"signed_in": False, "error": "Your Google connection expired. Sign in again."}
        request = UrlRequest("https://openidconnect.googleapis.com/v1/userinfo",
                             headers={"Authorization": f"Bearer {creds.token}"})
        with urlopen(request, timeout=4) as response:  # nosec B310: fixed Google OpenID endpoint
            identity = json.loads(response.read().decode("utf-8"))
        return {
            "signed_in": True,
            "email": identity.get("email"),
            "name": identity.get("name"),
            "given_name": identity.get("given_name"),
            "picture": identity.get("picture"),
        }
    except Exception as e:                       # noqa: BLE001 - expired or revoked login
        log.warning("saved Google login is not usable: %s", e)
        return {"signed_in": False, "error": "The saved Google login expired. Sign in again."}


@app.get("/auth/google")
def google_start(next: str = "/"):
    """Send the browser to Google's basic-profile consent page."""
    try:
        flow = _flow()
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    with _lock:
        now = time.time()
        for k in [k for k, (_, _, t) in _pending.items() if now - t > 600]:
            _pending.pop(k, None)
        _pending[state] = (flow, next, now)
    return RedirectResponse(url)


@app.get("/")
def root(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    """Google redirects back here (loopback), then back to the dashboard."""
    if code or error:
        with _lock:
            flow, nxt, _ = _pending.pop(state or "", (None, "/", 0))
        if error or flow is None:
            msg = error or "That sign-in link expired. Try again."
            return RedirectResponse(f"{_back(nxt)}?login_error={quote(msg)}")
        flow.fetch_token(code=code)
        TOKEN.write_text(flow.credentials.to_json())
        TOKEN.chmod(0o600)
        return RedirectResponse(f"{_back(nxt)}?login=ok")
    return RedirectResponse(f"{DASHBOARD}/agreements")


def _back(nxt: str) -> str:
    # only bounce back to this server or the dashboard, never to an arbitrary site
    if nxt.startswith((DASHBOARD, "/")):
        return nxt
    return "/"


@app.post("/auth/logout")
def logout():
    TOKEN.unlink(missing_ok=True)
    return {"signed_in": False}


# -- the agent ------------------------------------------------------------------------------
class ScanIn(BaseModel):
    query: str = DEFAULT_QUERY
    limit: int = 15
    model: bool = False                  # Claude drives the tools (about $0.03 per document)
    sent_on: date | None = None          # only mail that arrived on this day; always set (default today)


@app.get("/api/status")
def status():
    return {**_account(), "query": DEFAULT_QUERY, "today": date.today().isoformat(),
            "model_available": bool(os.getenv("ANTHROPIC_API_KEY")), "last": _summary()}


@app.post("/api/scan")
def scan(body: ScanIn):
    return JSONResponse({"error": "Inbox scanning is off. Google is connected only for your profile."}, status_code=403)


@app.get("/api/last")
def last():
    return _summary()


def _summary() -> dict:
    rs = _last["reviews"]
    return {"reviews": rs, "skipped": _last["skipped"], "query": _last["query"],
            "sent_on": _last["sent_on"], "at": _last["at"],
            "cost": _last["cost"], "checked": len(rs),
            "escalated": sum(1 for r in rs if r.get("escalation"))}


def main() -> None:
    import uvicorn
    if not os.getenv("ANTHROPIC_API_KEY"):
        env = ROOT / ".env"
        if env.exists():                   # pick up the key for Claude mode, if the team has one
            for line in env.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
    print(f"Google profile connector on http://127.0.0.1:{PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
