#!/usr/bin/env python3
"""Self-hosted web dashboard for the Shorts pipeline.

Start it on the server (binds 0.0.0.0 so other Tailnet devices can reach it):

    .venv/bin/python src/dashboard.py --host 0.0.0.0 --port 8080

Security: a random token is generated on first start and stored in
config/dashboard_secret.txt (chmod 600). Every API call and the UI itself
requires it. Override with --token or the DASHBOARD_TOKEN env var.

Endpoints:
    GET  /                     -> dashboard UI
    GET  /api/status           -> running? mode, current run log
    POST /api/run              -> {mode: "upload"|"dry"} start a pipeline run
    GET  /api/config           -> current config.json
    POST /api/config           -> save config.json (raw JSON body)
    GET  /api/options          -> bleep styles, kokoro voices, caption fields
    POST /api/settings         -> update bleep_style / kokoro_voice / caption
    GET  /api/posts            -> posted story ids
    DELETE /api/posts/<id>     -> forget a story id (allows reposting)
    GET  /api/published        -> recent published posts (title, link, time)
    GET  /api/videos           -> rendered videos in output/videos
    GET  /api/video/<name>     -> stream a rendered video
    GET  /api/logs/list        -> available log files
    GET  /api/logs?file=&n=    -> tail of a log file
"""

import argparse
import json
import os
import secrets
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, jsonify, request, send_from_directory, abort, Response

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE / "config" / "config.json"
POSTED_PATH = BASE / "logs" / "posted.json"
POSTS_META_PATH = BASE / "logs" / "posts_meta.json"
VIDEO_DIR = BASE / "output" / "videos"
LOG_DIR = BASE / "logs"
LOCK_PATH = BASE / "logs" / "run.lock"
STATE_PATH = BASE / "logs" / "dashboard_state.json"
SECRET_PATH = BASE / "config" / "dashboard_secret.txt"

app = Flask(__name__, static_folder=None)
app.json.sort_keys = False


# ---- helpers -------------------------------------------------------------

def _read_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _load_token(args_token=None):
    env = os.environ.get("DASHBOARD_TOKEN")
    if env:
        return env
    if args_token:
        return args_token
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    token = secrets.token_urlsafe(32)
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(token)
    os.chmod(SECRET_PATH, 0o600)
    return token


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _cleanup_stale_lock():
    if not LOCK_PATH.exists():
        return
    pid = LOCK_PATH.read_text().strip()
    if pid.isdigit() and not _pid_alive(int(pid)):
        LOCK_PATH.unlink(missing_ok=True)


def _current_run_log():
    state = _read_json(STATE_PATH, None)
    if state and state.get("log"):
        logf = Path(state["log"])
        if logf.exists():
            return logf.name
    logs = sorted(LOG_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0].name if logs else None


def _cron_schedule():
    """Return the cron schedule line for this project (or None)."""
    try:
        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        for line in out.stdout.splitlines():
            if "yt-automation" in line and not line.strip().startswith("#"):
                return line.split(" cd ")[0].strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


# ---- auth ----------------------------------------------------------------

TOKEN = None


def _auth_ok():
    supplied = request.headers.get("Authorization", "")
    return supplied == f"Bearer {TOKEN}" or request.args.get("token") == TOKEN


def require_auth():
    if _auth_ok():
        return
    abort(401)


def require_token_decorator(fn):
    def wrapper(*a, **kw):
        require_auth()
        return fn(*a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


# ---- routes --------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(BASE / "src", "dashboard.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/auth/check")
def auth_check():
    if _auth_ok():
        return jsonify({"ok": True})
    abort(401)


@app.get("/api/status")
@require_token_decorator
def status():
    _cleanup_stale_lock()
    state = _read_json(STATE_PATH, None)
    running = bool(state and _pid_alive(state.get("pid")))
    lock_pid = None
    if LOCK_PATH.exists():
        lock_pid = LOCK_PATH.read_text().strip()
    if running:
        mode = state.get("mode")
        started = state.get("started")
        run_log = Path(state["log"]).name if state.get("log") else None
    else:
        if state:
            STATE_PATH.unlink(missing_ok=True)
        mode = started = run_log = None
    return jsonify({
        "running": running,
        "mode": mode,
        "started": started,
        "run_log": run_log,
        "lock_pid": lock_pid,
        "schedule": _cron_schedule(),
    })


@app.post("/api/run")
@require_token_decorator
def start_run():
    state = _read_json(STATE_PATH, None)
    if state and _pid_alive(state.get("pid")):
        return jsonify({"error": "A pipeline run is already in progress."}), 409
    _cleanup_stale_lock()
    if LOCK_PATH.exists():
        return jsonify({"error": "Pipeline is locked by another process."}), 409

    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "upload")
    if mode not in ("upload", "dry"):
        return jsonify({"error": f"Unknown mode {mode!r}."}), 400

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log = LOG_DIR / f"run_{ts}.log"
    cmd = [sys.executable, "src/main.py"]
    if mode == "dry":
        cmd.append("--no-upload")
    logf = open(run_log, "w")
    proc = subprocess.Popen(
        cmd, cwd=str(BASE), stdout=logf, stderr=subprocess.STDOUT,
    )
    _write_json(STATE_PATH, {
        "pid": proc.pid,
        "mode": mode,
        "started": datetime.now().isoformat(),
        "log": str(run_log),
    })
    return jsonify({"ok": True, "pid": proc.pid, "mode": mode})


@app.get("/api/config")
@require_token_decorator
def get_config():
    return jsonify(_read_json(CONFIG_PATH, {}))


@app.post("/api/config")
@require_token_decorator
def save_config():
    raw = request.get_data(as_text=True)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Invalid JSON: {e}"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Config must be a JSON object."}), 400
    _write_json(CONFIG_PATH, data)
    return jsonify({"ok": True, "saved": True})


BLEEP_STYLES = [
    "dual",
    "tone1k",
    "tone2k",
    "low300",
    "noise",
    "sweep",
    "osc",
    "double",
]

CAPTION_FIELDS = {
    "font_size": {"min": 30, "max": 120, "step": 1, "label": "Font size"},
    "stroke": {"min": 0, "max": 20, "step": 1, "label": "Outline width"},
    "highlight_color": {"label": "Highlight color"},
    "text_color": {"label": "Text color"},
    "y_landscape": {"min": 0.2, "max": 0.9, "step": 0.01, "label": "Caption Y (landscape)"},
    "y_portrait": {"min": 0.2, "max": 0.9, "step": 0.01, "label": "Caption Y (portrait)"},
    "gap_scale": {"min": 0.05, "max": 0.5, "step": 0.01, "label": "Word gap"},
    "max_words": {"min": 1, "max": 6, "step": 1, "label": "Words per line"},
    "uppercase": {"label": "All caps"},
}


def _kokoro_voices():
    """List voice names from the Kokoro voices blob via the config path."""
    cfg = _read_json(CONFIG_PATH, {})
    voices_path = cfg.get("paths", {}).get("kokoro_voices")
    if voices_path and not os.path.isabs(voices_path):
        voices_path = os.path.join(BASE, voices_path)
    if not voices_path or not os.path.exists(voices_path):
        return []
    try:
        import re

        data = open(voices_path, "rb").read()
        names = sorted(
            set(n.decode() for n in re.findall(rb"[a-z]{2}_[a-z]+", data))
        )
        return names
    except (OSError, ValueError):
        return []


@app.get("/api/options")
@require_token_decorator
def get_options():
    cfg = _read_json(CONFIG_PATH, {})
    tts = cfg.get("tts", {})
    caption = cfg.get("caption", {})
    return jsonify({
        "bleep_styles": BLEEP_STYLES,
        "caption_fields": CAPTION_FIELDS,
        "kokoro_voices": _kokoro_voices(),
        "current": {
            "bleep_style": tts.get("bleep_style", "dual"),
            "kokoro_voice": tts.get("kokoro_voice", ""),
            "caption": caption,
        },
    })


@app.post("/api/settings")
@require_token_decorator
def save_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Settings must be a JSON object."}), 400
    cfg = _read_json(CONFIG_PATH, {})
    tts = cfg.setdefault("tts", {})
    allowed_tts = {"bleep_style", "kokoro_voice"}
    for key in allowed_tts & set(body):
        tts[key] = str(body[key])
    if isinstance(body.get("caption"), dict):
        allowed_caption = set(CAPTION_FIELDS)
        new_caption = {k: v for k, v in body["caption"].items() if k in allowed_caption}
        cfg.setdefault("caption", {}).update(new_caption)
    _write_json(CONFIG_PATH, cfg)
    return jsonify({"ok": True, "saved": True})


@app.get("/api/preview/caption")
@require_token_decorator
def preview_caption():
    """Render a static preview of the current caption style as PNG."""
    import io

    from editor import VideoEditor

    cfg = _read_json(CONFIG_PATH, {})
    caption = dict(cfg.get("caption", {}))
    for key, val in request.args.items():
        if key in caption or key in CAPTION_FIELDS:
            if key in ("uppercase",):
                caption[key] = val.lower() in ("1", "true", "yes", "on")
            elif key in ("font_size", "stroke", "max_words"):
                try:
                    caption[key] = int(val)
                except ValueError:
                    pass
            elif key in ("gap_scale", "y_landscape", "y_portrait"):
                try:
                    caption[key] = float(val)
                except ValueError:
                    pass
            else:
                caption[key] = val
    res = cfg.get("video", {}).get("resolution", [1080, 1920])
    W, H = res
    scale = 480 / H
    preview = VideoEditor.render_caption_preview(
        caption, resolution=(int(W * scale), 480)
    )
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(preview).save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.read(), mimetype="image/png")


@app.get("/api/preview/bleep")
@require_token_decorator
def preview_bleep():
    """Return a WAV sample of a given bleep style."""
    import io

    import numpy as np
    import soundfile as sf

    from tts import TTS

    style = request.args.get("style", "dual")
    if style not in BLEEP_STYLES:
        abort(404)
    sr = 22050
    wave = TTS._make_beep(sr, 0.6, style)
    buf = io.BytesIO()
    sf.write(buf, wave, sr, format="WAV")
    buf.seek(0)
    return Response(buf.read(), mimetype="audio/wav")


_TTS_INSTANCE = None


def _get_tts():
    """Lazily built TTS (kokoro) instance reused across voice previews."""
    global _TTS_INSTANCE
    if _TTS_INSTANCE is None:
        from tts import TTS

        cfg = _read_json(CONFIG_PATH, {})
        paths = cfg.get("paths", {})
        _TTS_INSTANCE = TTS(
            engine="kokoro",
            kokoro_voice=cfg.get("tts", {}).get("kokoro_voice", "am_adam"),
            model_path=paths.get("kokoro_model"),
            voices_path=paths.get("kokoro_voices"),
            explicit_words=[],
            speed=cfg.get("tts", {}).get("speed", 1.0),
        )
    return _TTS_INSTANCE


@app.get("/api/preview/voice")
@require_token_decorator
def preview_voice():
    """Return a short WAV sample of a given kokoro voice."""
    import tempfile

    voice = request.args.get("voice", "")
    if voice not in _kokoro_voices():
        abort(404)
    tts = _get_tts()
    tts.kokoro_voice = voice
    sample = "This is a sample of my narration voice for testing."
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        path, _ = tts._kokoro_synthesize(sample, tmp)
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, mimetype="audio/wav")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@app.get("/api/published")
@require_token_decorator
def get_published():
    return jsonify({"records": _read_json(BASE / "logs" / "published.json", [])})


@app.get("/api/posts")
@require_token_decorator
def get_posts():
    posts = _read_json(POSTED_PATH, [])
    meta = _read_json(POSTS_META_PATH, {})
    return jsonify({"posts": posts, "titles": meta})


@app.delete("/api/posts/<story_id>")
@require_token_decorator
def delete_post(story_id):
    posts = _read_json(POSTED_PATH, [])
    if story_id not in posts:
        return jsonify({"error": "Not in posted list."}), 404
    posts = [p for p in posts if p != story_id]
    _write_json(POSTED_PATH, posts)
    return jsonify({"ok": True})


@app.get("/api/videos")
@require_token_decorator
def list_videos():
    if not VIDEO_DIR.exists():
        return jsonify({"videos": []})
    vids = []
    for p in sorted(VIDEO_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        vids.append({
            "name": p.name,
            "size_mb": round(st.st_size / (1024 * 1024), 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return jsonify({"videos": vids})


@app.get("/api/video/<path:name>")
@require_token_decorator
def stream_video(name):
    safe = Path(name).name
    if not (VIDEO_DIR / safe).exists():
        abort(404)
    return send_from_directory(str(VIDEO_DIR), safe)


@app.get("/api/logs/list")
@require_token_decorator
def list_logs():
    files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify({"files": [p.name for p in files]})


@app.get("/api/logs")
@require_token_decorator
def tail_logs():
    name = unquote(request.args.get("file", ""))
    n = int(request.args.get("n", 300))
    if not name:
        name = _current_run_log()
        if not name:
            return jsonify({"lines": [], "file": None})
    safe = Path(name).name
    p = LOG_DIR / safe
    if not p.exists():
        return jsonify({"error": "Log not found."}), 404
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n * 4096))
            data = f.read().decode("utf-8", errors="replace")
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    lines = data.splitlines()[-n:]
    return jsonify({"lines": lines, "file": safe})


def main():
    global TOKEN
    parser = argparse.ArgumentParser(description="Pipeline dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--token", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    TOKEN = _load_token(args.token)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Dashboard token: {TOKEN}", flush=True)
    if args.host == "0.0.0.0":
        print("Listening on all interfaces - the token above protects this.", flush=True)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
