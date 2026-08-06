# yt-automation

A fully automated pipeline that creates faceless short-form videos: fetches Reddit stories, narrates them with AI text-to-speech, layers them over Minecraft parkour footage, and uploads them to YouTube (with optional TikTok/Instagram cross-posting).

## Overview

1. **Scrape** trending stories from subreddits (AITA, TIFU, relationships, confession, nosleep...)
2. **Narrate** them with a TTS engine (ElevenLabs primary; Kokoro local model, Edge, and gTTS as automatic fallbacks)
3. **Censor** explicit words (beeped in audio, masked in captions/titles)
4. **Render** the narration over Minecraft parkour footage with TikTok-style word captions (constant-size, baseline-aligned, sync'd to the audio)
5. **Upload** to YouTube automatically, then cross-post to TikTok and Instagram via their official APIs

## Requirements

- Python 3.12+ (tested with 3.14)
- MoviePy (bundles its own ffmpeg via `imageio-ffmpeg`)
- Google Cloud / YouTube Data API v3 credentials for upload
- Minecraft parkour `.mp4` clips in `assets/gameplay/` (not included in this repo)
- (Optional) Kokoro ONNX model files for offline TTS

## Setup

```bash
pip install -r requirements.txt
```

### 1. Assets

- **Gameplay:** drop your own Minecraft parkour clips into `assets/gameplay/` (any resolution/format; the editor center-crops and loops them).
- **Kokoro TTS model** (optional fallback): download `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the Kokoro-82M HuggingFace repo and place them in `assets/kokoro/`. Int8 and fp32 variants both work.

### 2. YouTube API Credentials (required)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable the **YouTube Data API v3**
3. **OAuth consent screen** → set app to *Testing* and add your Gmail under **Test users**
4. **Credentials** → Create Credentials → **OAuth client ID** → Desktop app
5. Download the JSON and save it as `config/client_secret.json`
6. Run `python src/gen_auth.py`, authorize in the browser, then `python src/finish_auth.py "<redirect URL>"` — this saves `config/token.json`

### 3. ElevenLabs TTS (optional but recommended)

Save your API key to `config/elevenlabs_key.txt` (or set `ELEVENLABS_API_KEY`). The pipeline uses it first and falls back to Kokoro/Edge/gTTS automatically.

### 4. Cross-posting (optional)

- **TikTok:** register an app at developers.tiktok.com (Content Posting API product), save key/secret to `config/tiktok_credentials.json`, then run `python src/auth_tiktok.py`. Note: posts stay self-only until the app passes TikTok's audit.
- **Instagram:** needs an Instagram Business account linked to a Facebook Page, a Meta app (Facebook Login for Business), then `python src/auth_instagram.py`.
- Enable each platform under the `crosspost` section of `config/config.json`.

### 5. Configure & run

Edit `config/config.json` (subreddits, voice, caption/upload settings), then:

```bash
python src/main.py            # full pipeline: scrape -> narrate -> render -> upload
python src/main.py --no-upload   # render only (dry run)
```

### 6. Automate (Linux/Mac)

Add to crontab to run daily at 9am:

```bash
0 9 * * * cd /path/to/yt-automation && python3 src/main.py >> logs/cron.log 2>&1
```

## File Structure

```
yt-automation/
├── assets/                    # Not in git: gameplay clips + kokoro models
│   └── gameplay/              # Your Minecraft parkour clips
├── config/
│   ├── config.json            # Channel settings, subreddits, TTS, tags
│   ├── reddit.ini.template    # Optional Reddit API credentials template
│   └── (secrets — gitignored: client_secret.json, token.json, keys)
├── logs/                      # Gitignored: posted.json + run logs
├── output/                    # Gitignored: rendered videos/audio
├── src/
│   ├── main.py                # Pipeline orchestrator
│   ├── scraper.py             # Reddit story fetcher (RSS + PullPush fallback)
│   ├── tts.py                 # TTS engines: elevenlabs/kokoro/edge/gTTS
│   ├── editor.py              # Video editor (moviepy) + captions
│   ├── thumbnail.py           # Thumbnail generator
│   ├── uploader.py            # YouTube upload
│   ├── crosspost.py           # TikTok + Instagram clients
│   ├── auth_tiktok.py         # One-time TikTok OAuth
│   └── auth_instagram.py      # One-time Instagram OAuth
├── requirements.txt
└── README.md
```

## Notes

- Reddit content is user-generated; comply with Reddit's User Agreement and YouTube's reuse policies, and honor removal requests.
- Explicit words are auto-censored (audio beep + masked captions/titles) — configurable in `config/config.json` under `content.explicit_words`.
- The YouTube OAuth scope is limited to `youtube.upload`, so custom thumbnails and video deletion require a broader scope / channel verification.
