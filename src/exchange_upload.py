"""Step 2 of manual OAuth: exchange pasted code, save token, upload.

Run in foreground (fast):
    .venv/bin/python src/exchange_upload.py "<full redirect URL from address bar>"
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from uploader import YouTubeUploader

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

BASE = Path(__file__).resolve().parent.parent
CLIENT = BASE / "config" / "client_secret.json"
SESSION_FILE = BASE / "config" / "oauth_session.json"
TOKEN_PATH = BASE / "config" / "token.json"


def main():
    if len(sys.argv) < 2:
        print("Usage: exchange_upload.py '<pasted redirect URL>'")
        return 1
    if not SESSION_FILE.exists():
        print("No oauth_session.json — run gen_auth.py first.")
        return 1

    pasted = sys.argv[1].strip()
    # oauthlib requires an https authorization response.
    pasted = (
        pasted.replace("http://127.0.0.1", "https://127.0.0.1")
        .replace("http://localhost", "https://localhost")
    )

    sess = json.loads(SESSION_FILE.read_text())
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT), SCOPES, redirect_uri=sess["redirect_uri"]
    )
    flow.code_verifier = sess["code_verifier"]
    flow.oauth2session._state = sess["state"]
    flow.oauth2session._client.state = sess["state"]

    print("Exchanging code for token...")
    flow.fetch_token(authorization_response=pasted)
    TOKEN_PATH.write_text(flow.credentials.to_json())
    print(f"token.json saved -> {TOKEN_PATH}")

    cfg = json.loads((BASE / "config" / "config.json").read_text())
    meta = cfg["metadata"]
    vids = list((BASE / "output" / "videos").glob("*.mp4"))
    ths = list((BASE / "output" / "thumbnails").glob("*.jpg"))
    if not vids:
        print("No rendered video found; token saved only.")
        return 0

    uploader = YouTubeUploader(
        client_secret_path=str(CLIENT), token_path=str(TOKEN_PATH)
    )
    video_id = uploader.upload(
        video_path=str(vids[0]),
        title=meta["title_format"].format(
            title=Path(vids[0].name).stem, url="https://www.reddit.com"
        ),
        description=meta["description_template"].format(
            url="https://www.reddit.com", title=Path(vids[0].name).stem
        ),
        tags=meta["tags"],
        thumbnail_path=str(ths[0]) if ths else None,
        category_id=cfg["channel"]["category_id"],
        privacy_status=cfg["channel"]["privacy"],
    )
    print(f"Published: https://youtu.be/{video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
