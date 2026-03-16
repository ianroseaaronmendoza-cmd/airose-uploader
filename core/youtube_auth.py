import os
import pickle
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Required YouTube scopes (upload + read-only for channel info, etc.)
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Explicit token path (project root)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOKEN_PATH = os.path.join(BASE_DIR, "token.pkl")
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")


def _load_credentials() -> Optional[Credentials]:
    if not os.path.exists(TOKEN_PATH):
        return None

    try:
        with open(TOKEN_PATH, "rb") as token:
            creds = pickle.load(token)
    except Exception:
        return None

    if not isinstance(creds, Credentials):
        return None
    return creds


def _save_credentials(creds: Credentials) -> None:
    with open(TOKEN_PATH, "wb") as token:
        pickle.dump(creds, token)


def _refresh_credentials(creds: Credentials) -> Optional[Credentials]:
    if not creds.refresh_token:
        return None

    try:
        creds.refresh(Request())
        _save_credentials(creds)
    except RefreshError:
        return None

    return creds


def _run_interactive_oauth_flow() -> Credentials:
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"YouTube credentials.json not found at {CREDENTIALS_PATH}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    # prompt=consent helps ensure Google returns a refresh token when re-authing.
    return flow.run_local_server(port=0, prompt="consent")


def get_youtube_credentials(
    force_refresh: bool = False,
    allow_interactive: bool = True,
) -> Credentials:
    creds = _load_credentials()

    if creds and (force_refresh or creds.expired):
        creds = _refresh_credentials(creds)

    if (not creds or not creds.valid) and allow_interactive:
        creds = _run_interactive_oauth_flow()
        _save_credentials(creds)

    if not creds or not creds.valid:
        raise RuntimeError(
            "No valid YouTube credentials. Run refresh_youtube_token.py to re-authenticate."
        )

    return creds


def refresh_saved_youtube_token(
    force_refresh: bool = True,
    allow_interactive: bool = True,
) -> Credentials:
    return get_youtube_credentials(
        force_refresh=force_refresh,
        allow_interactive=allow_interactive,
    )


def get_authenticated_service(
    force_refresh: bool = False,
    allow_interactive: bool = True,
):
    creds = get_youtube_credentials(
        force_refresh=force_refresh,
        allow_interactive=allow_interactive,
    )
    return build("youtube", "v3", credentials=creds)
