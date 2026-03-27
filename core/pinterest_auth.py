"""Pinterest credential loader."""

import json
import os
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_PATH = os.path.join(BASE_DIR, "pinterest_credentials.json")
DEFAULT_PINTEREST_API_BASE_URL = "https://api.pinterest.com"
PINTEREST_SANDBOX_API_BASE_URL = "https://api-sandbox.pinterest.com"


def _load_pinterest_creds() -> dict[str, Any]:
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"Pinterest credentials not found at {CREDS_PATH}.\n"
            "Create pinterest_credentials.json using pinterest_credentials.example.json."
        )

    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("pinterest_credentials.json must contain a JSON object")
    return data


def get_pinterest_config(*, api_base_url_override: str = "") -> dict[str, Any]:
    data = _load_pinterest_creds()

    access_token = str(data.get("access_token", "")).strip()
    if not access_token:
        raise ValueError("pinterest_credentials.json must include access_token")

    api_base_url = str(
        api_base_url_override or data.get("api_base_url", DEFAULT_PINTEREST_API_BASE_URL)
    ).rstrip("/")

    return {
        "access_token": access_token,
        "board_id": str(data.get("board_id", "")).strip(),
        "board_section_id": str(data.get("board_section_id", "")).strip(),
        "api_base_url": api_base_url,
        "media_source_type": str(data.get("media_source_type", "video_url")).strip() or "video_url",
        "cover_image_key_frame_time": data.get("cover_image_key_frame_time", 0),
    }
