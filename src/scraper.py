"""Reddit story fetching with no API key required.

Source cascade (tries in order):
  1. Reddit RSS feeds  - no credentials, returns full story text.
     Works great from residential IPs. Falls back per-subreddit if the
     combined feed is rate-limited.
  2. PullPush archive   - no credentials, works from any IP (incl. cloud/
     datacenter). Data may lag several months behind.
  3. PRAW (OAuth)       - only if you create a free Reddit "script" app
     and provide a reddit.ini / env vars. Most robust.

A story must be 60-140 words to be a Shorts-length narration (~25-55s).
"""

import html
import json
import os
import random
import re
import configparser
import subprocess
import threading
import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

try:
    import praw
except ImportError:
    praw = None

MIN_WORDS = 60
MAX_WORDS = 140

# Reddit caps unauthenticated traffic at ~10 queries/min per IP. Our scraper
# runs several requests back-to-back (combined feed, per-sub feeds, retries),
# so we self-throttle to stay under the anonymous limit and avoid 429 walls.
MIN_REQUEST_INTERVAL = 6.5
_http_lock = threading.Lock()
_last_request = 0.0


def _pace_request() -> None:
    """Ensure at least MIN_REQUEST_INTERVAL seconds since the last HTTP call."""
    global _last_request
    with _http_lock:
        elapsed = time.monotonic() - _last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        _last_request = time.monotonic()
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
)

# Maximum wall-clock seconds spent trying Reddit before falling back.
REDDIT_ATTEMPT_TIMEOUT = 40


def _clean_html(text: str) -> str:
    # Decode HTML entities FIRST (&lt;div&gt; -> <div>) so the tag regex below
    # can strip Reddit's SC_OFF/markup that arrives HTML-encoded.
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalise_body(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s{2,}", " ", text or "")
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


class StoryScraper:
    """Fetches fresh Reddit stories from a cascade of free sources."""

    def __init__(
        self,
        subreddits: list,
        log_path: str = None,
        min_words: int = MIN_WORDS,
        max_words: int = MAX_WORDS,
        reddit_ini: Optional[str] = None,
        blocked_keywords: list = None,
        profanity_words: list = None,
    ):
        self.subreddits = subreddits
        self.min_words = min_words
        self.max_words = max_words
        self.blocked_keywords = [
            k.lower() for k in (blocked_keywords or [])
        ]
        self.profanity_words = list(profanity_words or [])
        self._profanity_re = None
        self.log_path = log_path
        self._posted = self._load_log()
        self.reddit = self._init_praw(reddit_ini)

    # ---- persistence ---------------------------------------------------
    def _load_log(self) -> set:
        if self.log_path and os.path.exists(self.log_path):
            with open(self.log_path) as f:
                return set(json.load(f))
        return set()

    def _save_log(self):
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "w") as f:
                json.dump(sorted(self._posted), f, indent=2)

    def mark_posted(self, story_id: str):
        self._posted.add(story_id)
        self._save_log()

    # ---- PRAW (optional) ------------------------------------------------
    @staticmethod
    def _init_praw(reddit_ini: Optional[str]):
        if praw is None:
            return None
        if reddit_ini and os.path.exists(reddit_ini):
            try:
                cfg = configparser.ConfigParser()
                cfg.read(reddit_ini)
                section = cfg[cfg.default_section] if cfg.default_section in cfg else cfg[list(cfg.sections())[0]]
                cid = section.get("client_id", "")
                sec = section.get("client_secret", "")
                if cid and sec:
                    return praw.Reddit(
                        client_id=cid,
                        client_secret=sec,
                        user_agent=section.get("user_agent", "story-automator/1.0"),
                    )
            except Exception:
                return None
        cid = os.environ.get("REDDIT_CLIENT_ID")
        sec = os.environ.get("REDDIT_CLIENT_SECRET")
        if cid and sec:
            return praw.Reddit(
                client_id=cid,
                client_secret=sec,
                user_agent=os.environ.get("REDDIT_USER_AGENT", "story-automator/1.0"),
            )
        return None

    # ---- shared filter ---------------------------------------------------
    def _is_blocked(self, combined: str) -> bool:
        """Reject stories that are about gaming/Minecraft rather than real-life
        drama (the gameplay overlay must not clash with the story's topic)."""
        if not self.blocked_keywords:
            return False
        lowered = combined.lower()
        return any(k in lowered for k in self.blocked_keywords)

    _PROMPT_RE = re.compile(
        r"\?\s*$|"
        r"^(whats|what's|what is|what are|what was|what were|what did|what do|what would|"
        r"how did|how do|how would|how are|how is|how was|how to|"
        r"who here|who has|who else|who wants|"
        r"does anyone|do you|have you|did you|are you|is there|has anyone|"
        r"anyone else|redditors|tell me|share your|share the|share a|best of|"
        r"aita|aitah|am i the)",
        re.IGNORECASE,
    )

    def _is_prompt(self, title: str) -> bool:
        """Reject question-style or community-request prompt threads (e.g.
        \"What's the craziest story that ever happened to you?\") which read as
        asks rather than narratives and make for boring narration."""
        return bool(self._PROMPT_RE.search(title))

    def _has_title_profanity(self, title: str) -> bool:
        """True if the title contains an explicit word (whole-word, any case).
        Used to prefer clean titles when picking a story."""
        if not self.profanity_words:
            return False
        if self._profanity_re is None:
            words = sorted(self.profanity_words, key=len, reverse=True)
            pattern = (
                r"(?<![a-zA-Z])("
                + "|".join(re.escape(w) for w in words)
                + r")(?![a-zA-Z])"
            )
            self._profanity_re = re.compile(pattern, re.IGNORECASE)
        return bool(self._profanity_re.search(title))

    def _build_story(
        self, sub: str, post_id: str, title: str, body: str,
        score: int = 0, permalink: str = "",
    ) -> Optional[dict]:
        if not post_id or post_id in self._posted:
            return None
        body = _normalise_body(body)
        combined = f"{title}. {body}".strip()
        if not combined or combined in {"[deleted]", "[removed]", "."}:
            return None
        if self._is_blocked(combined):
            return None
        if self._is_prompt(title):
            return None
        word_count = len(combined.split())
        if not (self.min_words <= word_count <= self.max_words):
            return None
        url = permalink if permalink.startswith("http") else (
            f"https://reddit.com{permalink}" if permalink else ""
        )
        return {
            "id": post_id,
            "subreddit": sub,
            "title": title,
            "content": body or title,
            "full_text": combined,
            "score": score,
            "url": url,
            "word_count": word_count,
        }

    # ---- shared HTTP ----------------------------------------------------
    def _http_get(self, url: str, timeout: int = 30) -> str:
        """Fetch a URL, preferring curl (HTTP/1.1) over Python requests.

        Reddit's bot detection fingerprints Python's TLS stack (JA3) and 429s
        it even from residential IPs, while curl's TLS client-hello passes.
        Retries with backoff on HTTP errors (429/5xx). Falls back to Python
        requests only when curl itself isn't installed."""
        last_err = None
        curl_missing = False
        for attempt in range(3):
            _pace_request()
            try:
                res = subprocess.run(
                    ["curl", "--http1.1", "-sSf", "--compressed",
                     "-m", str(timeout), "-A", BROWSER_UA, url],
                    capture_output=True, text=True, timeout=timeout + 15,
                )
                if res.returncode == 0:
                    return res.stdout
                last_err = RuntimeError(
                    f"curl exit {res.returncode}: {res.stderr[:200].strip()}"
                )
                time.sleep(3 if "429" in res.stderr else 1)
            except FileNotFoundError:
                curl_missing = True
                break  # curl not installed -> use requests below
            except subprocess.SubprocessError as e:
                last_err = e
                time.sleep(1)
        if curl_missing and requests is not None:
            _pace_request()
            resp = requests.get(
                url, headers={"User-Agent": BROWSER_UA}, timeout=timeout
            )
            resp.raise_for_status()
            return resp.text
        raise RuntimeError(f"HTTP fetch failed for {url}: {last_err}")

    # ---- 1. Reddit RSS -----------------------------------------------------
    def _fetch_rss(self, url: str) -> list:
        raw = self._http_get(url)

        stories = []
        for entry in re.findall(r"<entry>(.*?)</entry>", raw, re.S):
            m_link = re.search(r'<link href="([^"]+)"/>', entry)
            link = m_link.group(1) if m_link else ""
            m_id = re.search(r"<id>.*?/comments/([a-z0-9]+)/", entry, re.S)
            post_id = m_id.group(1) if m_id else None
            if not post_id:
                continue
            m_title = re.search(r"<title>(.*?)</title>", entry, re.S)
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", m_title.group(1)) if m_title else ""
            m_content = re.search(r"<content[^>]*>(.*?)</content>", entry, re.S)
            body = (
                _clean_html(m_content.group(1))
                if m_content
                else ""
            )
            m_sub = re.search(r"/r/([^/]+)/", link) or re.search(
                r"/r/([^/]+)/", f"/r/{link}"
            )
            sub = m_sub.group(1) if m_sub else ""
            if not sub:
                continue
            story = self._build_story(sub, post_id, title, body, permalink=link)
            if story:
                stories.append(story)
        return stories

    def _try_reddit(self, limit: int) -> list:
        """Try one combined RSS request; per-sub only if Reddit was reachable."""
        combined = "+".join(self.subreddits)
        url = (
            f"https://www.reddit.com/r/{combined}/top.rss"
            f"?t=week&limit={limit}"
        )
        try:
            stories = self._fetch_rss(url)
            if stories:
                return stories
            # Reddit responded fine but nothing fit the word window; per-sub
            # (smaller limit) can surface different posts. Only do this when
            # Reddit was reachable - a 429 would have raised above.
            print("Combined feed empty; trying per-subreddit feeds...")
        except Exception as e:
            print(f"Combined RSS failed ({e}); skipping per-sub to respect rate limits.")
            return []

        stories = []
        for sub in self.subreddits:
            try:
                stories.extend(
                    self._fetch_rss(
                        f"https://www.reddit.com/r/{sub}/top.rss"
                        f"?t=week&limit={limit // len(self.subreddits) + 3}"
                    )
                )
            except Exception as e:
                print(f"  r/{sub} RSS failed: {e}")
            time.sleep(3)
        return stories

    # ---- 2. PullPush archive -----------------------------------------------
    def _fetch_pullpush(self, sub: str, size: int) -> list:
        url = (
            "https://api.pullpush.io/reddit/search/submission/"
            f"?subreddit={sub}&sort_type=created_utc&sort=desc&size={size}"
        )
        data = json.loads(self._http_get(url)).get("data", [])

        stories = []
        for p in data:
            story = self._build_story(
                sub,
                p.get("id", ""),
                p.get("title", ""),
                p.get("selftext", ""),
                score=p.get("score", 0),
                permalink=p.get("permalink", ""),
            )
            if story:
                stories.append(story)
        return stories

    # ---- 2.5 Arctic Shift archive -------------------------------------------
    # Free Pushshift-style archive on a separate host (not throttled by Reddit's
    # anonymous rate limits). Serves as a reliable fallback when RSS is 429'd.
    def _fetch_arctic(self, sub: str, size: int) -> list:
        url = (
            "https://arctic-shift.photon-reddit.com/api/posts/search"
            f"?subreddit={sub}&sort_type=created_utc&sort=desc&limit={size}"
        )
        data = json.loads(self._http_get(url)).get("data", [])

        stories = []
        for p in data:
            story = self._build_story(
                sub,
                p.get("id", ""),
                p.get("title", ""),
                p.get("selftext", ""),
                score=p.get("score", 0),
                permalink=p.get("permalink", ""),
            )
            if story:
                stories.append(story)
        return stories

    # ---- 3. PRAW -------------------------------------------------------------
    def _fetch_praw(self, sub: str, limit: int) -> list:
        subreddit = self.reddit.subreddit(sub)
        stories = []
        for e in subreddit.top(time_filter="week", limit=limit):
            story = self._build_story(
                sub, e.id, e.title, e.selftext,
                score=e.score, permalink=e.permalink,
            )
            if story:
                stories.append(story)
        return stories

    # ---- public API ----------------------------------------------------------
    def fetch_stories(self, limit: int = 25) -> list:
        stories = []

        # Source 3: PRAW (best quality, only if creds exist)
        if self.reddit is not None:
            try:
                for sub in self.subreddits:
                    stories.extend(self._fetch_praw(sub, limit))
                if stories:
                    return stories
            except Exception as e:
                print(f"PRAW failed ({e}); falling back to free sources.")

        # Source 1: Reddit RSS (no credentials needed)
        stories = self._try_reddit(limit)

        # Source 2: Arctic Shift archive (free, not affected by Reddit rate limits)
        if not stories:
            print("RSS yielded nothing; trying Arctic Shift archive...")
            for sub in self.subreddits:
                try:
                    stories.extend(self._fetch_arctic(sub, 40))
                except Exception as e:
                    print(f"  r/{sub} Arctic Shift failed: {e}")
                if len(stories) >= 10:
                    break
                time.sleep(1)

        # Source 3: PullPush archive (if RSS / Arctic Shift got nothing)
        if not stories:
            print("RSS/Arctic Shift yielded nothing; trying PullPush archive...")
            per_sub = max(limit // len(self.subreddits), 5)
            for sub in self.subreddits:
                try:
                    stories.extend(self._fetch_pullpush(sub, per_sub))
                except Exception as e:
                    print(f"  r/{sub} PullPush failed: {e}; giving up")
                    break
                if stories:
                    break
                time.sleep(1)

        # Deduplicate
        seen, unique = set(), []
        for s in stories:
            if s["id"] not in seen:
                seen.add(s["id"])
                unique.append(s)
        unique.sort(key=lambda s: s["score"], reverse=True)
        return unique


def pick_story(scraper: StoryScraper, limit: int = 25) -> dict:
    """Pick a story: weighted toward high score, but varied."""
    stories = scraper.fetch_stories(limit=limit)
    if not stories:
        raise RuntimeError(
            "No fresh stories found. Check internet access. "
            "If your IP is rate-limited by Reddit, the pipeline retries "
            "via the PullPush archive automatically. "
            "For testing, clear logs/posted.json to allow reuse."
        )
    top = stories[:15]
    # Prefer stories with clean titles; only fall back to profane-title
    # stories if nothing clean is available (the title still gets censored).
    clean = [s for s in top if not scraper._has_title_profanity(s["title"])]
    pool = clean if clean else top
    weights = [max(s["score"], 1) for s in pool]
    total = sum(weights)
    weights = [w / total for w in weights]
    return random.choices(pool, weights=weights, k=1)[0]
