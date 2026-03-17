import argparse
import os
import sys
from datetime import timezone

from core.youtube_auth import TOKEN_PATH, YouTubeAuthError, refresh_saved_youtube_token


def _format_expiry(expiry) -> str:
    if expiry is None:
        return "unknown"

    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    return expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _print_token_file_status() -> None:
    print(f"Token file: {TOKEN_PATH}")
    print(f"Token file exists: {os.path.exists(TOKEN_PATH)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh saved YouTube credentials and report token status."
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Do not fall back to browser-based OAuth if refresh is not possible.",
    )
    args = parser.parse_args()

    try:
        creds = refresh_saved_youtube_token(
            force_refresh=True,
            allow_interactive=not args.no_interactive,
        )
    except YouTubeAuthError as exc:
        print("YouTube token refresh failed.")
        print(f"Reason code: {exc.code}")
        print(f"Reason: {exc.message}")
        print(f"Interactive allowed: {not args.no_interactive}")
        _print_token_file_status()
        return 1

    print("YouTube token refresh successful.")
    print("Reason code: ok")
    _print_token_file_status()
    print(f"Access token expires: {_format_expiry(creds.expiry)}")
    print(f"Refresh token available: {bool(creds.refresh_token)}")

    if not creds.refresh_token:
        print("Warning: refresh token missing. Next expiry will require browser login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
