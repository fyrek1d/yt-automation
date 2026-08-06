import os
import time
from typing import Optional

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

DEFAULT_TAGS = [
    "reddit stories",
    "minecraft parkour",
    "minecraft",
    "reddit",
    "am i the asshole",
    "reddit stories to fall asleep to",
    "storytime",
    "minecraft parkour gameplay",
    "parkour",
    "ambient stories",
]


class YouTubeUploader:
    """Uploads videos to YouTube via the Data API v3.

    Requires: pip install google-api-python-client google-auth-httplib2
    google-auth-oauthlib (in requirements.txt).
    """

    def __init__(self, client_secret_path: str, token_path: str = None):
        self.client_secret_path = client_secret_path
        self.token_path = token_path or os.path.join(
            os.path.dirname(client_secret_path), "token.json"
        )
        self._creds = None
        self._youtube = None

    def _authenticate(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(
                self.token_path, SCOPES
            )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secret_path, SCOPES
                )
                # Use 127.0.0.1 (not "localhost") so browsers that prefer
                # IPv6 for "localhost" (e.g. LibreWolf) don't fail to reach
                # the local callback server.
                creds = flow.run_local_server(
                    host="127.0.0.1",
                    bind_addr="127.0.0.1",
                    port=0,
                    timeout_seconds=300,
                )
            with open(self.token_path, "w") as f:
                f.write(creds.to_json())
        self._creds = creds
        self._youtube = build("youtube", "v3", credentials=creds)

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[list] = None,
        thumbnail_path: Optional[str] = None,
        category_id: str = "22",
        privacy_status: str = "public",
    ) -> str:
        """
        Upload a video to YouTube. Returns the video ID.
        category 22 = People & Blogs
        privacy: 'private' for review, 'public' to publish immediately.
        """
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        if not self._youtube:
            self._authenticate()

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4950],
                "tags": tags or DEFAULT_TAGS,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(video_path, chunksize=1024 * 1024, resumable=True)

        request = self._youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        attempts = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    print(f"Uploaded {status.progress() * 100:.0f}%")
            except Exception as e:
                from googleapiclient.errors import HttpError

                if isinstance(e, HttpError) and e.resp.status < 500:
                    raise  # auth/validation error: do not retry
                attempts += 1
                if attempts > 5:
                    raise
                print(
                    f"Upload interrupted ({type(e).__name__}); "
                    f"retrying in {attempts * 5}s..."
                )
                time.sleep(attempts * 5)

        video_id = response["id"]
        print(f"Video uploaded: https://youtu.be/{video_id}")

        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                self._youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(thumbnail_path),
                ).execute()
                print("Thumbnail set.")
            except HttpError as e:
                print(f"Thumbnail failed: {e}")

        return video_id

    def delete(self, video_id: str):
        """Delete a video owned by the channel. Requires the auth scope to
        permit it (youtube.upload may be denied)."""
        if not self._youtube:
            self._authenticate()
        return self._youtube.videos().delete(id=video_id).execute()
