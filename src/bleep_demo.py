#!/usr/bin/env python3
"""Render a comparison of censor bleep styles.

Every style bleeps the same profanity out of the same sentence (captions
show the censored word), labeled on screen, so you can pick the bleep you
like and set it as `bleep_style` under [tts] in config/config.json.

Usage (from the project root):
    .venv/bin/python src/bleep_demo.py

Output:
    output/videos/bleep_demo.mp4
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    VideoFileClip,
    concatenate_audioclips,
    concatenate_videoclips,
)

from editor import VideoEditor
from tts import TTS

SAMPLE = (
    "What the fuck did you just say to me, you little shit? "
    "I will end you, I swear to god."
)

STYLES = [
    "dual",    # 1000+1500 Hz sine (current production bleep)
    "tone1k",  # single 1000 Hz tone
    "tone2k",  # single 2000 Hz tone (classic TV censor)
    "low300",  # deep 300 Hz thump
    "noise",   # white noise burst
    "sweep",   # rising 300->2000 Hz sweep
    "osc",     # oscillating/vibrato tone
    "double",  # two quick beeps
]

WORDS = ["fuck", "fucking", "shit", "shitting", "goddamn"]


def main():
    ap = argparse.ArgumentParser(
        description="Render a censor bleep comparison video."
    )
    ap.add_argument(
        "--styles", nargs="*", default=STYLES,
        help="Bleep styles to include (default: all).",
    )
    ap.add_argument(
        "--out",
        default=str(BASE / "output" / "videos" / "bleep_demo.mp4"),
        help="Output video path.",
    )
    args = ap.parse_args()

    cfg = json.loads((BASE / "config" / "config.json").read_text())
    paths = cfg["paths"]
    vcfg = cfg["video"]

    tts = TTS(
        engine="kokoro",
        kokoro_voice=cfg["tts"].get("kokoro_voice", "am_michael"),
        model_path=str(BASE / paths["kokoro_model"]),
        voices_path=str(BASE / paths["kokoro_voices"]),
        speed=cfg["tts"].get("speed", 1.0),
        explicit_words=WORDS,
    )
    tts._load_kokoro()

    editor = VideoEditor(
        gameplay_dir=str(BASE / paths["gameplay_dir"]),
        output_dir=str(BASE / paths["video_dir"]),
        resolution=tuple(vcfg["resolution"]),
        fps=vcfg["fps"],
    )
    W, H = editor.resolution

    spoken = TTS._prep(SAMPLE)
    print(f"Sample: {spoken}")
    print(f"Synthesizing {len(args.styles)} bleep styles...", flush=True)
    segments = []
    for style in args.styles:
        tts.bleep_style = style
        wav = f"/tmp/bleep_demo_{style}.wav"
        _, timings = tts._kokoro_synthesize(spoken, wav)
        segments.append((style, wav, timings))
        print(f"  {style}: {len(timings)} words", flush=True)

    audio_clips = [AudioFileClip(p) for _, p, _ in segments]
    total = sum(c.duration for c in audio_clips)

    bg = VideoFileClip(editor.pick_gameplay(), audio=False)
    bg = editor._fit(bg)
    if bg.duration < total:
        copies = int(total // bg.duration) + 2
        bg = concatenate_videoclips([bg] * copies)
    bg = bg.subclipped(0, total)

    clips = [bg]
    name_font = max(90, int(round(150 * H / 720)))
    cursor = 0.0
    for (style, wav, timings), audio in zip(segments, audio_clips):
        dur = audio.duration
        name_img, nw, nh = editor._render_word(style, "white", font_size=name_font)
        clips.append(
            name_img.with_start(cursor)
            .with_duration(dur)
            .with_position(((W - nw) // 2, int(H * 0.28)))
        )
        for c in editor._create_caption_clips(timings):
            clips.append(c.with_start(c.start + cursor))
        cursor += dur

    final = CompositeVideoClip(clips, size=editor.resolution)
    final = final.with_audio(concatenate_audioclips(audio_clips)).with_fps(editor.fps)
    final.write_videofile(
        args.out,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="fast",
        logger=None,
    )
    for c in clips:
        try:
            c.close()
        except Exception:
            pass
    for a in audio_clips:
        a.close()
    final.close()
    bg.close()
    print(f"Wrote {args.out} ({total:.0f}s, {len(segments)} styles)")


if __name__ == "__main__":
    main()
