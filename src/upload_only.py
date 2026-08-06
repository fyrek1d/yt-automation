"""Upload the first rendered video using an existing token.json (no auth)."""

import json
import sys
from pathlib import Path

from uploader import YouTubeUploader

BASE = Path(__file__).resolve().parent.parent
CLIENT = BASE / "config" / "client_secret.json"
TOKEN = BASE / "config" / "token.json"


def main():
    cfg = json.loads((BASE / "config" / "config.json").read_text())
    meta = cfg["metadata"]
    vids = list((BASE / "output" / "videos").glob("*.mp4"))
    ths = list((BASE / "output" / "thumbnails").glob("*.jpg"))
    if not vids:
        print("No rendered video found.")
        return 1

    uploader = YouTubeUploader(
        client_secret_path=str(CLIENT), token_path=str(TOKEN)
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
    print(f"Published: https://youtu.be/{video_id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
