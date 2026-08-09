import os
import re
import random
from datetime import datetime
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
)


DEFAULT_CAPTION = {
    "font_size": 56,           # base point size, scaled by H/720
    "stroke": 4,               # outline width, scaled by H/720
    "highlight_color": "#FFD600",
    "text_color": "#FFFFFF",
    "y_landscape": 0.60,       # caption baseline fraction (landscape)
    "y_portrait": 0.76,        # caption baseline fraction (portrait)
    "gap_scale": 0.20,         # gap between words as a fraction of font size
    "max_words": 3,            # words per line
    "uppercase": False,        # render captions in ALL CAPS
}


class VideoEditor:
    """Combines TTS narration with Minecraft parkour footage."""

    _SUPPORTED_CACHE = {}

    @classmethod
    def _supported_chars(cls, font_path: str) -> frozenset:
        """Codepoints the caption font can actually draw (cached)."""
        cached = cls._SUPPORTED_CACHE.get(font_path)
        if cached is not None:
            return cached
        supported = frozenset()
        try:
            from fontTools.ttLib import TTFont

            tt = TTFont(font_path)
            cmap = tt.getBestCmap()
            supported = frozenset(
                cp for cp, name in cmap.items() if name != ".notdef"
            )
            tt.close()
        except Exception:
            # Heuristic fallback: DejaVu Sans covers Latin/Greek/Cyrillic and
            # symbols up to U+2FFF; emoji and CJK live well above that.
            supported = frozenset(range(0x20, 0x3000))
        cls._SUPPORTED_CACHE[font_path] = supported
        return supported

    @classmethod
    def _sanitize(cls, word: str, font_path: str) -> str:
        """Drop characters the font cannot render (avoids tofu/blank boxes)."""
        supported = cls._supported_chars(font_path)
        return "".join(
            ch for ch in word if ch == " " or ord(ch) in supported
        )

    @staticmethod
    def _find_font(bold: bool = False) -> str:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
            "Arial-Bold" if bold else "Arial",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return candidates[-1]

    def __init__(
        self,
        gameplay_dir: str,
        output_dir: str,
        resolution: tuple = (1920, 1080),
        fps: int = 30,
        caption: dict = None,
    ):
        self.gameplay_dir = gameplay_dir
        self.output_dir = output_dir
        self.resolution = resolution
        self.fps = fps
        self.caption = dict(DEFAULT_CAPTION)
        if caption:
            self.caption.update(caption)
        os.makedirs(output_dir, exist_ok=True)

    def pick_gameplay(self) -> str:
        clips = [
            os.path.join(self.gameplay_dir, f)
            for f in os.listdir(self.gameplay_dir)
            if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv"))
        ]
        if not clips:
            raise FileNotFoundError(
                "No gameplay videos found in " + self.gameplay_dir
            )
        # Prefer the highest-resolution footage so the vertical crop stays
        # sharp; choose randomly within the best tier for variety.
        sizes = {}
        for c in clips:
            try:
                probe = VideoFileClip(c, audio=False)
                sizes[c] = probe.size[0] * probe.size[1]
                probe.close()
            except Exception:
                sizes[c] = 0
        best = max(sizes.values())
        top = [c for c in clips if sizes[c] == best] or clips
        return random.choice(top)

    @staticmethod
    def clean_text(text: str) -> str:
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        text = re.sub(r"[*_~`#]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _fit(self, clip: VideoFileClip) -> VideoFileClip:
        """Scale and center-crop gameplay footage to fill the output frame
        (turns landscape clips into vertical 9:16 for Shorts)."""
        W, H = self.resolution
        src_w, src_h = clip.size
        if src_w / src_h >= W / H:  # source is wider than target -> crop sides
            scaled = clip.resized(height=H)
            w, _ = scaled.size
            x1 = max(0, (w - W) // 2)
            return scaled.cropped(x1=x1, x2=x1 + W, y1=0, y2=H)
        scaled = clip.resized(width=W)  # source is taller -> crop top/bottom
        _, h = scaled.size
        y1 = max(0, (h - H) // 2)
        return scaled.cropped(x1=0, x2=W, y1=y1, y2=y1 + H)

    def _render_word(
        self, word: str, color: str, font_size: int = None, line_h: int = None
    ) -> Tuple[ImageClip, int, int]:
        """Render one word with PIL into a fixed-height padded RGBA image.

        Returns (clip, width, height). Every word shares the same line height
        AND the same baseline (constant draw y), so captions sit on one line
        and descenders are never clipped. Unsupported glyphs (emoji, etc.) are
        stripped so we never render blank boxes."""
        W, H = self.resolution
        if font_size is None:
            font_size = max(40, int(round(self.caption["font_size"] * H / 720)))
        stroke = max(2, int(round(self.caption["stroke"] * H / 720)))
        hex_color = color.lstrip("#")
        rgb = (
            tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
            if len(hex_color) == 6
            else (255, 255, 255)
        )
        font_path = self._find_font(bold=True)
        word = self._sanitize(word, font_path) or " "
        font = ImageFont.truetype(font_path, font_size)
        tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        bbox = tmp.textbbox((0, 0), word, font=font, stroke_width=stroke)
        ascent, descent = font.getmetrics()
        pad = stroke + 12
        if line_h is None:
            # Cap/ascender ink plus descender ink plus stroke on both edges,
            # plus a small margin: guarantees nothing is ever clipped.
            line_h = (ascent + descent) + 4 * stroke + 8
        w = int((bbox[2] - bbox[0]) + 2 * pad)
        img = Image.new("RGBA", (w, line_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 'la' anchor: baseline sits at draw_y + ascent. Using a constant
        # draw_y keeps every word on the same baseline; x stays left-aligned.
        d.text(
            (pad - bbox[0], pad),
            word,
            font=font,
            fill=rgb + (255,),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 255),
        )
        clip = ImageClip(np.array(img))
        return clip, w, line_h

    def _create_caption_clips(
        self,
        word_timings: List[Tuple[str, float, float]],
    ) -> List:
        """TikTok-style captions at a constant font size.

        Up to 3 words per line at fixed positions; the currently-spoken word
        is yellow, the others white. Lines never resize between each other
        (only a single oversized word may shrink), every word sits on the same
        baseline, and unsupported characters are stripped. A line stays
        visible from its first word to its last, so positions never jump and
        timings match the narration exactly."""
        W, H = self.resolution
        y = int(H * (self.caption["y_landscape"] if H > W else self.caption["y_portrait"]))
        font_size = max(40, int(round(self.caption["font_size"] * H / 720)))
        gap = int(font_size * self.caption["gap_scale"])
        max_words = max(1, int(self.caption["max_words"]))
        uppercase = bool(self.caption["uppercase"])

        # Drop tokens that become empty after sanitization so we never render
        # blank boxes or orphaned gaps.
        font_path = self._find_font(bold=True)
        pairs = []
        for w, s, e in word_timings:
            safe = self._sanitize(w, font_path)
            if safe:
                safe = safe.upper() if uppercase else safe
                pairs.append((safe, s, e))
        n = len(pairs)
        if n == 0:
            return []

        probe = self._render_word("Ag", self.caption["text_color"], font_size)
        line_h = probe[2]
        probe[0].close()

        def render_at(start: int, size: int, fs: int):
            rendered = [
                self._render_word(
                    pairs[start + k][0], self.caption["text_color"], fs, line_h
                )
                for k in range(size)
            ]
            widths = [r[1] for r in rendered]
            total = sum(widths) + gap * (size - 1)
            return rendered, widths, total

        word_clips: List = []
        i = 0
        while i < n:
            size = min(max_words, n - i)
            fs = font_size
            rendered, widths, total = render_at(i, size, fs)
            while total > W and size > 1:
                for r in rendered:
                    r[0].close()
                size -= 1
                rendered, widths, total = render_at(i, size, fs)
            if total > W:  # single oversized word -> shrink just that word
                for r in rendered:
                    r[0].close()
                while total > W and fs > 20:
                    fs = max(20, int(fs * 0.85))
                    rendered, widths, total = render_at(i, 1, fs)

            # Fixed horizontal layout, centered once per chunk.
            total_w = sum(widths) + gap * (size - 1)
            xs = []
            cx = (W - total_w) // 2
            for wpx in widths:
                xs.append(cx)
                cx += wpx + gap

            chunk_start = pairs[i][1]
            chunk_end = pairs[i + size - 1][2]

            for j in range(size):
                ws = pairs[i + j][1]
                we = pairs[i + j][2]
                word = pairs[i + j][0]
                pos = (xs[j], y - line_h // 2)
                used_white = False
                if ws > chunk_start:
                    word_clips.append(
                        rendered[j][0]
                        .with_start(chunk_start)
                        .with_duration(ws - chunk_start)
                        .with_position(pos)
                    )
                    used_white = True
                yellow_img = self._render_word(word, self.caption["highlight_color"], fs, line_h)[0]
                word_clips.append(
                    yellow_img
                    .with_start(ws)
                    .with_duration(max(0.05, we - ws))
                    .with_position(pos)
                )
                if we < chunk_end:
                    word_clips.append(
                        rendered[j][0]
                        .with_start(we)
                        .with_duration(chunk_end - we)
                        .with_position(pos)
                    )
                    used_white = True
                if not used_white:
                    rendered[j][0].close()

            i += size

        return word_clips

    def render(
        self,
        audio_path: str,
        story: dict,
        word_timings: List[Tuple[str, float, float]] = None,
        output_filename: str = None,
    ) -> str:
        if output_filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = re.sub(r"[^\w\-]", "_", story["title"])[:40]
            output_filename = f"{ts}_{safe_title}.mp4"

        out_path = os.path.join(self.output_dir, output_filename)

        audio = AudioFileClip(audio_path)
        duration = audio.duration

        gameplay_path = self.pick_gameplay()
        video = VideoFileClip(gameplay_path, audio=False)
        video = self._fit(video)

        # Loop the clip until it covers the full narration duration
        if video.duration < duration:
            copies = int(duration // video.duration) + 2
            video = concatenate_videoclips([video] * copies)
        video = video.subclipped(0, duration)

        text_clips = self._create_caption_clips(word_timings) if word_timings else []
        final = CompositeVideoClip([video] + text_clips, size=self.resolution)
        final = final.with_audio(audio).with_fps(self.fps)

        final.write_videofile(
            out_path,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="fast",
            logger=None,
        )

        video.close()
        audio.close()
        final.close()
        for tc in text_clips:
            tc.close()

        return out_path
