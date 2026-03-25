"""TikTok OAuth2 helper using the Content Posting API.

Flow
----
1. First run opens browser → user authorises → code exchanged for tokens.
2. Tokens persisted to tiktok_token.json so subsequent runs skip login.
3. Refresh token used automatically when access token expires.

Prerequisites
-------------
- Register a TikTok Developer App at https://developers.tiktok.com
- Enable the **Content Posting API** scope (video.upload + video.publish)
- Put your client_key and client_secret in  tiktok_credentials.json:
    {
        "client_key": "YOUR_CLIENT_KEY",
        "client_secret": "YOUR_CLIENT_SECRET"
    }
"""

import json
import os
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CREDS_PATH = os.path.join(BASE_DIR, "tiktok_credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "tiktok_token.json")

DEFAULT_REDIRECT_URI = "http://localhost:8585/callback"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

DEFAULT_SCOPES = "video.upload,video.publish"


def _load_client_creds() -> dict:
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"TikTok credentials not found at {CREDS_PATH}.\n"
            "Create tiktok_credentials.json with your client_key and client_secret.\n"
            "See https://developers.tiktok.com for setup instructions."
        )
    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "client_key" not in data or "client_secret" not in data:
        raise ValueError("tiktok_credentials.json must contain client_key and client_secret")
    return data


def get_tiktok_oauth_settings() -> dict:
    creds = _load_client_creds()
    return {
        "client_key": str(creds.get("client_key", "")).strip(),
        "client_secret": str(creds.get("client_secret", "")).strip(),
        "redirect_uri": str(creds.get("redirect_uri", DEFAULT_REDIRECT_URI)).strip() or DEFAULT_REDIRECT_URI,
        "scopes": str(creds.get("scopes", DEFAULT_SCOPES)).strip() or DEFAULT_SCOPES,
        "website_url": str(creds.get("website_url", "")).strip(),
    }


def build_tiktok_auth_url() -> tuple[str, dict]:
    oauth_settings = get_tiktok_oauth_settings()
    params = {
        "client_key": oauth_settings["client_key"],
        "response_type": "code",
        "scope": oauth_settings["scopes"],
        "redirect_uri": oauth_settings["redirect_uri"],
    }
    return f"{AUTH_URL}?{urlencode(params)}", oauth_settings


def wait_for_tiktok_callback(redirect_uri: str) -> str:
    parsed_redirect = urlparse(redirect_uri)
    redirect_host = parsed_redirect.hostname or "127.0.0.1"
    redirect_port = parsed_redirect.port or 8585

    auth_code: dict[str, str | None] = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            auth_code["code"] = qs.get("code", [None])[0]
            auth_code["error"] = qs.get("error", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h3>TikTok authorization complete - you can close this tab.</h3>")

        def log_message(self, *_args):
            pass

    server = HTTPServer((redirect_host, redirect_port), _Handler)
    try:
        server.handle_request()
    finally:
        server.server_close()

    if auth_code.get("error") or not auth_code.get("code"):
        raise RuntimeError(f"TikTok auth failed: {auth_code.get('error', 'no code')}")
    return str(auth_code["code"])


def exchange_tiktok_code_for_token(code: str) -> dict:
    oauth_settings = get_tiktok_oauth_settings()
    response = requests.post(
        TOKEN_URL,
        json={
            "client_key": oauth_settings["client_key"],
            "client_secret": oauth_settings["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": oauth_settings["redirect_uri"],
        },
        timeout=60,
    )
    response.raise_for_status()
    token_data = response.json()
    if "access_token" not in token_data:
        raise RuntimeError(f"Token exchange failed: {token_data}")
    _save_token(token_data)
    return token_data


def _save_token(token_data: dict):
    token_data["_saved_at"] = time.time()
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)


def _load_token() -> dict | None:
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _token_is_valid(token: dict) -> bool:
    saved_at = token.get("_saved_at", 0)
    expires_in = token.get("expires_in", 0)
    return time.time() < saved_at + expires_in - 60  # 60 s buffer


def _refresh_access_token(creds: dict, token: dict) -> dict:
    resp = requests.post(TOKEN_URL, json={
        "client_key": creds["client_key"],
        "client_secret": creds["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    })
    resp.raise_for_status()
    new_token = resp.json()
    if "access_token" not in new_token:
        raise RuntimeError(f"Token refresh failed: {new_token}")
    _save_token(new_token)
    return new_token


def _authorize_via_browser(creds: dict) -> dict:
    """Open browser for user consent, capture redirect, exchange code for token."""
    oauth_settings = get_tiktok_oauth_settings()
    redirect_uri = oauth_settings["redirect_uri"]
    scopes = oauth_settings["scopes"]
    parsed_redirect = urlparse(redirect_uri)
    redirect_host = parsed_redirect.hostname or "127.0.0.1"
    redirect_port = parsed_redirect.port or 8585

    params = {
        "client_key": creds["client_key"],
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": redirect_uri,
    }
    url = f"{AUTH_URL}?{urlencode(params)}"

    # Tiny HTTP server to catch the redirect
    auth_code = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            auth_code["code"] = qs.get("code", [None])[0]
            auth_code["error"] = qs.get("error", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h3>Authorization complete - you can close this tab.</h3>")

        def log_message(self, *_args):
            pass  # silence logs

    server = HTTPServer((redirect_host, redirect_port), _Handler)
    webbrowser.open(url)
    print("Waiting for TikTok authorization in browser…")
    server.handle_request()  # blocks until one request

    if auth_code.get("error") or not auth_code.get("code"):
        raise RuntimeError(f"TikTok auth failed: {auth_code.get('error', 'no code')}")

    # Exchange code for tokens
    resp = requests.post(TOKEN_URL, json={
        "client_key": creds["client_key"],
        "client_secret": creds["client_secret"],
        "code": auth_code["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    resp.raise_for_status()
    token_data = resp.json()
    if "access_token" not in token_data:
        raise RuntimeError(f"Token exchange failed: {token_data}")
    _save_token(token_data)
    return token_data


def get_tiktok_access_token() -> str:
    """Return a valid TikTok access token, prompting login if needed."""
    creds = _load_client_creds()
    token = _load_token()

    if token and _token_is_valid(token):
        return token["access_token"]

    if token and token.get("refresh_token"):
        try:
            token = _refresh_access_token(creds, token)
            return token["access_token"]
        except Exception:
            pass  # fall through to full re-auth

    token = _authorize_via_browser(creds)
    return token["access_token"]
