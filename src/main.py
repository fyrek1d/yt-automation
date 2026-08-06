import argparse
import json
import os
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pipeline")

from scraper import StoryScraper
from tts import TTS
from editor import VideoEditor
from thumbnail import ThumbnailGenerator
from uploader import YouTubeUploader
from crosspost import TikTokClient, InstagramClient


def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def resolve(base_dir: Path, p: str) -> str:
    p = Path(p)
    if p.is_absolute():
        return str(p)
    return str(base_dir / p)


def main():
    parser = argparse.ArgumentParser(
        description="Faceless Reddit-story + Minecraft parkour video pipeline."
    )
    parser.add_argument(
        "--config", default="config/config.json", help="Path to config.json"
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Render the video but skip uploading (dry run).",
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "unlisted", "private"],
        help="Override video privacy setting.",
    )
    parser.add_argument(
        "--story-file",
        help="Use a specific Reddit story JSON instead of scraping.",
    )
    parser.add_argument(
        "--no-crosspost",
        action="store_true",
        help="Skip TikTok/Instagram cross-posting (YouTube upload still runs).",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    cfg = load_config(resolve(base_dir, args.config))

    paths = cfg["paths"]
    for key in ("gameplay_dir", "audio_dir", "video_dir", "thumbnail_dir"):
        paths[key] = resolve(base_dir, paths[key])
    paths["log_path"] = resolve(base_dir, paths["log_path"])
    paths["client_secret"] = resolve(base_dir, paths["client_secret"])
    paths["token"] = resolve(base_dir, paths["token"])
    for key in (
        "tiktok_credentials",
        "tiktok_token",
        "instagram_credentials",
        "instagram_token",
    ):
        if paths.get(key):
            paths[key] = resolve(base_dir, paths[key])
    if paths.get("reddit_ini"):
        paths["reddit_ini"] = resolve(base_dir, paths["reddit_ini"])

    # 1. Get a story -------------------------------------------------------
    scraper = StoryScraper(
        subreddits=cfg["content"]["subreddits"],
        log_path=paths["log_path"],
        min_words=cfg["content"]["min_words"],
        max_words=cfg["content"]["max_words"],
        reddit_ini=paths.get("reddit_ini"),
        blocked_keywords=cfg["content"].get("blocked_keywords"),
    )

    tts_cfg = cfg["tts"]
    tts = TTS(
        lang=tts_cfg.get("lang", "en"),
        slow=tts_cfg.get("slow", False),
        tld=tts_cfg.get("tld", "com"),
        voice=tts_cfg.get("voice", "JBFqnCBsd6RMkjVDRZzb"),
        rate=tts_cfg.get("rate", "+8%"),
        engine=tts_cfg.get("engine", "elevenlabs"),
        model=tts_cfg.get("model", "eleven_flash_v2_5"),
        speed=tts_cfg.get("speed", 1.0),
        kokoro_voice=tts_cfg.get("kokoro_voice", "am_michael"),
        edge_voice=tts_cfg.get("edge_voice", "en-US-GuyNeural"),
        model_path=paths.get("kokoro_model"),
        voices_path=paths.get("kokoro_voices"),
        elevenlabs_key_path=paths.get("elevenlabs_key"),
        explicit_words=cfg["content"].get("explicit_words"),
    )

    # Narration must land in the Shorts window; otherwise skip the story and
    # try the next one (word-count filtering alone can be fooled by edits).
    dur_min, dur_max = cfg["content"].get("target_seconds", [20, 57])

    if args.story_file:
        with open(args.story_file) as f:
            story = json.load(f)
        audio_path, word_timings = tts.synthesize(story, paths["audio_dir"])
        log.info(f"Using story from file: {story['title']}")
    else:
        candidates = scraper.fetch_stories(limit=cfg["content"]["posts_to_fetch"])
        candidates.sort(key=lambda s: s["score"], reverse=True)
        story = None
        for cand in candidates[:15]:
            audio_path, word_timings = tts.synthesize(cand, paths["audio_dir"])
            duration = word_timings[-1][2] if word_timings else TTS._audio_duration(audio_path)
            log.info(
                f"Tried r/{cand['subreddit']} | {cand['title']} | "
                f"{cand['word_count']} words | {duration:.0f}s"
            )
            if dur_min <= duration <= dur_max:
                story = cand
                break
            os.remove(audio_path)
            log.warning(f"  {duration:.0f}s outside Shorts window, skipping.")
        if story is None:
            raise RuntimeError(
                f"No story produced a {dur_min}-{dur_max}s narration. "
                "Retry later when fresh posts are available."
            )
        log.info(
            f"Picked story r/{story['subreddit']} | {story['title']} | "
            f"{story['word_count']} words | score {story['score']}"
        )

    log.info(f"Narration written: {audio_path}")

    # 3. Render video -------------------------------------------------------
    vcfg = cfg["video"]
    editor = VideoEditor(
        gameplay_dir=paths["gameplay_dir"],
        output_dir=paths["video_dir"],
        resolution=tuple(vcfg["resolution"]),
        fps=vcfg["fps"],
    )
    video_path = editor.render(audio_path, story, word_timings=word_timings)
    log.info(f"Video rendered: {video_path}")

    # Shorts eligibility: portrait 9:16 and under 3 minutes (YouTube auto-
    # classifies vertical shorts-length uploads as Shorts).
    from moviepy import VideoFileClip

    probe = VideoFileClip(video_path, audio=False)
    size = tuple(probe.size)
    portrait = size[1] > size[0]
    probe.close()
    if not portrait:
        raise RuntimeError(
            f"Rendered video is not vertical ({size}); "
            "it will not be posted as a Short. Aborting upload."
        )

    # 4. Thumbnail ----------------------------------------------------------
    thumb = ThumbnailGenerator(output_dir=paths["thumbnail_dir"])
    thumb_path = thumb.create(story)

    # 5. Metadata ------------------------------------------------------------
    meta = cfg["metadata"]
    title = tts.censor_display(
        meta["title_format"].format(title=story["title"])
    )[:100]
    description = meta["description_template"].format(
        url=story.get("url", ""), title=story["title"]
    )

    # 6. Upload --------------------------------------------------------------
    if args.no_upload:
        log.info("DRY RUN: skipping upload.")
        log.info(f"Title: {title}")
        log.info(f"Video: {video_path}")
        log.info(f"Thumbnail: {thumb_path}")
        log.info("Done.")
    else:
        uploader = YouTubeUploader(
            client_secret_path=paths["client_secret"],
            token_path=paths["token"],
        )
        privacy = args.privacy or cfg["channel"]["privacy"]
        video_id = uploader.upload(
            video_path=video_path,
            title=title,
            description=description,
            tags=meta["tags"],
            thumbnail_path=thumb_path,
            category_id=cfg["channel"]["category_id"],
            privacy_status=privacy,
        )
        log.info(f"Published: https://youtu.be/{video_id}")
        scraper.mark_posted(story["id"])
        log.info("Done.")

    # 7. Cross-post to TikTok / Instagram -------------------------------------
    if not args.no_crosspost:
        _crosspost(cfg, paths, video_path, title, story)


def _crosspost(cfg: dict, paths: dict, video_path: str, title: str, story: dict):
    """Publish the rendered Short to TikTok and/or Instagram. Failures are
    logged but never abort the pipeline (YouTube upload already succeeded)."""
    import json

    xp = cfg.get("crosspost") or {}
    caption = xp.get("caption_template", "{title}").format(
        title=title, url=story.get("url", "")
    )[:2200]

    tiktok = xp.get("tiktok") or {}
    if tiktok.get("enabled") and paths.get("tiktok_credentials"):
        try:
            creds = json.loads(open(paths["tiktok_credentials"]).read())
            client = TikTokClient(
                client_key=creds["client_key"],
                client_secret=creds["client_secret"],
                token_path=paths["tiktok_token"],
            )
            status = client.post(
                video_path=video_path,
                title=caption,
                privacy_level=tiktok.get("privacy_level", "SELF_ONLY"),
                is_aigc=tiktok.get("is_aigc", True),
            )
            log.info(f"TikTok post status: {status}")
        except Exception as e:
            log.warning(f"TikTok cross-post failed (YouTube is fine): {e}")

    insta = xp.get("instagram") or {}
    if insta.get("enabled") and paths.get("instagram_credentials"):
        try:
            creds = json.loads(open(paths["instagram_credentials"]).read())
            client = InstagramClient(
                app_id=creds["app_id"],
                app_secret=creds["app_secret"],
                token_path=paths["instagram_token"],
            )
            media_id = client.post(
                video_path=video_path,
                caption=caption,
                thumb_offset_ms=insta.get("thumb_offset_ms", 1000),
            )
            log.info(f"Instagram published, media id {media_id}")
        except Exception as e:
            log.warning(f"Instagram cross-post failed (YouTube is fine): {e}")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        log.error(str(e))
        sys.exit(1)
    except Exception as e:
        log.exception("Pipeline failed")
        sys.exit(1)
