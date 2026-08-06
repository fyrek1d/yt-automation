"""Delete a video from the channel using the saved token."""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def main():
    video_id = sys.argv[1] if len(sys.argv) > 1 else "VlAZkuyxfgY"
    from uploader import YouTubeUploader

    uploader = YouTubeUploader(
        client_secret_path=str(BASE / "config" / "client_secret.json"),
        token_path=str(BASE / "config" / "token.json"),
    )
    result = uploader.delete(video_id)
    print(f"Deleted {video_id}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
