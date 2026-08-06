"""One-time manual OAuth + upload.

Because LibreWolf cannot reach the localhost callback server, this uses the
manual paste-back flow:

  1. It opens a browser tab and writes the authorization URL to
     logs/auth_url.txt
  2. You authorize with Google. The browser lands on a 127.0.0.1 URL that
     fails to load — THAT IS NORMAL. Copy the full URL from the address bar
     (it contains ?code=...) and paste it into config/oauth_paste.txt
  3. This script detects the file, exchanges the code, saves config/token.json
  4. Uploads the already-rendered video + thumbnail.

Run detached:
  setsid .venv/bin/python src/auth_upload.py > logs/auth_upload.log 2>&1 < /dev/null &
"""

import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from uploader import YouTubeUploader

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://127.0.0.1:9999/"

BASE = Path(__file__).resolve().parent.parent
CLIENT_SECRET = BASE / "config" / "client_secret.json"
TOKEN_PATH = BASE / "config" / "token.json"
PASTE_FILE = BASE / "config" / "oauth_paste.txt"
AUTH_URL_FILE = BASE / "logs" / "auth_url.txt"
TIMEOUT = 10 * 60  # 10 minutes


def save_token(flow):
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    TOKEN_PATH.write_text(flow.credentials.to_json())
    print(f"token.json saved ({TOKEN_PATH})", flush=True)


def wait_for_paste():
    if PASTE_FILE.exists():
        PASTE_FILE.unlink()
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        if PASTE_FILE.exists():
            url = PASTE_FILE.read_text().strip()
            if url:
                return url
        time.sleep(2)
    raise RuntimeError("Timed out waiting for pasted URL in config/oauth_paste.txt")


def main():
    if not CLIENT_SECRET.exists():
        print("ERROR: config/client_secret.json not found", flush=True)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET), SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent"
    )

    BASE.joinpath("logs").mkdir(exist_ok=True)
    AUTH_URL_FILE.write_text(auth_url)
    print(f"Auth URL written to {AUTH_URL_FILE}", flush=True)

    webbrowser.open(auth_url, new=1)
    print("Opened browser. Waiting for you to paste the callback URL...", flush=True)

    pasted = wait_for_paste()
    # LibreWolf may have upgraded http:// to https:// in the address bar.
    pasted = pasted.replace("https://127.0.0.1", "http://127.0.0.1")
    pasted = pasted.replace("https://localhost", "http://localhost")
    print("Code received. Exchanging for token...", flush=True)

    flow.fetch_token(authorization_response=pasted)
    save_token(flow)

    # ---- upload the existing video ---------------------------------------
    video = BASE / "output" / "videos"
    thumbs = BASE / "output" / "thumbnails"
    vids = list(video.glob("*.mp4"))
    ths = list(thumbs.glob("*.jpg"))
    if not vids:
        print("No rendered video found; token saved but nothing uploaded.", flush=True)
        return 0

    cfg = json.loads((BASE / "config" / "config.json").read_text())
    meta = cfg["metadata"]
    title = meta["title_format"]
    description = meta["description_template"].format(
        url="https://www.reddit.com", title=os.path.basename(vids[0])
    )

    uploader = YouTubeUploader(
        client_secret_path=str(CLIENT_SECRET), token_path=str(TOKEN_PATH)
    )
    video_id = uploader.upload(
        video_path=str(vids[0]),
        title=title,
        description=description,
        tags=meta["tags"],
        thumbnail_path=str(ths[0]) if ths else None,
        category_id=cfg["channel"]["category_id"],
        privacy_status=cfg["channel"]["privacy"],
    )
    print(f"Published: https://youtu.be/{video_id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
