"""Re-upload a previously rendered video without re-rendering.

Usage:
  python src/reupload.py --video output/videos/FILE.mp4 \
      --thumb output/thumbnails/ID_thumb.jpg \
      --story-id 1vao2in \
      --title "Story title here"
"""

import argparse
import json
import sys
from pathlib import Path

from uploader import YouTubeUploader


def load_config(base_dir: Path, path: str) -> dict:
    with open(base_dir / path) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--thumb", required=True)
    ap.add_argument("--story-id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--privacy", default=None)
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    cfg = load_config(base, args.config)
    paths = cfg["paths"]
    meta = cfg["metadata"]

    story = {
        "id": args.story_id,
        "title": args.title,
        "url": "https://www.reddit.com",
    }
    title = meta["title_format"].format(title=args.title)
    description = meta["description_template"].format(
        url=story["url"], title=args.title
    )

    uploader = YouTubeUploader(
        client_secret_path=str(base / paths["client_secret"]),
        token_path=str(base / paths["token"]),
    )
    privacy = args.privacy or cfg["channel"]["privacy"]
    video_id = uploader.upload(
        video_path=args.video,
        title=title,
        description=description,
        tags=meta["tags"],
        thumbnail_path=args.thumb,
        category_id=cfg["channel"]["category_id"],
        privacy_status=privacy,
    )
    print(f"Published: https://youtu.be/{video_id}")

    # Mark story posted so it isn't picked again
    log_path = base / paths["log_path"]
    posted = []
    if log_path.exists():
        posted = json.loads(log_path.read_text())
    posted.append(args.story_id)
    log_path.write_text(json.dumps(sorted(posted), indent=2))


if __name__ == "__main__":
    sys.exit(main())
