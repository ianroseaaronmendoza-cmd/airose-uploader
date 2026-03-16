import argparse
from datetime import timezone

from core.youtube_auth import TOKEN_PATH, refresh_saved_youtube_token


def _format_expiry(expiry) -> str:
    if expiry is None:
        return "unknown"

    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    return expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh saved YouTube credentials and report token status."
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Do not fall back to browser-based OAuth if refresh is not possible.",
    )
    args = parser.parse_args()

    creds = refresh_saved_youtube_token(
        force_refresh=True,
        allow_interactive=not args.no_interactive,
    )

    print("YouTube token refresh successful.")
    print(f"Token file: {TOKEN_PATH}")
    print(f"Access token expires: {_format_expiry(creds.expiry)}")
    print(f"Refresh token available: {bool(creds.refresh_token)}")

    if not creds.refresh_token:
        print("Warning: refresh token missing. Next expiry will require browser login.")


if __name__ == "__main__":
    main()
