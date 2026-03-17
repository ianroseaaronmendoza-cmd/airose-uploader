import os
import pickle
import webbrowser
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


class YouTubeAuthError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _is_headless_environment() -> bool:
    ci_markers = ("CI", "GITHUB_ACTIONS")
    if any((os.environ.get(name) or "").strip().lower() == "true" for name in ci_markers):
        return True

    # Linux/macOS browser flows usually need a display server.
    return os.name != "nt" and not (os.environ.get("DISPLAY") or "").strip()


def _load_credentials() -> tuple[Optional[Credentials], Optional[YouTubeAuthError]]:
    if not os.path.exists(TOKEN_PATH):
        return None, YouTubeAuthError(
            "token_missing",
            f"YouTube token file not found at {TOKEN_PATH}.",
        )

    try:
        with open(TOKEN_PATH, "rb") as token:
            creds = pickle.load(token)
    except Exception as exc:
        return None, YouTubeAuthError(
            "token_unreadable",
            f"YouTube token file at {TOKEN_PATH} could not be read: {exc}",
        )

    if not isinstance(creds, Credentials):
        return None, YouTubeAuthError(
            "token_invalid_type",
            f"YouTube token file at {TOKEN_PATH} does not contain Google OAuth credentials.",
        )
    return creds, None


def _save_credentials(creds: Credentials) -> None:
    with open(TOKEN_PATH, "wb") as token:
        pickle.dump(creds, token)


def _refresh_credentials(
    creds: Credentials,
) -> tuple[Optional[Credentials], Optional[YouTubeAuthError]]:
    if not creds.refresh_token:
        return None, YouTubeAuthError(
            "refresh_token_missing",
            "Saved YouTube credentials do not include a refresh token.",
        )

    try:
        creds.refresh(Request())
        _save_credentials(creds)
    except RefreshError as exc:
        return None, YouTubeAuthError(
            "refresh_rejected",
            f"Google rejected the saved YouTube refresh token: {exc}",
        )

    return creds, None


def _run_interactive_oauth_flow() -> Credentials:
    if not os.path.exists(CREDENTIALS_PATH):
        raise YouTubeAuthError(
            "client_credentials_missing",
            f"YouTube credentials.json not found at {CREDENTIALS_PATH}",
        )

    if _is_headless_environment():
        raise YouTubeAuthError(
            "interactive_unavailable",
            "Interactive YouTube OAuth is unavailable in this headless environment. "
            "Refresh token.pkl locally and upload the updated token file to CI secrets."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    # prompt=consent helps ensure Google returns a refresh token when re-authing.
    try:
        return flow.run_local_server(port=0, prompt="consent")
    except webbrowser.Error as exc:
        raise YouTubeAuthError(
            "browser_unavailable",
            "Interactive YouTube OAuth could not open a browser. "
            "Run refresh_youtube_token.py locally, then update token.pkl in CI secrets."
        ) from exc


def get_youtube_credentials(
    force_refresh: bool = False,
    allow_interactive: bool = True,
) -> Credentials:
    creds, load_error = _load_credentials()
    last_error = load_error

    if creds and (force_refresh or creds.expired):
        creds, refresh_error = _refresh_credentials(creds)
        if refresh_error is not None:
            last_error = refresh_error

    if (not creds or not creds.valid) and allow_interactive:
        creds = _run_interactive_oauth_flow()
        _save_credentials(creds)
        last_error = None

    if not creds or not creds.valid:
        if last_error is not None:
            raise last_error
        raise YouTubeAuthError(
            "credentials_invalid",
            "Saved YouTube credentials are present but invalid. If this is CI, refresh "
            "token.pkl locally and update the stored secret; otherwise run "
            "refresh_youtube_token.py to re-authenticate.",
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
