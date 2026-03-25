"""Fetch Pinterest boards for the configured account and save them locally."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from core.pinterest_auth import get_pinterest_config


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "pinterest_boards.json"


def _normalize_board(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")).strip(),
        "name": str(item.get("name", "")).strip(),
        "description": str(item.get("description", "")).strip(),
        "privacy": str(item.get("privacy", "")).strip(),
    }


def _is_terminal_bookmark_error(status_code: int, details: Any) -> bool:
    if status_code != 404 or not isinstance(details, dict):
        return False
    return str(details.get("message", "")).strip().lower() == "bookmark not found."


def fetch_all_boards(*, api_base_url_override: str = "") -> list[dict[str, Any]]:
    config = get_pinterest_config(api_base_url_override=api_base_url_override)
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {config['access_token']}"})

    boards: list[dict[str, Any]] = []
    bookmark = ""

    while True:
        params: dict[str, Any] = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark

        response = session.get(
            f"{config['api_base_url']}/v5/boards",
            params=params,
            timeout=60,
        )
        if not response.ok:
            try:
                details = response.json()
            except ValueError:
                details = response.text
            if boards and _is_terminal_bookmark_error(response.status_code, details):
                break
            raise RuntimeError(
                f"Pinterest boards fetch failed (HTTP {response.status_code}): {details}"
            )

        data = response.json()
        items = data.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected Pinterest boards response: {data}")

        boards.extend(_normalize_board(item) for item in items if isinstance(item, dict))

        bookmark = str(data.get("bookmark", "")).strip()
        if not bookmark:
            break

    boards.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return boards


def write_board_file(boards: list[dict[str, Any]]) -> Path:
    payload = {
        "count": len(boards),
        "boards": boards,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return OUTPUT_PATH


def main() -> int:
    boards = fetch_all_boards()
    output_path = write_board_file(boards)
    print(f"Saved {len(boards)} boards to {output_path}")
    for board in boards:
        print(f"{board['name']} -> {board['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
