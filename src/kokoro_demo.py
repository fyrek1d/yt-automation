#!/usr/bin/env python3
"""Render a comparison video of every Kokoro voice.

Each voice speaks the same sample line with its name shown on screen, so you
can pick the voice you like and set it as `kokoro_voice` in config/config.json.

Usage (from the project root):
    .venv/bin/python src/kokoro_demo.py
    .venv/bin/python src/kokoro_demo.py --voices am_michael af_heart bm_george

Output:
    output/videos/kokoro_voice_demo.mp4
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
    "He asked to borrow my car for the weekend, so I said no. "
    "Now the whole family is mad at me... and honestly, I would do it again."
)

ENGLISH_VOICES = [
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
]


def main():
    ap = argparse.ArgumentParser(
        description="Render a Kokoro voice comparison video."
    )
    ap.add_argument(
        "--voices", nargs="*", default=ENGLISH_VOICES,
        help="Kokoro voice ids to include (default: all English voices).",
    )
    ap.add_argument(
        "--out",
        default=str(BASE / "output" / "videos" / "kokoro_voice_demo.mp4"),
        help="Output video path.",
    )
    args = ap.parse_args()

    cfg = json.loads((BASE / "config" / "config.json").read_text())
    paths = cfg["paths"]
    vcfg = cfg["video"]

    tts = TTS(
        engine="kokoro",
        kokoro_voice=args.voices[0],
        model_path=str(BASE / paths["kokoro_model"]),
        voices_path=str(BASE / paths["kokoro_voices"]),
        speed=cfg["tts"].get("speed", 1.0),
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
    print(f"Synthesizing {len(args.voices)} voices...", flush=True)
    segments = []
    for voice in args.voices:
        tts.kokoro_voice = voice
        wav = f"/tmp/kokoro_demo_{voice}.wav"
        _, timings = tts._kokoro_synthesize(spoken, wav)
        segments.append((voice, wav, timings))
        print(f"  {voice}: {len(timings)} words", flush=True)

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
    for (voice, wav, timings), audio in zip(segments, audio_clips):
        dur = audio.duration
        name_img, nw, nh = editor._render_word(voice, "white", font_size=name_font)
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
    print(f"Wrote {args.out} ({total:.0f}s, {len(segments)} voices)")


if __name__ == "__main__":
    main()
