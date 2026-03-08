"""Meta (Facebook/Instagram) credential loader.

This module expects a simple static token setup in `meta_credentials.json`.
Use a Page access token with permissions for Facebook Page video posting and,
if needed, Instagram Content Publishing.
"""

import json
import os
from typing import Any

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CREDS_PATH = os.path.join(BASE_DIR, "meta_credentials.json")


def _load_meta_creds() -> dict[str, Any]:
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"Meta credentials not found at {CREDS_PATH}.\n"
            "Create meta_credentials.json using meta_credentials.example.json."
        )

    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data.get("access_token"):
        raise ValueError("meta_credentials.json must include access_token")

    return data


def get_meta_config() -> dict[str, Any]:
    """Return normalized Meta config."""
    data = _load_meta_creds()
    data.setdefault("graph_api_version", "v23.0")
    return data

