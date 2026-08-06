"""One-time TikTok OAuth: print auth URL, exchange pasted code for tokens.

Usage:
    .venv/bin/python src/auth_tiktok.py
Authorize in the browser, then paste the whole redirect-URL from the address
bar when prompted. Tokens are saved to config/tiktok_tokens.json.

Client key/secret are read from config/tiktok_credentials.json
({"client_key": "...", "client_secret": "..."}) or passed via args.
"""

import argparse
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from crosspost import TikTokClient

BASE = Path(__file__).resolve().parent.parent
CRED_FILE = BASE / "config" / "tiktok_credentials.json"
TOKEN_FILE = BASE / "config" / "tiktok_tokens.json"
REDIRECT = "http://127.0.0.1:9999/"


def main():
    parser = argparse.ArgumentParser(description="One-time TikTok OAuth.")
    parser.add_argument("--client-key", help="TikTok app Client Key")
    parser.add_argument("--client-secret", help="TikTok app Client Secret")
    parser.add_argument(
        "--save-credentials",
        action="store_true",
        help="Persist the key/secret to config/tiktok_credentials.json",
    )
    args = parser.parse_args()

    if args.client_key and args.client_secret:
        creds = {
            "client_key": args.client_key,
            "client_secret": args.client_secret,
        }
        if args.save_credentials:
            CRED_FILE.write_text(__import__("json").dumps(creds, indent=2))
            print(f"Credentials saved -> {CRED_FILE}")
    elif CRED_FILE.exists():
        import json

        creds = json.loads(CRED_FILE.read_text())
    else:
        print(
            "No credentials found. Provide them via --client-key/--client-secret "
            "(add --save-credentials to persist)."
        )
        return 1

    client = TikTokClient(
        client_key=creds["client_key"],
        client_secret=creds["client_secret"],
        token_path=str(TOKEN_FILE),
        redirect_uri=REDIRECT,
    )

    url = client.auth_url()
    webbrowser.open(url, new=1)
    print("Browser opened. Log in with the TikTok account to post from and click Allow.")
    print("If no browser opened, visit:\n")
    print(f"  {url}\n")
    pasted = input("Paste the full redirect URL from the address bar: ").strip()
    if "code=" not in pasted:
        print("No code found in that URL; try again.")
        return 1

    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)["code"][0]
    print("Exchanging code for tokens...")
    client.exchange_code(code)
    print(f"Tokens saved -> {TOKEN_FILE}")
    print("TikTok is now connected. (Public posting still requires the app audit.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
