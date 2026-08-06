#!/usr/bin/env python3
"""Download an online profanity list into config/explicit_words.json.

Default source is the LDNOOBW English list (MIT licensed), which keeps the
repo free of profanity/slurs while still censoring them at runtime.

Usage:
    python src/update_wordlist.py                # download default source
    python src/update_wordlist.py --out /tmp/x.json

The pipeline merges this file with any extra words you add to
config.json under "content.explicit_words".
"""

import argparse
import json
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT_URL = (
    "https://raw.githubusercontent.com/LDNOOBW/"
    "List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words/master/en"
)


def fetch_words(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "yt-automation/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    words = []
    for line in text.splitlines():
        w = line.strip()
        if not w or any(c.isspace() for c in w):
            continue  # skip empty lines and multi-word phrases (can't match word tokens)
        words.append(w)
    return sorted(set(words), key=lambda w: (-len(w), w))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL, help="Source text file URL")
    ap.add_argument("--out", default=str(BASE / "config" / "explicit_words.json"))
    args = ap.parse_args()

    words = fetch_words(args.url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(words, indent=2))
    print(f"Wrote {len(words)} words to {out}")


if __name__ == "__main__":
    main()
