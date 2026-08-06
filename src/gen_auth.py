"""Step 1 of manual OAuth: generate auth URL + persist PKCE session.

Run in foreground (fast):
    .venv/bin/python src/gen_auth.py
Then authorize in the browser and paste the callback URL back, e.g.:
    .venv/bin/python src/exchange_upload.py "<pasted URL>"
"""

import json
import webbrowser
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://127.0.0.1:9999/"

BASE = Path(__file__).resolve().parent.parent
CLIENT = BASE / "config" / "client_secret.json"
SESSION_FILE = BASE / "config" / "oauth_session.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT), SCOPES, redirect_uri=REDIRECT_URI
    )
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")

    session = {
        "code_verifier": flow.code_verifier,
        "state": flow.oauth2session._state,
        "client_id": flow.client_config["client_id"],
        "client_secret": flow.client_config.get("client_secret", ""),
        "token_uri": flow.client_config["token_uri"],
        "redirect_uri": REDIRECT_URI,
    }
    SESSION_FILE.write_text(json.dumps(session, indent=2))
    print(f"Session saved -> {SESSION_FILE}", flush=True)

    webbrowser.open(url, new=1)
    print("Browser opened. Log in and click Allow.", flush=True)
    print("Then copy the address-bar URL and run exchange_upload.py with it.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
