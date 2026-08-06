"""Exchange a pasted OAuth code for a token, then upload.

Usage: python src/finish_auth.py "<full redirect URL from address bar>"
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from uploader import YouTubeUploader

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://127.0.0.1:9999/"

BASE = Path(__file__).resolve().parent.parent
CLIENT_SECRET = BASE / "config" / "client_secret.json"
TOKEN_PATH = BASE / "config" / "token.json"


def main():
    if len(sys.argv) < 2:
        print("Usage: finish_auth.py '<redirect URL>'")
        return 1
    pasted = sys.argv[1].strip()
    pasted = pasted.replace("https://127.0.0.1", "http://127.0.0.1")
    pasted = pasted.replace("https://localhost", "http://localhost")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET), SCOPES, redirect_uri=REDIRECT_URI
    )
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
        client_secret_path=str(CLIENT_SECRET), token_path=str(TOKEN_PATH)
    )
    video_id = uploader.upload(
        video_path=str(vids[0]),
        title=meta["title_format"],
        description=meta["description_template"].format(
            url="https://www.reddit.com", title=vids[0].name
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
