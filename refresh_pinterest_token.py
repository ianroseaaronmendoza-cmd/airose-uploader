"""Run an interactive Pinterest OAuth flow and update pinterest_credentials.json."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests


BASE_DIR = Path(__file__).resolve().parent
OAUTH_CREDS_PATH = BASE_DIR / "pinterest_oauth_credentials.json"
PINTEREST_CREDS_PATH = BASE_DIR / "pinterest_credentials.json"
TOKEN_CACHE_PATH = BASE_DIR / "pinterest_oauth_token.json"

AUTH_URL = "https://www.pinterest.com/oauth/"
TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def _load_token_cache() -> dict[str, Any] | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    return _load_json(TOKEN_CACHE_PATH)


def _load_oauth_creds() -> dict[str, Any]:
    if not OAUTH_CREDS_PATH.exists():
        raise FileNotFoundError(
            f"Missing {OAUTH_CREDS_PATH.name}. "
            f"Create it from {OAUTH_CREDS_PATH.with_suffix('.example.json').name}."
        )

    data = _load_json(OAUTH_CREDS_PATH)
    client_id = str(data.get("client_id", "")).strip()
    client_secret = str(data.get("client_secret", "")).strip()
    redirect_uri = str(data.get("redirect_uri", "")).strip()
    scopes = data.get("scopes", [])

    if not client_id or not client_secret or not redirect_uri:
        raise ValueError(
            "pinterest_oauth_credentials.json must include client_id, client_secret, and redirect_uri"
        )
    if not isinstance(scopes, list) or not all(isinstance(item, str) and item.strip() for item in scopes):
        raise ValueError("pinterest_oauth_credentials.json must include a non-empty `scopes` array")

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scopes": [item.strip() for item in scopes],
    }


def _build_auth_url(creds: dict[str, Any], state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": creds["client_id"],
            "redirect_uri": creds["redirect_uri"],
            "scope": ",".join(creds["scopes"]),
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def _wait_for_callback(redirect_uri: str, expected_state: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("redirect_uri must use a local http callback, for example http://localhost:8788/callback")

    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path or "/"
    result: dict[str, str] = {}
    latest_mismatched_state = ""

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request = urlparse(self.path)
            if request.path != path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found.")
                return

            params = parse_qs(request.query)
            state = (params.get("state") or [""])[0]
            code = (params.get("code") or [""])[0]
            error = (params.get("error") or [""])[0]

            if error:
                result["error"] = error
            elif not code:
                result["error"] = "missing_code"
            elif state != expected_state:
                nonlocal latest_mismatched_state
                latest_mismatched_state = state
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                body = (
                    "<html><body><h2>Pinterest authorization not accepted yet.</h2>"
                    "<p>This callback used a stale or unexpected state value. "
                    "Return to Pinterest and complete the latest authorization window.</p>"
                    "</body></html>"
                )
                self.wfile.write(body.encode("utf-8"))
                return
            else:
                result["code"] = code

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in result:
                body = (
                    "<html><body><h2>Pinterest authorization received.</h2>"
                    "<p>You can close this tab and return to Codex.</p></body></html>"
                )
            else:
                body = (
                    "<html><body><h2>Pinterest authorization failed.</h2>"
                    f"<p>{result.get('error', 'Unknown error')}</p></body></html>"
                )
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer((host, port), CallbackHandler)
    deadline = time.monotonic() + 180
    server.timeout = 1
    try:
        while time.monotonic() < deadline and "code" not in result and "error" not in result:
            server.handle_request()
    finally:
        server.server_close()

    if "error" in result:
        raise RuntimeError(f"Pinterest OAuth failed during callback: {result['error']}")
    code = result.get("code", "").strip()
    if not code:
        if latest_mismatched_state:
            raise RuntimeError(
                "Pinterest OAuth callback state did not match the latest auth attempt. "
                "Retry the flow and use the most recently opened Pinterest consent window."
            )
        raise RuntimeError("Pinterest OAuth callback did not include an authorization code.")
    return code


def _exchange_code_for_token(creds: dict[str, Any], code: str) -> dict[str, Any]:
    basic = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode("utf-8")
    ).decode("ascii")
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": creds["redirect_uri"],
        },
        timeout=60,
    )
    if not response.ok:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(
            f"Pinterest token exchange failed (HTTP {response.status_code}): {details}"
        )
    data = response.json()
    if not isinstance(data, dict) or not str(data.get("access_token", "")).strip():
        raise RuntimeError(f"Pinterest token response missing access_token: {data}")
    return data


def _exchange_refresh_token(creds: dict[str, Any], refresh_token: str) -> dict[str, Any]:
    basic = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode("utf-8")
    ).decode("ascii")
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=60,
    )
    if not response.ok:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(
            f"Pinterest token refresh failed (HTTP {response.status_code}): {details}"
        )
    data = response.json()
    if not isinstance(data, dict) or not str(data.get("access_token", "")).strip():
        raise RuntimeError(f"Pinterest refresh response missing access_token: {data}")
    return data


def _update_pinterest_access_token(access_token: str) -> None:
    current: dict[str, Any] = {}
    if PINTEREST_CREDS_PATH.exists():
        current = _load_json(PINTEREST_CREDS_PATH)
    current["access_token"] = access_token.strip()
    with PINTEREST_CREDS_PATH.open("w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)


def _save_token_cache(token_data: dict[str, Any]) -> None:
    with TOKEN_CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)


def _merge_token_data(token_data: dict[str, Any], previous_token_data: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(previous_token_data or {})
    merged.update(token_data)
    if previous_token_data and not str(merged.get("refresh_token", "")).strip():
        merged["refresh_token"] = str(previous_token_data.get("refresh_token", "")).strip()

    refresh_expires_in = merged.get("refresh_token_expires_in")
    if refresh_expires_in not in (None, ""):
        try:
            merged["refresh_token_expires_at"] = int(time.time()) + int(refresh_expires_in)
        except (TypeError, ValueError):
            pass

    access_expires_in = merged.get("expires_in")
    if access_expires_in not in (None, ""):
        try:
            merged["access_token_expires_at"] = int(time.time()) + int(access_expires_in)
        except (TypeError, ValueError):
            pass

    return merged


def _refresh_saved_token_non_interactive() -> dict[str, Any]:
    creds = _load_oauth_creds()
    existing_token_data = _load_token_cache()
    if not existing_token_data:
        raise RuntimeError(
            f"Missing {TOKEN_CACHE_PATH.name}. Run interactive Pinterest OAuth locally first."
        )

    refresh_token = str(existing_token_data.get("refresh_token", "")).strip()
    if not refresh_token:
        raise RuntimeError(
            f"{TOKEN_CACHE_PATH.name} does not include refresh_token. Re-run interactive Pinterest OAuth locally."
        )

    refreshed_token_data = _exchange_refresh_token(creds, refresh_token)
    merged_token_data = _merge_token_data(refreshed_token_data, existing_token_data)
    _save_token_cache(merged_token_data)
    _update_pinterest_access_token(str(merged_token_data["access_token"]))
    return merged_token_data


def refresh_saved_pinterest_token_non_interactive() -> dict[str, Any]:
    return _refresh_saved_token_non_interactive()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh Pinterest credentials interactively or via a saved refresh token."
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Refresh using the saved refresh_token only; do not open a browser.",
    )
    args = parser.parse_args()

    try:
        if args.no_interactive:
            token_data = refresh_saved_pinterest_token_non_interactive()
            print("Pinterest token refresh successful.")
        else:
            creds = _load_oauth_creds()
            state = secrets.token_urlsafe(24)
            auth_url = _build_auth_url(creds, state)

            print("Pinterest re-OAuth starting")
            print(f"Redirect URI: {creds['redirect_uri']}")
            print(f"Scopes: {', '.join(creds['scopes'])}")
            print()
            print("Open this URL if the browser does not launch automatically:")
            print(auth_url)
            print()

            opened = webbrowser.open(auth_url, new=1, autoraise=True)
            if not opened:
                print("Browser auto-open failed. Paste the URL above into your browser.", file=sys.stderr)

            code = _wait_for_callback(creds["redirect_uri"], state)
            token_data = _merge_token_data(
                _exchange_code_for_token(creds, code),
                _load_token_cache(),
            )
            _save_token_cache(token_data)
            _update_pinterest_access_token(str(token_data["access_token"]))
            print("Pinterest token updated successfully.")
    except Exception as exc:
        print("Pinterest token refresh failed.")
        print(f"Reason: {exc}")
        print(f"Interactive allowed: {not args.no_interactive}")
        print(f"OAuth creds file exists: {OAUTH_CREDS_PATH.exists()}")
        print(f"Token cache file exists: {TOKEN_CACHE_PATH.exists()}")
        return 1

    granted_scopes = token_data.get("scope")
    if granted_scopes:
        print(f"Granted scopes: {granted_scopes}")
    print(f"Saved access token to {PINTEREST_CREDS_PATH}")
    print(f"Saved raw token response to {TOKEN_CACHE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
