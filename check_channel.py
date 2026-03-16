import argparse

from core.youtube_auth import get_authenticated_service


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate current YouTube credentials by reading the authenticated channel."
    )
    parser.add_argument(
        "--allow-interactive-auth",
        action="store_true",
        help="Allow browser-based OAuth fallback if saved credentials are invalid.",
    )
    args = parser.parse_args()

    youtube = get_authenticated_service(allow_interactive=args.allow_interactive_auth)
    response = youtube.channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("YouTube auth succeeded, but no authenticated channel was returned.")

    for item in items:
        print("Authenticated Channel:", item["snippet"]["title"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
