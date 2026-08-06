"""One-time Instagram OAuth (Facebook Login for Business): print auth URL,
exchange pasted code for a long-lived token, and discover the linked
Instagram Business account.

Usage:
    .venv/bin/python src/auth_instagram.py --app-id X --app-secret Y
    (add --save-credentials to persist them)

Authorize in the browser with the Facebook account that administers the
Facebook Page linked to your Instagram Business account, then paste the full
redirect URL. Tokens are saved to config/instagram_tokens.json.
"""

import argparse
import json
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from crosspost import InstagramClient

BASE = Path(__file__).resolve().parent.parent
CRED_FILE = BASE / "config" / "instagram_credentials.json"
TOKEN_FILE = BASE / "config" / "instagram_tokens.json"
REDIRECT = "http://127.0.0.1:9999/"


def main():
    parser = argparse.ArgumentParser(description="One-time Instagram OAuth.")
    parser.add_argument("--app-id", help="Meta app ID")
    parser.add_argument("--app-secret", help="Meta app secret")
    parser.add_argument(
        "--ig-user-id",
        help="Instagram Business account ID (auto-discovered if omitted)",
    )
    parser.add_argument(
        "--save-credentials",
        action="store_true",
        help="Persist app id/secret to config/instagram_credentials.json",
    )
    args = parser.parse_args()

    if args.app_id and args.app_secret:
        creds = {"app_id": args.app_id, "app_secret": args.app_secret}
        if args.save_credentials:
            CRED_FILE.write_text(json.dumps(creds, indent=2))
            print(f"Credentials saved -> {CRED_FILE}")
    elif CRED_FILE.exists():
        creds = json.loads(CRED_FILE.read_text())
    else:
        print(
            "No credentials found. Provide them via --app-id/--app-secret "
            "(add --save-credentials to persist)."
        )
        return 1

    client = InstagramClient(
        app_id=creds["app_id"],
        app_secret=creds["app_secret"],
        token_path=str(TOKEN_FILE),
        ig_user_id=args.ig_user_id,
        redirect_uri=REDIRECT,
    )

    url = client.auth_url()
    webbrowser.open(url, new=1)
    print("Browser opened. Log in with the FACEBOOK account that manages the")
    print("Facebook Page linked to your Instagram Business account, then Allow.")
    print("If no browser opened, visit:\n")
    print(f"  {url}\n")
    pasted = input("Paste the full redirect URL from the address bar: ").strip()
    if "code=" not in pasted:
        print("No code found in that URL; try again.")
        return 1

    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)["code"][0]
    print("Exchanging code and locating your Instagram account...")
    result = client.exchange_code(code)
    print(f"Tokens saved -> {TOKEN_FILE}")
    print(f"Linked Instagram Business account ID: {result['ig_user_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
