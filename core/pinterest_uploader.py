"""Pinterest pin uploader."""

import os
import re
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from core.meta_auth import get_meta_config
from core.meta_uploader import (
    _ensure_drive_file_public,
    _extract_google_drive_file_id,
    _extract_google_drive_folder_id,
    _find_drive_file_id_by_name,
)
from core.pinterest_auth import get_pinterest_config


PINTEREST_THEME_BOARD_IDS = {
    "faith": "1142366330421125990",
    "neutral": "1142366330421257918",
    "neural": "1142366330421257918",
    "sentimental": "1142366330421257922",
    "love": "1142366330421257919",
}


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sanitize_pinterest_text(text: str) -> str:
    cleaned = re.sub(r"(^|\s)#[A-Za-z0-9_]+", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned


def _normalize_public_video_url(video_url: str) -> str:
    url = (video_url or "").strip()
    if not url:
        return ""
    file_id = _extract_google_drive_file_id(url)
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


def _normalize_pinterest_link(link: str) -> str:
    candidate = (link or "").strip()
    if not candidate:
        return ""

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parse_qs(parsed.query)

    if query.get("alt", [""])[0].lower() == "media":
        return ""
    if host.endswith("drive.google.com") and path == "/uc" and query.get("export", [""])[0].lower() == "download":
        return ""
    if host == "www.googleapis.com" and path.startswith("/drive/v3/files/"):
        return ""
    if path.endswith((".mp4", ".mov", ".m4v", ".avi", ".wmv", ".webm", ".mkv")):
        return ""

    return candidate


def has_pinterest_media_source(data: dict[str, Any], video_path: str = "") -> bool:
    if isinstance(data.get("pinterest_media_source"), dict):
        return True
    if video_path and os.path.isfile(video_path):
        return True
    if resolve_pinterest_media_url(data):
        return True

    video_filename = _first_non_empty(
        os.path.basename(video_path) if video_path else "",
        str(data.get("video", "")).strip(),
    )
    if not video_filename:
        return False

    try:
        meta_config = get_meta_config()
    except Exception:
        return False

    return bool(
        _first_non_empty(
            str(meta_config.get("google_drive_api_key", "")).strip(),
            str(meta_config.get("google_service_account_json", "")).strip(),
        )
    )


def explain_pinterest_readiness(
    data: dict[str, Any],
    *,
    approved: bool,
    already_uploaded: bool,
    video_path: str = "",
) -> str:
    if already_uploaded:
        return "Pinterest already uploaded for this asset."
    if not approved:
        return "Approve this asset first."
    if not resolve_pinterest_board_id(data):
        return "No Pinterest board resolved from theme or override."
    if has_pinterest_media_source(data, video_path=video_path):
        return "Ready to upload."
    return (
        "Missing Pinterest video source. Provide a local video file, `pinterest_media_source`, "
        "or a resolvable public/Drive video URL."
    )


def resolve_pinterest_media_url(
    data: dict[str, Any],
    *,
    video_path: str = "",
) -> str:
    direct_url = _first_non_empty(
        data.get("pinterest_media_url"),
        data.get("public_video_url"),
        data.get("instagram_video_url"),
        data.get("youtube_video_url"),
        data.get("google_drive_link"),
        data.get("google_drive_url"),
    )
    if direct_url:
        return _normalize_public_video_url(direct_url)

    meta_config = get_meta_config()
    drive_folder_url = _first_non_empty(
        data.get("google_drive_folder_url"),
        data.get("google_drive_folder_link"),
        data.get("drive_folder_url"),
        str(meta_config.get("google_drive_folder_url", "")).strip(),
    )
    if not drive_folder_url:
        return ""

    video_filename = _first_non_empty(
        os.path.basename(video_path) if video_path else "",
        str(data.get("video", "")).strip(),
    )
    if not video_filename:
        return ""

    folder_id = _extract_google_drive_folder_id(drive_folder_url)
    if not folder_id:
        raise ValueError("Invalid Google Drive folder URL for Pinterest media lookup.")

    drive_api_key = str(meta_config.get("google_drive_api_key", "")).strip() or None
    drive_service_account_json = str(meta_config.get("google_service_account_json", "")).strip() or None
    drive_auto_make_public = bool(meta_config.get("google_drive_auto_make_public", True))

    file_id = _find_drive_file_id_by_name(
        folder_id=folder_id,
        filename=video_filename,
        api_key=drive_api_key,
        service_account_json=drive_service_account_json,
    )
    if not file_id:
        raise ValueError(
            f"No matching video named `{video_filename}` found in Google Drive folder for Pinterest."
        )

    if drive_auto_make_public and drive_service_account_json:
        _ensure_drive_file_public(file_id, drive_service_account_json)

    if drive_api_key:
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={drive_api_key}"
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def resolve_pinterest_board_id(
    metadata: dict[str, Any] | None = None,
    explicit_board_id: str = "",
) -> str:
    if explicit_board_id.strip():
        return explicit_board_id.strip()

    metadata = metadata or {}
    direct_board_id = _first_non_empty(metadata.get("pinterest_board_id"))
    if direct_board_id:
        return direct_board_id

    theme = _first_non_empty(
        metadata.get("preset"),
        metadata.get("theme"),
        metadata.get("pinterest_theme"),
    ).lower()
    if theme:
        return PINTEREST_THEME_BOARD_IDS.get(theme, "")

    return ""


def _build_media_source(
    config: dict[str, Any],
    *,
    video_path: str,
    media_url: str,
    explicit_media_source: dict[str, Any] | None,
    cover_image_key_frame_time: str | int | float | None = None,
) -> dict[str, Any]:
    if explicit_media_source:
        source = dict(explicit_media_source)
        _ensure_video_media_source_has_cover(
            source,
            default_key_frame_time=cover_image_key_frame_time,
            config=config,
        )
        return source
    upload_path, temp_path = _prepare_pinterest_video_file(video_path, media_url)
    try:
        media_upload = _register_media_upload(config)
        _upload_media_bytes(media_upload, upload_path)
        media_id = _wait_for_media_ready(config, str(media_upload.get("media_id", "")).strip())
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    source = {
        "source_type": "video_id",
        "media_id": media_id,
    }
    _ensure_video_media_source_has_cover(
        source,
        default_key_frame_time=cover_image_key_frame_time,
        config=config,
    )
    return source


def _media_source_has_cover(source: dict[str, Any]) -> bool:
    if _first_non_empty(source.get("cover_image_url")):
        return True
    if source.get("cover_image_key_frame_time") not in (None, ""):
        return True
    return bool(
        _first_non_empty(source.get("cover_image_content_type"))
        and _first_non_empty(source.get("cover_image_data"))
    )


def _coerce_cover_image_key_frame_time(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Pinterest cover_image_key_frame_time must be numeric, not boolean.")
    if isinstance(value, (int, float)):
        numeric = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Pinterest cover_image_key_frame_time: {value!r}"
            ) from exc
    if numeric < 0:
        raise ValueError("Pinterest cover_image_key_frame_time must be >= 0.")
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _ensure_video_media_source_has_cover(
    source: dict[str, Any],
    *,
    default_key_frame_time: str | int | float | None,
    config: dict[str, Any],
) -> None:
    if str(source.get("source_type", "")).strip().lower() != "video_id":
        return
    if _media_source_has_cover(source):
        return

    resolved_key_frame_time = _coerce_cover_image_key_frame_time(
        default_key_frame_time
        if default_key_frame_time not in (None, "")
        else config.get("cover_image_key_frame_time")
    )
    if resolved_key_frame_time is None:
        resolved_key_frame_time = 0
    source["cover_image_key_frame_time"] = resolved_key_frame_time


def _prepare_pinterest_video_file(video_path: str, media_url: str) -> tuple[str, str | None]:
    local_path = (video_path or "").strip()
    if local_path and os.path.isfile(local_path):
        return local_path, None

    resolved_url = (media_url or "").strip()
    if not resolved_url:
        raise ValueError(
            "Pinterest upload requires a local video file, `pinterest_media_source`, "
            "or a resolvable public media URL."
        )

    response = requests.get(resolved_url, stream=True, timeout=300, allow_redirects=True)
    if not response.ok:
        raise RuntimeError(
            f"Pinterest source video URL returned HTTP {response.status_code}: {resolved_url}"
        )

    fd, temp_path = tempfile.mkstemp(prefix="pinterest_", suffix=".mp4")
    os.close(fd)
    try:
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    finally:
        response.close()

    if os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError("Downloaded Pinterest source video is empty.")

    return temp_path, temp_path


def _register_media_upload(config: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{config['api_base_url']}/v5/media",
        headers={
            "Authorization": f"Bearer {config['access_token']}",
            "Content-Type": "application/json",
        },
        json={"media_type": "video"},
        timeout=120,
    )
    if not response.ok:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(
            f"Pinterest media registration failed (HTTP {response.status_code}): {details}"
        )

    data = response.json()
    if not str(data.get("media_id", "")).strip():
        raise RuntimeError(f"Pinterest media registration missing media_id: {data}")
    if not str(data.get("upload_url", "")).strip():
        raise RuntimeError(f"Pinterest media registration missing upload_url: {data}")
    return data


def _upload_media_bytes(media_upload: dict[str, Any], file_path: str) -> None:
    upload_url = str(media_upload.get("upload_url", "")).strip()
    upload_parameters = media_upload.get("upload_parameters", {})
    if not isinstance(upload_parameters, dict):
        raise RuntimeError(f"Pinterest upload_parameters malformed: {media_upload}")

    fields = {key: value for key, value in upload_parameters.items()}

    with open(file_path, "rb") as f:
        response = requests.post(
            upload_url,
            data=fields,
            files={"file": (os.path.basename(file_path), f)},
            timeout=600,
        )

    if not response.ok:
        raise RuntimeError(
            f"Pinterest media upload failed (HTTP {response.status_code}): {response.text}"
        )


def _wait_for_media_ready(
    config: dict[str, Any],
    media_id: str,
    *,
    attempts: int = 40,
    sleep_seconds: float = 3.0,
) -> str:
    if not media_id:
        raise ValueError("Pinterest media upload missing media_id.")

    last_status = ""
    for _ in range(attempts):
        response = requests.get(
            f"{config['api_base_url']}/v5/media/{media_id}",
            headers={"Authorization": f"Bearer {config['access_token']}"},
            timeout=60,
        )
        if not response.ok:
            try:
                details = response.json()
            except ValueError:
                details = response.text
            raise RuntimeError(
                f"Pinterest media status check failed (HTTP {response.status_code}): {details}"
            )

        data = response.json()
        status = str(data.get("status", "")).strip().lower()
        last_status = status
        if status == "succeeded":
            return media_id
        if status == "failed":
            raise RuntimeError(f"Pinterest media processing failed: {data}")
        time.sleep(sleep_seconds)

    raise RuntimeError(
        f"Pinterest media processing did not complete in time for media_id={media_id}. "
        f"Last status: {last_status or 'unknown'}"
    )


def _build_pinterest_pin_payload(
    config: dict[str, Any],
    *,
    title: str,
    description: str = "",
    video_path: str = "",
    media_url: str = "",
    link: str = "",
    alt_text: str = "",
    board_id: str = "",
    board_section_id: str = "",
    media_source: dict[str, Any] | None = None,
    cover_image_key_frame_time: str | int | float | None = None,
) -> dict[str, Any]:
    effective_board_id = _first_non_empty(board_id, config.get("board_id"))
    if not effective_board_id:
        raise ValueError(
            "Pinterest upload requires `board_id` in pinterest_credentials.json "
            "or `pinterest_board_id` in metadata."
        )

    effective_section_id = _first_non_empty(
        board_section_id,
        config.get("board_section_id"),
    )
    payload: dict[str, Any] = {
        "board_id": effective_board_id,
        "title": title.strip(),
        "description": description.strip(),
        "media_source": _build_media_source(
            config,
            video_path=video_path,
            media_url=media_url,
            explicit_media_source=media_source,
            cover_image_key_frame_time=cover_image_key_frame_time,
        ),
    }

    if effective_section_id:
        payload["board_section_id"] = effective_section_id
    normalized_link = _normalize_pinterest_link(link)
    if normalized_link:
        payload["link"] = normalized_link
    if alt_text.strip():
        payload["alt_text"] = alt_text.strip()
    return payload


def upload_pinterest_pin_with_details(
    title: str,
    description: str = "",
    video_path: str = "",
    media_url: str = "",
    link: str = "",
    alt_text: str = "",
    board_id: str = "",
    board_section_id: str = "",
    media_source: dict[str, Any] | None = None,
    cover_image_key_frame_time: str | int | float | None = None,
    api_base_url_override: str = "",
) -> tuple[str, dict[str, Any], str]:
    config = get_pinterest_config(api_base_url_override=api_base_url_override)
    payload = _build_pinterest_pin_payload(
        config,
        title=title,
        description=description,
        video_path=video_path,
        media_url=media_url,
        link=link,
        alt_text=alt_text,
        board_id=board_id,
        board_section_id=board_section_id,
        media_source=media_source,
        cover_image_key_frame_time=cover_image_key_frame_time,
    )

    response = requests.post(
        f"{config['api_base_url']}/v5/pins",
        headers={
            "Authorization": f"Bearer {config['access_token']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    if not response.ok:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(
            f"Pinterest pin creation failed (HTTP {response.status_code}): {details}"
        )

    data = response.json()
    pin_id = data.get("id")
    if not pin_id:
        raise RuntimeError(f"Pinterest response missing pin id: {data}")
    return str(pin_id), payload, str(config["api_base_url"])


def upload_pinterest_pin(
    title: str,
    description: str = "",
    video_path: str = "",
    media_url: str = "",
    link: str = "",
    alt_text: str = "",
    board_id: str = "",
    board_section_id: str = "",
    media_source: dict[str, Any] | None = None,
    cover_image_key_frame_time: str | int | float | None = None,
    api_base_url_override: str = "",
) -> str:
    pin_id, _, _ = upload_pinterest_pin_with_details(
        title=title,
        description=description,
        video_path=video_path,
        media_url=media_url,
        link=link,
        alt_text=alt_text,
        board_id=board_id,
        board_section_id=board_section_id,
        media_source=media_source,
        cover_image_key_frame_time=cover_image_key_frame_time,
        api_base_url_override=api_base_url_override,
    )
    return pin_id
