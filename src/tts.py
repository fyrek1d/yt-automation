import asyncio
import html
import os
import re
import sys
from typing import List, Tuple

import edge_tts
from gtts import gTTS

# Emojis, symbols and pictographs that TTS engines read aloud by name
# ("\U0001F914" -> "thinking"); strip them from spoken text.
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"      # most emoji blocks
    "\U0001F1E6-\U0001F1FF"      # regional indicator flags
    "\u2600-\u27BF"              # misc symbols, dingbats
    "\u2B00-\u2BFF"              # arrows / shapes
    "\u2190-\u21FF"              # arrows
    "\u23E9-\u23FA"              # clock / media controls
    "\u2B50\u2764\u2705\u274C"   # common standalone emoji
    "\uFE0F\uFE0E"               # variation selectors
    "\u20E3"                     # keycap
    "]+",
    re.UNICODE,
)

# Common function words are spoken faster than content words; used to weight
# caption timing estimates so captions track the narration.
_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "so", "for", "nor", "yet", "of",
    "to", "in", "on", "at", "by", "with", "without", "from", "as", "into",
    "up", "out", "off", "over", "under", "than", "i", "you", "he", "she",
    "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "their", "our", "its", "this", "that", "these", "those", "there",
    "here", "is", "are", "was", "were", "be", "been", "being", "am", "do",
    "does", "did", "have", "has", "had", "not", "no", "yes", "will", "would",
    "can", "could", "shall", "should", "may", "might", "must", "just",
    "very", "really", "then", "when", "what", "who", "why", "how", "too",
    "also", "if", "because", "while", "like", "get", "got", "don't",
    "didn't", "doesn't", "isn't", "aren't", "wasn't", "weren't", "it's",
    "i'm", "i've", "i'll", "i'd", "you're", "you've", "you'll", "you'd",
    "that's", "what's", "there's", "let's", "here's", "where's", "who's",
})


class TTS:
    """Narration for Reddit story Shorts.

    Primary engine: ElevenLabs (cloud AI voices). Falls back to Kokoro
    (local AI model), then Edge neural TTS, then gTTS. Word-timings for
    captions come from ElevenLabs alignment or per-clause Kokoro chunks.
    Explicit words are bleeped and captions censored.
    """

    DEFAULT_VOICE = "en-US-GuyNeural"
    DEFAULT_RATE = "+8%"
    ENGINES = ("elevenlabs", "kokoro", "edge")

    def __init__(
        self,
        lang: str = "en",
        slow: bool = False,
        tld: str = "com",
        voice: str = None,
        rate: str = DEFAULT_RATE,
        engine: str = "edge",
        model: str = "eleven_flash_v2_5",
        speed: float = 1.0,
        kokoro_voice: str = "am_michael",
        edge_voice: str = "en-US-GuyNeural",
        model_path: str = None,
        voices_path: str = None,
        elevenlabs_key_path: str = None,
        explicit_words: list = None,
        bleep_style: str = "dual",
    ):
        self.lang = lang
        self.slow = slow
        self.tld = tld
        self.voice = voice or self.DEFAULT_VOICE
        self.rate = rate or self.DEFAULT_RATE
        self.engine = engine
        self.model = model
        self.speed = speed
        self.kokoro_voice = kokoro_voice
        self.edge_voice = edge_voice
        self.model_path = model_path
        self.voices_path = voices_path
        self.elevenlabs_key_path = elevenlabs_key_path
        self._kokoro = None
        self.explicit_words = explicit_words or []
        self.bleep_style = bleep_style or "dual"
        self._regex = None

    @staticmethod
    def _prep(text: str) -> str:
        # Clean for speech: convert markdown, HTML and trim whitespace
        text = html.unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        text = re.sub(r"[*_~`#>]+", "", text)
        # TTS engines read emoji/symbols aloud by name ("\U0001F914" ->
        # "thinking"); drop them from what gets spoken and captioned.
        text = _EMOJI.sub(" ", text)
        # Collapse ellipses to a short pause so no engine vocalizes them.
        text = re.sub(r"\s*…\s*|\s*\.{2,}\s*", ", ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def synthesize(
        self, story: dict, output_dir: str
    ) -> Tuple[str, List[Tuple[str, float, float]]]:
        text = self._prep(story["full_text"])
        os.makedirs(output_dir, exist_ok=True)
        base = os.path.join(output_dir, story["id"])
        spoken = self._censor_spoken(text)

        order = [self.engine] + [
            e for e in self.ENGINES if e != self.engine
        ]
        last_err = None
        for eng in order:
            try:
                if eng == "elevenlabs":
                    return self._elevenlabs_synthesize(text, f"{base}.wav")
                if eng == "kokoro":
                    return self._kokoro_synthesize(text, f"{base}.wav")
                return self._edge_synthesize_wrap(spoken, f"{base}.mp3", text)
            except Exception as e:
                last_err = e
                print(f"{eng} TTS failed ({e}); trying next engine.",
                      file=sys.stderr)

        raise RuntimeError(f"All TTS engines failed: {last_err}")

    def _edge_synthesize_wrap(
        self, spoken: str, out_path: str, text: str
    ) -> Tuple[str, List[Tuple[str, float, float]]]:
        audio, word_events = self._edge_synthesize(spoken)

    def _edge_synthesize_wrap(
        self, spoken: str, out_path: str, text: str
    ) -> Tuple[str, List[Tuple[str, float, float]]]:
        audio, word_events = self._edge_synthesize(spoken)
        with open(out_path, "wb") as f:
            f.write(audio)
        # Edge offsets are in 100ns ticks; divide by 1e7 for seconds.
        timings = [
            (w, o / 1e7, (o + d) / 1e7)
            for (w, o, d) in word_events
        ]
        if timings:
            return out_path, timings
        print("Edge TTS returned no word boundaries; using estimate.",
              file=sys.stderr)
        tts = gTTS(text=spoken, lang=self.lang, slow=self.slow)
        tts.save(out_path)
        duration = self._audio_duration(out_path)
        return out_path, self.estimate_word_timings(text, duration)

    def _elevenlabs_synthesize(
        self, text: str, out_path: str
    ) -> Tuple[str, List[Tuple[str, float, float]]]:
        import base64
        import os as _os

        import soundfile as sf
        from elevenlabs import ElevenLabs

        key = _os.environ.get("ELEVENLABS_API_KEY")
        if not key and self.elevenlabs_key_path and _os.path.exists(
            self.elevenlabs_key_path
        ):
            with open(self.elevenlabs_key_path) as f:
                key = f.read().strip()
        if not key:
            raise RuntimeError("ElevenLabs API key not found")

        client = ElevenLabs(api_key=key)
        resp = client.text_to_speech.convert_with_timestamps(
            voice_id=self.voice,
            text=text,
            model_id=self.model,
            output_format="mp3_44100_128",
        )
        audio_bytes = base64.b64decode(resp.audio_base_64)
        tmp = out_path + ".tmp.mp3"
        with open(tmp, "wb") as f:
            f.write(audio_bytes)
        try:
            from moviepy import AudioFileClip

            ac = AudioFileClip(tmp)
            arr = ac.to_soundarray(fps=44100)
            ac.close()
            sr = 44100
        finally:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        arr = arr.astype("float32")

        duration = len(arr) / sr
        timings = self._elevenlabs_timings(text, resp.alignment, duration)
        if self.explicit_words:
            arr, timings = self._splice_beeps(arr, sr, timings)
        sf.write(out_path, arr, sr)
        return out_path, timings

    @staticmethod
    def _chars_to_words(alignment):
        words, ws, we = [], [], []
        cur, cs, ce = "", None, None
        for ch, s, e in zip(
            alignment.characters,
            alignment.character_start_times_seconds,
            alignment.character_end_times_seconds,
        ):
            if ch.strip() == "":
                if cur:
                    words.append(cur)
                    ws.append(cs)
                    we.append(ce)
                    cur, cs, ce = "", None, None
            else:
                if not cur:
                    cs = s
                cur += ch
                ce = e
        if cur:
            words.append(cur)
            ws.append(cs)
            we.append(ce)
        return words, ws, we

    def _elevenlabs_timings(
        self, text: str, alignment, duration: float
    ) -> List[Tuple[str, float, float]]:
        aligned_words, ws, we = self._chars_to_words(alignment)
        display = re.findall(r"\S+", text)
        if len(aligned_words) == len(display):
            return [
                (d, s, e)
                for d, w, s, e in zip(display, aligned_words, ws, we)
            ]
        # Model normalized some tokens (numbers, symbols); keep the model's
        # spoken words so timings stay exactly aligned with the audio.
        return [
            (w, s, e) for w, s, e in zip(aligned_words, ws, we)
        ]

    def _edge_synthesize(
        self,
        text: str,
    ) -> Tuple[bytes, List[Tuple[str, int, int]]]:
        async def _run():
            communicate = edge_tts.Communicate(
                text, self.edge_voice, rate=self.rate, boundary="WordBoundary"
            )
            audio = b""
            words: List[Tuple[str, int, int]] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    words.append(
                        (chunk["text"], chunk["offset"], chunk["duration"])
                    )
            return audio, words

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    def _load_kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            model = self.model_path or "assets/kokoro/kokoro-v1.0.onnx"
            voices = self.voices_path or "assets/kokoro/voices-v1.0.bin"
            self._kokoro = Kokoro(model, voices)
        return self._kokoro

    def _kokoro_synthesize(
        self, text: str, out_path: str
    ) -> Tuple[str, List[Tuple[str, float, float]]]:
        import asyncio

        import numpy as np
        import soundfile as sf

        kokoro = self._load_kokoro()
        chunks = []

        async def _run():
            async for audio_part, sr in kokoro.create_stream(
                text, voice=self.kokoro_voice, speed=self.speed
            ):
                chunks.append((audio_part, sr))

        asyncio.run(_run())
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        sr = chunks[0][1]
        full = np.concatenate([a for a, _ in chunks])
        sf.write(out_path, full, sr)

        # Kokoro streams clause-by-clause (splits at sentence punctuation), so
        # distribute each clause's audio across its words for accurate captions.
        clauses = [
            c.strip()
            for c in re.findall(r"[^.!?;]+[.!?;]?", text)
            if c.strip()
        ]
        if len(clauses) == len(chunks):
            timings = []
            t = 0.0
            for clause, (audio_part, _) in zip(clauses, chunks):
                clause_dur = len(audio_part) / sr
                for w, s, e in self.estimate_word_timings(clause, clause_dur):
                    timings.append((w, s + t, e + t))
                t += clause_dur
        else:
            duration = len(full) / sr
            timings = self.estimate_word_timings(text, duration)

        if self.explicit_words:
            full, timings = self._splice_beeps(full, sr, timings)
        sf.write(out_path, full, sr)
        return out_path, timings

    def _explicit_regex(self):
        if self._regex is None:
            words = sorted(self.explicit_words, key=len, reverse=True)
            pattern = (
                r"(?<![a-zA-Z])("
                + "|".join(re.escape(w) for w in words)
                + r")(?![a-zA-Z])"
            )
            self._regex = re.compile(pattern, re.IGNORECASE)
        return self._regex

    def _censor_spoken(self, text: str) -> str:
        if not self.explicit_words:
            return text
        return self._explicit_regex().sub("bleep", text)

    def censor_display(self, text: str) -> str:
        if not self.explicit_words:
            return text
        return self._explicit_regex().sub(
            lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text
        )

    @staticmethod
    def _make_beep(
        sr: int, duration: float = 0.45, style: str = "dual"
    ) -> "np.ndarray":
        import numpy as np

        n = max(1, int(sr * duration))
        t = np.arange(n) / sr
        if style == "tone1k":
            wave = np.sin(2 * np.pi * 1000.0 * t)
        elif style == "tone2k":
            wave = np.sin(2 * np.pi * 2000.0 * t)
        elif style == "low300":
            wave = np.sin(2 * np.pi * 300.0 * t)
        elif style == "noise":
            wave = np.random.uniform(-1, 1, n)
        elif style == "sweep":
            wave = np.sin(2 * np.pi * np.cumsum(np.linspace(300.0, 2000.0, n)) / sr)
        elif style == "osc":
            wave = np.sin(
                2 * np.pi * (1200.0 + 500.0 * np.sin(2 * np.pi * 8.0 * t)) * t
            )
        elif style == "double":
            beep_n = max(1, n // 2 - int(0.03 * sr))
            b1 = np.sin(2 * np.pi * 1000.0 * t[:beep_n])
            b2 = np.sin(2 * np.pi * 1000.0 * t[-beep_n:])
            wave = np.concatenate([b1, np.zeros(n - 2 * beep_n), b2])
        else:  # "dual" - the current production beep
            wave = 0.5 * np.sin(2 * np.pi * 1000.0 * t) + 0.5 * np.sin(
                2 * np.pi * 1500.0 * t
            )
        fade = max(1, int(0.03 * sr))
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        return (wave * env * 0.7).astype(np.float32)

    def _splice_beeps(
        self, full, sr: int, timings
    ) -> Tuple["np.ndarray", list]:
        """Replace explicit words with a bleep tone and censor their captions.

        Beeps replace exactly the spoken word (never overwriting the next
        word's audio), and any extra beep length is inserted rather than
        overwritten, so subsequent caption times stay in sync with the final
        audio."""
        import numpy as np

        regex = self._explicit_regex()
        segs = []
        out = []
        cursor = 0.0  # position in the original audio (seconds)
        offset = 0.0  # how much the audio has grown after this point
        for w, s, e in timings:
            if s > cursor:
                segs.append(full[int(cursor * sr): int(s * sr)])
            real_start = s + offset
            if regex.search(w):
                beep_dur = max(0.28, (e - s) + 0.02)
                segs.append(
                    self._make_beep(sr, beep_dur, self.bleep_style)
                )
                out.append(
                    (self.censor_display(w), real_start, real_start + beep_dur)
                )
                offset += beep_dur - (e - s)
            else:
                segs.append(full[int(s * sr): int(e * sr)])
                out.append((w, real_start, real_start + (e - s)))
            cursor = e
        if cursor * sr < len(full):
            segs.append(full[int(cursor * sr):])
        return np.concatenate(segs), out

    @staticmethod
    def _audio_duration(path: str) -> float:
        from moviepy import AudioFileClip

        clip = AudioFileClip(path)
        try:
            return float(clip.duration)
        finally:
            clip.close()

    @staticmethod
    def estimate_word_timings(
        text: str, duration: float
    ) -> List[Tuple[str, float, float]]:
        """Estimate per-word speech times from text.

        Content words are paced slower than function words and punctuation
        adds a pause; the result is scaled to the actual audio duration so
        captions track the narration more closely than a length-proportional
        split."""
        words = re.findall(r"\S+", text)
        if not words:
            return []
        durations = []
        for w in words:
            key = re.sub(r"[^a-zA-Z']", "", w).lower()
            chars = max(1, len(key))
            d = chars * (0.050 if key in _FUNCTION_WORDS else 0.085) + 0.02
            last = w[-1]
            if last in ".!?":
                d += 0.30
            elif last in ",;:":
                d += 0.14
            durations.append(max(0.10, d))
        total = sum(durations)
        scale = duration / total if total else 1.0
        timings = []
        t = 0.0
        for w, d in zip(words, durations):
            dd = d * scale
            timings.append((w, t, t + dd))
            t += dd
        return timings
