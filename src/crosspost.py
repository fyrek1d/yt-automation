"""Cross-post rendered Shorts to TikTok and Instagram via their official APIs.

TikTok  - Content Posting API  (open.tiktokapis.com)
Instagram - Graph API with Facebook Login for Business + resumable upload
  (required because our ~7MB files exceed Meta's ~5MB video_url fetch limit).

One-time OAuth setup is done with src/auth_tiktok.py / src/auth_instagram.py.
Tokens are persisted next to the client credentials under config/.

This module has no hard dependency on the rest of the pipeline; it only needs
a video file path, a caption, and valid tokens.
"""

import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIKTOK_API = "https://open.tiktokapis.com"
TIKTOK_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_SCOPE = "user.info.basic,video.upload,video.publish"

IG_GRAPH = "https://graph.facebook.com"
IG_RUPLOAD = "https://rupload.facebook.com"
IG_OAUTH = "https://www.facebook.com"
IG_SCOPE = "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list"
IG_API_VERSION = "v24.0"
IG_FB_VERSION = "v24.0"

CHUNK_SIZE = 64 * 1024 * 1024  # TikTok upload chunk (our files are one chunk)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _http_json(resp: requests.Response, context: str) -> dict:
    """Raise a helpful error when an API call does not return JSON success."""
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(
            f"{context}: HTTP {resp.status_code} (non-JSON response): "
            f"{resp.text[:500]}"
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{context}: HTTP {resp.status_code} {body}"
        )
    return body


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

class TikTokClient:
    """Publishes videos to a TikTok account (Content Posting API, Direct Post)."""

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        token_path: str,
        redirect_uri: str = "http://127.0.0.1:9999/",
    ):
        self.client_key = client_key
        self.client_secret = client_secret
        self.token_path = Path(token_path)
        self.redirect_uri = redirect_uri

    # -- auth --------------------------------------------------------------

    def auth_url(self, state: Optional[str] = None) -> str:
        import urllib.parse

        params = {
            "client_key": self.client_key,
            "response_type": "code",
            "scope": TIKTOK_SCOPE,
            "redirect_uri": self.redirect_uri,
            "state": state or uuid.uuid4().hex,
        }
        return f"{TIKTOK_AUTH}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        resp = requests.post(
            f"{TIKTOK_API}/v2/oauth/token/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            timeout=30,
        )
        body = _http_json(resp, "TikTok token exchange")
        _save_json(self.token_path, {
            "access_token": body["access_token"],
            "expires_at": time.time() + body["expires_in"],
            "refresh_token": body.get("refresh_token", ""),
            "refresh_expires_at": time.time() + body.get("refresh_expires_in", 0),
            "open_id": body.get("open_id", ""),
        })
        return body

    def _refresh(self) -> None:
        data = _load_json(self.token_path)
        resp = requests.post(
            f"{TIKTOK_API}/v2/oauth/token/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": data.get("refresh_token", ""),
            },
            timeout=30,
        )
        body = _http_json(resp, "TikTok token refresh")
        data.update({
            "access_token": body["access_token"],
            "expires_at": time.time() + body["expires_in"],
            "refresh_token": body.get("refresh_token", data.get("refresh_token", "")),
            "refresh_expires_at": time.time() + body.get("refresh_expires_in", data.get("refresh_expires_at", 0)),
        })
        _save_json(self.token_path, data)

    def _access_token(self) -> str:
        data = _load_json(self.token_path)
        if not data.get("access_token"):
            raise RuntimeError(
                "No TikTok tokens yet. Run src/auth_tiktok.py first."
            )
        # Refresh a few minutes early to avoid boundary races.
        if time.time() > data.get("expires_at", 0) - 300:
            self._refresh()
            data = _load_json(self.token_path)
        return data["access_token"]

    # -- posting -----------------------------------------------------------

    def creator_info(self) -> dict:
        resp = requests.post(
            f"{TIKTOK_API}/v2/post/publish/creator_info/query/",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json={},
            timeout=30,
        )
        body = _http_json(resp, "TikTok creator_info")
        if body.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"TikTok creator_info error: {body['error']}")
        return body["data"]

    def post(
        self,
        video_path: str,
        title: str,
        privacy_level: str = "SELF_ONLY",
        is_aigc: bool = True,
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
        cover_timestamp_ms: Optional[int] = None,
    ) -> str:
        """Upload + publish a video. Returns the TikTok post status."""
        token = self._access_token()
        size = os.path.getsize(video_path)

        # Privacy must match one of the creator's allowed options.
        allowed = {
            o.get("value")
            for o in self.creator_info().get("creator_info", {}).get(
                "privacy_level_options", []
            )
        }
        if allowed and privacy_level not in allowed:
            fallback = "SELF_ONLY" if "SELF_ONLY" in allowed else sorted(allowed)[0]
            print(
                f"[tiktok] privacy '{privacy_level}' not allowed "
                f"(audit pending?), using '{fallback}'"
            )
            privacy_level = fallback

        chunk_count = max(1, -(-size // CHUNK_SIZE))
        post_info = {
            "title": title[:2200],
            "privacy_level": privacy_level,
            "disable_comment": disable_comment,
            "disable_duet": disable_duet,
            "disable_stitch": disable_stitch,
            "is_aigc": is_aigc,
        }
        if cover_timestamp_ms is not None:
            post_info["video_cover_timestamp_ms"] = cover_timestamp_ms
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": min(size, CHUNK_SIZE),
            "total_chunk_count": chunk_count,
        }

        # 1. Initialize ------------------------------------------------------
        resp = requests.post(
            f"{TIKTOK_API}/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"post_info": post_info, "source_info": source_info},
            timeout=30,
        )
        body = _http_json(resp, "TikTok video/init")
        if body.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"TikTok video/init error: {body['error']}")
        publish_id = body["data"]["publish_id"]
        upload_url = body["data"].get("upload_url")
        if not upload_url:
            raise RuntimeError("TikTok returned no upload_url")

        # 2. Upload the file --------------------------------------------------
        print(f"[tiktok] uploading {size} bytes...")
        sent = 0
        with open(video_path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                end = sent + len(chunk) - 1
                up = requests.put(
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{end}/{size}",
                    },
                    data=chunk,
                    timeout=600,
                )
                if up.status_code >= 400:
                    raise RuntimeError(
                        f"TikTok chunk upload failed: HTTP {up.status_code} {up.text[:300]}"
                    )
                sent += len(chunk)
        print(f"[tiktok] uploaded {sent}/{size} bytes")

        # 3. Publish ----------------------------------------------------------
        resp = requests.post(
            f"{TIKTOK_API}/v2/post/publish/video/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "publish_id": publish_id,
                "source_info": {"source": "FILE_UPLOAD"},
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"[tiktok] publish call returned HTTP {resp.status_code} "
                f"({resp.text[:200]}); will rely on status polling."
            )

        # 4. Poll status -------------------------------------------------------
        status = self._poll_status(publish_id, token)
        return status

    def _poll_status(self, publish_id: str, token: str, timeout: int = 600) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = requests.post(
                f"{TIKTOK_API}/v2/post/publish/status/fetch/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"publish_id": publish_id},
                timeout=30,
            )
            body = _http_json(resp, "TikTok status/fetch")
            status = body["data"]["status"]
            print(f"[tiktok] status: {status}")
            if status == "PUBLISH_COMPLETE":
                return status
            if status == "FAILED":
                raise RuntimeError(
                    f"TikTok publish failed: {body['data'].get('fail_reason')}"
                )
            time.sleep(10)
        raise RuntimeError("Timed out waiting for TikTok publish status")


# ---------------------------------------------------------------------------
# Instagram (Facebook Login for Business + resumable upload)
# ---------------------------------------------------------------------------

class InstagramClient:
    """Publishes Reels to an Instagram Business account linked to a Facebook Page."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        token_path: str,
        ig_user_id: Optional[str] = None,
        redirect_uri: str = "http://127.0.0.1:9999/",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token_path = Path(token_path)
        self.ig_user_id = ig_user_id
        self.redirect_uri = redirect_uri

    # -- auth --------------------------------------------------------------

    def auth_url(self, state: Optional[str] = None) -> str:
        import urllib.parse

        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "state": state or uuid.uuid4().hex,
            "scope": IG_SCOPE,
            "response_type": "code",
        }
        return f"{IG_OAUTH}/{IG_FB_VERSION}/dialog/oauth?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """Exchange code for a long-lived FB user token; find the linked IG user."""
        resp = requests.post(
            f"{IG_GRAPH}/{IG_FB_VERSION}/oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
            timeout=30,
        )
        body = _http_json(resp, "Instagram code exchange")
        short_token = body["access_token"]

        # Short-lived (1-2h) -> long-lived (60 days).
        resp = requests.post(
            f"{IG_GRAPH}/{IG_FB_VERSION}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=30,
        )
        body = _http_json(resp, "Instagram long-lived token exchange")
        user_token = body["access_token"]

        # Find a Facebook Page the user manages that has a linked IG account.
        resp = requests.get(
            f"{IG_GRAPH}/{IG_FB_VERSION}/me/accounts",
            params={"access_token": user_token},
            timeout=30,
        )
        pages = _http_json(resp, "Instagram pages list").get("data", [])
        ig_id = self.ig_user_id
        page_token = None
        for page in pages:
            p_resp = requests.get(
                f"{IG_GRAPH}/{IG_FB_VERSION}/{page['id']}",
                params={
                    "fields": "instagram_business_account,access_token",
                    "access_token": user_token,
                },
                timeout=30,
            )
            p_body = p_resp.json() if p_resp.status_code < 400 else {}
            if p_body.get("instagram_business_account"):
                ig_id = ig_id or p_body["instagram_business_account"]["id"]
                page_token = p_body.get("access_token", user_token)
                print(
                    f"[instagram] linked to FB page '{page['name']}' "
                    f"(IG user {ig_id})"
                )
                break
        if not ig_id:
            raise RuntimeError(
                "No Instagram Business account found linked to your Facebook "
                "Pages. Set IG to a Business account and link it to a Page first."
            )

        _save_json(self.token_path, {
            "user_token": user_token,
            "page_token": page_token,
            "ig_user_id": ig_id,
            "issued_at": time.time(),
            "expires_in": 60 * 24 * 3600,  # long-lived, ~60 days
        })
        return {"ig_user_id": ig_id}

    def _access_token(self) -> str:
        data = _load_json(self.token_path)
        if not data.get("user_token"):
            raise RuntimeError(
                "No Instagram tokens yet. Run src/auth_instagram.py first."
            )
        # Long-lived tokens cannot be silently refreshed; re-exchange the
        # current (still valid) one via grant_type=fb_exchange_token.
        age = time.time() - data.get("issued_at", 0)
        if age > data.get("expires_in", 0) - 5 * 24 * 3600:
            resp = requests.post(
                f"{IG_GRAPH}/{IG_FB_VERSION}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "fb_exchange_token": data["user_token"],
                },
                timeout=30,
            )
            body = _http_json(resp, "Instagram token refresh")
            data["user_token"] = body["access_token"]
            data["issued_at"] = time.time()
            _save_json(self.token_path, data)
        return data["user_token"]

    # -- posting -----------------------------------------------------------

    def post(self, video_path: str, caption: str, thumb_offset_ms: int = 1000) -> str:
        token = self._access_token()
        ig_id = self.ig_user_id or _load_json(self.token_path)["ig_user_id"]

        # 1. Create resumable upload container ---------------------------------
        resp = requests.post(
            f"{IG_GRAPH}/{IG_API_VERSION}/{ig_id}/media",
            params={
                "access_token": token,
            },
            json={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption[:2200],
                "thumb_offset": thumb_offset_ms,
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        body = _http_json(resp, "Instagram container create")
        container_id = body["id"]
        upload_uri = body.get("uri")
        if not upload_uri:
            raise RuntimeError("Instagram returned no resumable upload uri")

        # 2. Upload video bytes ------------------------------------------------
        size = os.path.getsize(video_path)
        print(f"[instagram] uploading {size} bytes...")
        with open(video_path, "rb") as fh:
            up = requests.post(
                upload_uri,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(size),
                },
                data=fh,
                timeout=900,
            )
        if up.status_code >= 400:
            raise RuntimeError(
                f"Instagram upload failed: HTTP {up.status_code} {up.text[:300]}"
            )
        print("[instagram] upload complete")

        # 3. Poll processing status ---------------------------------------------
        self._wait_processed(container_id, token)

        # 4. Publish ------------------------------------------------------------
        resp = requests.post(
            f"{IG_GRAPH}/{IG_API_VERSION}/{ig_id}/media_publish",
            params={"creation_id": container_id, "access_token": token},
            timeout=30,
        )
        body = _http_json(resp, "Instagram media_publish")
        media_id = body.get("id")
        print(f"[instagram] published, media id {media_id}")
        return media_id

    def _wait_processed(self, container_id: str, token: str, timeout: int = 600) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = requests.get(
                f"{IG_GRAPH}/{IG_API_VERSION}/{container_id}",
                params={
                    "fields": "status_code,video_status",
                    "access_token": token,
                },
                timeout=30,
            )
            body = _http_json(resp, "Instagram container status")
            code = body.get("status_code")
            print(f"[instagram] container status: {code}")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise RuntimeError(f"Instagram processing failed: {body}")
            if code == "EXPIRED":
                raise RuntimeError("Instagram container expired; retry with a new one")
            time.sleep(15)
        raise RuntimeError("Timed out waiting for Instagram processing")
