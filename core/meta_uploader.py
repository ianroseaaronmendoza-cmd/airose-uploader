"""Facebook + Instagram uploader via Meta Graph API.

Facebook supports direct file upload from local disk.
Instagram Reels requires a publicly reachable `video_url`.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
try:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleAuthRequest
except Exception:  # pragma: no cover - optional at runtime
    service_account = None
    GoogleAuthRequest = None

from core.meta_auth import get_meta_config


_HASHTAG_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "if", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "this", "to", "was", "we", "with", "you", "your",
    "about", "after", "before", "can", "could", "did", "does", "had", "has",
    "have", "just", "make", "more", "over", "than", "then", "they", "them",
    "what", "when", "where", "which", "who", "why", "will",
    "guide", "video", "videos", "short", "shorts", "reel", "reels", "post", "posts",
}


def _tokenize_hashtag_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def _is_meaningful_hashtag_word(word: str) -> bool:
    return (
        len(word) >= 3
        and not word.isdigit()
        and word not in _HASHTAG_STOP_WORDS
    )


def _format_hashtag_phrase(words: tuple[str, ...]) -> str:
    if not words or not all(_is_meaningful_hashtag_word(word) for word in words):
        return ""
    body = "".join(word.capitalize() for word in words)
    if not body or body[0].isdigit() or len(body) > 40:
        return ""
    return f"#{body}"


def _iter_hashtag_phrase_candidates(text: str) -> list[tuple[str, tuple[str, ...]]]:
    tokens = _tokenize_hashtag_words(text)
    candidates: list[tuple[str, tuple[str, ...]]] = []

    for size in (3, 2):
        if len(tokens) < size:
            continue
        for idx in range(len(tokens) - size + 1):
            phrase = tuple(tokens[idx:idx + size])
            tag = _format_hashtag_phrase(phrase)
            if tag:
                candidates.append((tag, phrase))

    return candidates


def _iter_hashtag_single_candidates(text: str) -> list[tuple[str, tuple[str, ...]]]:
    tokens = _tokenize_hashtag_words(text)
    candidates: list[tuple[str, tuple[str, ...]]] = []

    for token in tokens:
        tag = _format_hashtag_phrase((token,))
        if tag:
            candidates.append((tag, (token,)))

    return candidates


def _is_subphrase(phrase: tuple[str, ...], full_phrase: tuple[str, ...]) -> bool:
    if len(phrase) >= len(full_phrase):
        return False
    for idx in range(len(full_phrase) - len(phrase) + 1):
        if full_phrase[idx:idx + len(phrase)] == phrase:
            return True
    return False


def _extract_google_drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" not in host:
        return None

    # /file/d/<FILE_ID>/...
    file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", path)
    if file_match:
        return file_match.group(1)

    # /open?id=<FILE_ID> or /uc?id=<FILE_ID>
    qs = parse_qs(parsed.query)
    file_id = qs.get("id", [None])[0]
    if file_id:
        return file_id

    return None


def _extract_google_drive_file_id_from_direct_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "googleapis.com" in host:
        # /drive/v3/files/{FILE_ID}
        m = re.search(r"/drive/v3/files/([a-zA-Z0-9_-]+)", path)
        if m:
            return m.group(1)

    if "drive.google.com" in host or "drive.usercontent.google.com" in host:
        qs = parse_qs(parsed.query)
        file_id = qs.get("id", [None])[0]
        if file_id:
            return file_id

    return None


def _extract_google_drive_folder_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" not in host:
        return None

    folder_match = re.search(r"/drive/folders/([a-zA-Z0-9_-]+)", path)
    if folder_match:
        return folder_match.group(1)

    return None


def _normalize_instagram_video_url(video_url: str) -> str:
    url = (video_url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" in host and "/drive/folders/" in path:
        raise ValueError(
            "Instagram requires a direct public video URL, not a Google Drive folder link. "
            "Use a file link (`/file/d/...`) in `instagram_video_url` or `public_video_url`."
        )

    file_id = _extract_google_drive_file_id(url)
    if file_id:
        # Direct download form Meta can fetch.
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    return url


def _assert_public_video_url_fetchable(video_url: str) -> dict[str, str]:
    try:
        resp = requests.get(video_url, stream=True, timeout=60, allow_redirects=True)
    except requests.RequestException as exc:
        raise ValueError(f"Instagram video URL is not reachable: {exc}") from exc

    if not resp.ok:
        raise ValueError(
            f"Instagram video URL returned HTTP {resp.status_code}; must be publicly downloadable."
        )

    content_type = (resp.headers.get("Content-Type") or "").lower()
    final_url = resp.url
    content_length = str(resp.headers.get("Content-Length") or "")
    sample = b""
    try:
        for chunk in resp.iter_content(chunk_size=256):
            if chunk:
                sample = chunk[:256]
                break
    finally:
        resp.close()
    sample_text = sample.decode("utf-8", errors="ignore").strip().lower()
    looks_like_html = sample_text.startswith("<!doctype html") or sample_text.startswith("<html")
    if "text/html" in content_type:
        raise ValueError(
            "Instagram video URL resolved to HTML instead of a video stream. "
            f"Final URL: {final_url}"
        )
    if looks_like_html:
        raise ValueError(
            "Instagram video URL body looks like HTML instead of raw video bytes. "
            f"Final URL: {final_url}"
        )
    if "video/" not in content_type and "application/octet-stream" not in content_type:
        raise ValueError(
            f"Instagram video URL content-type is `{content_type}`; expected video/*."
        )
    return {
        "final_url": final_url,
        "content_type": content_type,
        "content_length": content_length,
    }


def _find_drive_file_id_by_name(
    folder_id: str,
    filename: str,
    api_key: str | None,
    service_account_json: str | None,
) -> str | None:
    endpoint = "https://www.googleapis.com/drive/v3/files"
    safe_filename = filename.replace("'", "\\'")
    q = (
        f"'{folder_id}' in parents and trashed=false and "
        f"name='{safe_filename}' and mimeType contains 'video/'"
    )
    params: dict[str, str] = {
        "q": q,
        "fields": "files(id,name,mimeType)",
        "pageSize": 10,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    headers: dict[str, str] = {}
    if api_key:
        params["key"] = api_key
    elif service_account_json:
        token = _get_service_account_access_token(service_account_json)
        headers["Authorization"] = f"Bearer {token}"
    else:
        raise ValueError(
            "Provide `google_drive_api_key` or `google_service_account_json` in meta_credentials.json."
        )

    resp = requests.get(endpoint, params=params, headers=headers, timeout=60)
    if not resp.ok:
        _raise_graph_error(resp, "Google Drive file lookup")
    data = resp.json()
    files = data.get("files", [])
    if files:
        file_id = files[0].get("id")
        if isinstance(file_id, str) and file_id:
            return file_id
    return None


def _get_service_account_access_token(service_account_json: str) -> str:
    if service_account is None or GoogleAuthRequest is None:
        raise RuntimeError(
            "google-auth is not available. Install dependencies for service-account Drive access."
        )
    key_path = service_account_json
    if not os.path.isabs(key_path):
        key_path = os.path.abspath(key_path)
    if not os.path.isfile(key_path):
        raise FileNotFoundError(f"Google service account key not found: {key_path}")

    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(GoogleAuthRequest())
    if not creds.token:
        raise RuntimeError("Failed to obtain service-account access token for Google Drive.")
    return str(creds.token)


def _ensure_drive_file_public(file_id: str, service_account_json: str) -> None:
    token = _get_service_account_access_token(service_account_json)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    headers = {"Authorization": f"Bearer {token}"}

    list_resp = requests.get(
        endpoint,
        params={
            "supportsAllDrives": "true",
            "fields": "permissions(type,role)",
        },
        headers=headers,
        timeout=60,
    )
    if not list_resp.ok:
        _raise_graph_error(list_resp, "Google Drive permissions list")

    perms = list_resp.json().get("permissions", [])
    already_public = any(
        p.get("type") == "anyone" and p.get("role") in {"reader", "writer", "commenter"}
        for p in perms
        if isinstance(p, dict)
    )
    if already_public:
        return

    create_resp = requests.post(
        endpoint,
        params={"supportsAllDrives": "true"},
        headers={**headers, "Content-Type": "application/json"},
        json={"type": "anyone", "role": "reader"},
        timeout=60,
    )
    if not create_resp.ok:
        _raise_graph_error(create_resp, "Google Drive make-public permission create")


def _build_drive_download_url(file_id: str, drive_api_key: str | None) -> str:
    if drive_api_key:
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={drive_api_key}"
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _upload_file_to_drive_folder(
    file_path: str,
    folder_id: str,
    drive_service_account_json: str,
    upload_name: str,
) -> str:
    token = _get_service_account_access_token(drive_service_account_json)
    endpoint = "https://www.googleapis.com/upload/drive/v3/files"
    metadata = {"name": upload_name, "parents": [folder_id]}
    headers = {"Authorization": f"Bearer {token}"}
    with open(file_path, "rb") as f:
        files = {
            "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (upload_name, f, "video/mp4"),
        }
        resp = requests.post(
            endpoint,
            params={"uploadType": "multipart", "supportsAllDrives": "true"},
            headers=headers,
            files=files,
            timeout=600,
        )
    if not resp.ok:
        _raise_graph_error(resp, "Google Drive upload")
    data = resp.json()
    file_id = data.get("id")
    if not file_id:
        raise RuntimeError(f"Google Drive upload response missing file id: {data}")
    return str(file_id)


def _update_drive_file_content(
    file_id: str,
    file_path: str,
    drive_service_account_json: str,
) -> None:
    token = _get_service_account_access_token(drive_service_account_json)
    endpoint = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
    }
    with open(file_path, "rb") as f:
        resp = requests.patch(
            endpoint,
            params={"uploadType": "media", "supportsAllDrives": "true"},
            headers=headers,
            data=f,
            timeout=600,
        )
    if not resp.ok:
        _raise_graph_error(resp, "Google Drive file content update")


def _transcode_video_for_instagram(input_path: str) -> str:
    ffmpeg_exe = os.environ.get("FFMPEG_PATH", "ffmpeg")
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(
        tempfile.gettempdir(),
        f"{base}_igsafe_{uuid.uuid4().hex[:8]}.mp4",
    )
    has_audio = _input_video_has_audio(input_path)
    cmd = [ffmpeg_exe, "-y", "-i", input_path]
    if not has_audio:
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])

    cmd.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0" if has_audio else "1:a:0",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-r",
            "30",
            "-vsync",
            "cfr",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-preset",
            "medium",
            "-b:v",
            "2500k",
            "-maxrate",
            "3000k",
            "-bufsize",
            "5000k",
            "-g",
            "60",
            "-keyint_min",
            "60",
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "128k",
            "-shortest",
            output_path,
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "FFmpeg transcode failed for Instagram fallback. "
            f"stderr: {proc.stderr[-1200:]}"
        )
    if not os.path.isfile(output_path):
        raise RuntimeError("FFmpeg transcode did not produce an output file.")
    return output_path


def _input_video_has_audio(input_path: str) -> bool:
    ffprobe_exe = os.environ.get("FFPROBE_PATH", "ffprobe")
    cmd = [
        ffprobe_exe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        input_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False
    streams = data.get("streams", [])
    return bool(streams)


def _add_cache_buster(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    # Signed CDN URLs (e.g., Facebook CDN) can break if query params are modified.
    if host.endswith("fbcdn.net") or "scontent." in host:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}cb={int(time.time())}"


def _resolve_instagram_video_url(
    instagram_video_url: str | None,
    drive_folder_url: str | None,
    video_filename: str,
    drive_api_key: str | None,
    drive_service_account_json: str | None,
    drive_auto_make_public: bool,
) -> str:
    if instagram_video_url:
        return _normalize_instagram_video_url(instagram_video_url)

    folder_url = (drive_folder_url or "").strip()
    if not folder_url:
        raise ValueError(
            "Instagram posting needs a public video URL. "
            "Set `instagram_video_url`/`public_video_url` or `google_drive_folder_url`."
        )

    folder_id = _extract_google_drive_folder_id(folder_url)
    if not folder_id:
        raise ValueError("Invalid Google Drive folder URL for Instagram lookup.")

    file_id = _find_drive_file_id_by_name(
        folder_id=folder_id,
        filename=video_filename,
        api_key=drive_api_key,
        service_account_json=drive_service_account_json,
    )
    if not file_id:
        raise ValueError(
            f"No matching video named `{video_filename}` found in Google Drive folder."
        )

    if drive_auto_make_public and drive_service_account_json:
        _ensure_drive_file_public(file_id, drive_service_account_json)

    if drive_api_key:
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={drive_api_key}"
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _extract_graph_error(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    error = data.get("error")
    return error if isinstance(error, dict) else {}


def _raise_graph_error(resp: requests.Response, action: str) -> None:
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}

    error = data.get("error", {}) if isinstance(data, dict) else {}
    code = error.get("code")
    subcode = error.get("error_subcode")
    message = error.get("message")
    fbtrace = error.get("fbtrace_id")
    details = (
        f"{action} failed (HTTP {resp.status_code}). "
        f"code={code}, subcode={subcode}, message={message}, fbtrace_id={fbtrace}"
    )
    raise RuntimeError(details)


def _resolve_page_access_token(
    user_access_token: str,
    page_id: str,
    graph_api_version: str,
) -> str | None:
    endpoint = f"https://graph.facebook.com/{graph_api_version}/{page_id}"
    params = {
        "fields": "access_token",
        "access_token": user_access_token,
    }
    resp = requests.get(endpoint, params=params, timeout=60)
    if not resp.ok:
        return None
    data = resp.json()
    token = data.get("access_token")
    if token and isinstance(token, str):
        return token
    return None


def _get_effective_page_access_token(
    access_token: str,
    page_id: str,
    graph_api_version: str,
) -> str:
    page_token = _resolve_page_access_token(
        user_access_token=access_token,
        page_id=page_id,
        graph_api_version=graph_api_version,
    )
    return page_token or access_token


def _build_auto_hashtags(title: str, description: str, max_tags: int = 8) -> str:
    source_text = f"{title} {description}"
    existing = {tag.lower() for tag in re.findall(r"#([A-Za-z0-9_]+)", source_text)}
    phrase_candidates = (
        _iter_hashtag_phrase_candidates(title)
        + _iter_hashtag_phrase_candidates(description)
    )
    single_candidates = (
        _iter_hashtag_single_candidates(title)
        + _iter_hashtag_single_candidates(description)
    )

    tags: list[str] = []
    seen_tags: set[str] = set()
    selected_phrases: list[tuple[str, ...]] = []
    for candidate_pool in (phrase_candidates, single_candidates):
        for candidate, phrase in candidate_pool:
            key = candidate[1:].lower()
            if key in seen_tags or key in existing:
                continue

            # Avoid redundant one-word tags when a longer selected phrase already covers it.
            if len(phrase) == 1 and any(phrase[0] in selected for selected in selected_phrases if len(selected) > 1):
                continue
            if any(_is_subphrase(phrase, selected) for selected in selected_phrases if len(selected) > len(phrase)):
                continue

            seen_tags.add(key)
            selected_phrases.append(phrase)
            tags.append(candidate)
            if len(tags) >= max_tags:
                break
        if len(tags) >= max_tags:
            break

    return " ".join(tags)


def _upload_facebook_video(
    page_id: str,
    access_token: str,
    graph_api_version: str,
    video_path: str,
    title: str,
    description: str,
    published: bool,
) -> str:
    effective_access_token = _get_effective_page_access_token(
        access_token=access_token,
        page_id=page_id,
        graph_api_version=graph_api_version,
    )
    endpoint = f"https://graph-video.facebook.com/{graph_api_version}/{page_id}/videos"
    payload = {
        "access_token": effective_access_token,
        "title": title,
        "description": description,
        "published": "true" if published else "false",
    }

    with open(video_path, "rb") as video_file:
        files = {"source": video_file}
        resp = requests.post(endpoint, data=payload, files=files, timeout=300)

    if not resp.ok:
        _raise_graph_error(resp, "Facebook video upload")
    data = resp.json()

    if "id" not in data:
        raise RuntimeError(f"Facebook upload failed: {data}")

    return str(data["id"])


def _get_facebook_video_source_url(
    video_id: str,
    page_id: str,
    access_token: str,
    graph_api_version: str,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
) -> str:
    effective_access_token = _get_effective_page_access_token(
        access_token=access_token,
        page_id=page_id,
        graph_api_version=graph_api_version,
    )
    endpoint = f"https://graph.facebook.com/{graph_api_version}/{video_id}"
    deadline = time.time() + timeout_seconds
    last_data: dict[str, Any] | None = None

    while time.time() < deadline:
        resp = requests.get(
            endpoint,
            params={
                "fields": "source,status,length",
                "access_token": effective_access_token,
            },
            timeout=60,
        )
        if not resp.ok:
            _raise_graph_error(resp, "Facebook video source fetch")
        data = resp.json()
        last_data = data if isinstance(data, dict) else {"raw": data}
        source = data.get("source")
        if source and isinstance(source, str):
            return source

        time.sleep(interval_seconds)

    raise RuntimeError(
        "Facebook video source URL unavailable after waiting for processing. "
        f"Last response: {last_data}"
    )


def _publish_instagram_reel(
    ig_user_id: str,
    access_token: str,
    graph_api_version: str,
    caption: str,
    video_url: str,
    ingest_context: str = "",
) -> str:
    create_endpoint = f"https://graph.facebook.com/{graph_api_version}/{ig_user_id}/media"
    create_payload = {
        "access_token": access_token,
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
    }

    create_resp = requests.post(create_endpoint, data=create_payload, timeout=60)
    if not create_resp.ok:
        _raise_graph_error(create_resp, "Instagram media creation")
    create_data = create_resp.json()
    creation_id = create_data.get("id")

    if not creation_id:
        raise RuntimeError(f"Instagram media creation failed: {create_data}")

    _wait_for_instagram_media_ready(
        creation_id=creation_id,
        access_token=access_token,
        graph_api_version=graph_api_version,
        ingest_context=ingest_context,
    )

    publish_endpoint = f"https://graph.facebook.com/{graph_api_version}/{ig_user_id}/media_publish"
    publish_payload = {
        "access_token": access_token,
        "creation_id": creation_id,
    }

    publish_resp = requests.post(publish_endpoint, data=publish_payload, timeout=60)
    if not publish_resp.ok:
        _raise_graph_error(publish_resp, "Instagram media publish")
    publish_data = publish_resp.json()
    media_id = publish_data.get("id")

    if not media_id:
        raise RuntimeError(f"Instagram media publish failed: {publish_data}")

    return str(media_id)


def _publish_instagram_reel_resumable(
    ig_user_id: str,
    access_token: str,
    graph_api_version: str,
    caption: str,
    video_path: str,
    ingest_context: str = "",
) -> str:
    create_endpoint = f"https://graph.facebook.com/{graph_api_version}/{ig_user_id}/media"
    create_payload = {
        "access_token": access_token,
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption,
        "share_to_feed": "true",
    }

    create_resp = requests.post(create_endpoint, data=create_payload, timeout=60)
    if not create_resp.ok:
        _raise_graph_error(create_resp, "Instagram resumable container creation")
    create_data = create_resp.json()
    creation_id = create_data.get("id")
    rupload_uri = create_data.get("uri")

    if not creation_id:
        raise RuntimeError(f"Instagram resumable container creation failed: {create_data}")
    if not rupload_uri or not isinstance(rupload_uri, str):
        rupload_uri = f"https://rupload.facebook.com/ig-api-upload/{graph_api_version}/{creation_id}"

    file_size = os.path.getsize(video_path)
    upload_headers = {
        "Authorization": f"OAuth {access_token}",
        "offset": "0",
        "file_size": str(file_size),
    }
    with open(video_path, "rb") as video_file:
        upload_resp = requests.post(
            rupload_uri,
            headers=upload_headers,
            data=video_file,
            timeout=600,
        )
    if not upload_resp.ok:
        _raise_graph_error(upload_resp, "Instagram resumable binary upload")

    _wait_for_instagram_media_ready(
        creation_id=creation_id,
        access_token=access_token,
        graph_api_version=graph_api_version,
        ingest_context=(
            f"{ingest_context} | mode=resumable | file_size={file_size}"
        ),
    )

    publish_endpoint = f"https://graph.facebook.com/{graph_api_version}/{ig_user_id}/media_publish"
    publish_payload = {
        "access_token": access_token,
        "creation_id": creation_id,
    }

    publish_resp = requests.post(publish_endpoint, data=publish_payload, timeout=60)
    if not publish_resp.ok:
        _raise_graph_error(publish_resp, "Instagram resumable media publish")
    publish_data = publish_resp.json()
    media_id = publish_data.get("id")

    if not media_id:
        raise RuntimeError(f"Instagram resumable media publish failed: {publish_data}")

    return str(media_id)


def _wait_for_instagram_media_ready(
    creation_id: str,
    access_token: str,
    graph_api_version: str,
    ingest_context: str = "",
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
) -> None:
    endpoint = f"https://graph.facebook.com/{graph_api_version}/{creation_id}"
    deadline = time.time() + timeout_seconds
    last_status = None

    while time.time() < deadline:
        resp = requests.get(
            endpoint,
            params={
                "fields": "status_code,status",
                "access_token": access_token,
            },
            timeout=30,
        )
        if not resp.ok:
            _raise_graph_error(resp, "Instagram media status check")

        data = resp.json()
        status_code = str(data.get("status_code", "")).upper()
        status_text = str(data.get("status", "")).upper()
        status_error_obj = data.get("status_error")
        status_error_msg = ""
        if isinstance(status_error_obj, dict):
            status_error_msg = str(status_error_obj.get("message", "")).strip()
        last_status = status_code or status_text or "UNKNOWN"

        if status_code == "FINISHED":
            return
        if status_code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(
                "Instagram media creation failed with status "
                f"`{status_code}`. Details: {status_error_msg or 'n/a'}"
                f" | status_payload={data}{ingest_context}"
            )

        time.sleep(interval_seconds)

    raise RuntimeError(
        "Instagram media processing did not finish in time "
        f"(last status: {last_status})."
    )


def upload_instagram_facebook_video(
    video_path: str,
    title: str,
    description: str = "",
    instagram_video_url: str | None = None,
    drive_folder_url: str | None = None,
    upload_facebook: bool = True,
    upload_instagram: bool = True,
) -> dict[str, Any]:
    """Upload to configured Meta surfaces.

    Returns
    -------
    dict:
        {
          "facebook_video_id": str | None,
          "instagram_media_id": str | None,
          "instagram_error": str | None
        }
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    config = get_meta_config()
    access_token = config["access_token"]
    graph_api_version = config["graph_api_version"]
    publish_facebook = bool(config.get("facebook_publish", True))
    drive_api_key = config.get("google_drive_api_key")
    drive_service_account_json = config.get("google_service_account_json")
    drive_auto_make_public = bool(config.get("google_drive_auto_make_public", True))
    instagram_transcode_retry = bool(config.get("instagram_transcode_retry", True))
    instagram_use_resumable_upload = bool(config.get("instagram_use_resumable_upload", True))
    instagram_facebook_source_fallback = bool(
        config.get("instagram_facebook_source_fallback", True)
    )
    facebook_page_id = config.get("facebook_page_id", "").strip()
    instagram_user_id = config.get("instagram_business_account_id", "").strip()

    if not upload_facebook and not upload_instagram:
        raise ValueError("Select at least one target: Facebook and/or Instagram.")

    if upload_facebook and not facebook_page_id:
        raise ValueError("Missing `facebook_page_id` in meta_credentials.json.")

    if upload_instagram and not instagram_user_id:
        raise ValueError("Missing `instagram_business_account_id` in meta_credentials.json.")

    if (
        upload_facebook
        and upload_instagram
        and not facebook_page_id
        and not instagram_user_id
    ):
        raise ValueError(
            "meta_credentials.json must include facebook_page_id, "
            "instagram_business_account_id, or both."
        )

    result: dict[str, Any] = {
        "facebook_video_id": None,
        "instagram_media_id": None,
        "instagram_error": None,
    }
    hashtags = _build_auto_hashtags(title, description)
    fb_description = description.strip()
    if hashtags:
        fb_description = f"{fb_description}\n\n{hashtags}" if fb_description else hashtags

    if upload_facebook and facebook_page_id:
        result["facebook_video_id"] = _upload_facebook_video(
            page_id=facebook_page_id,
            access_token=access_token,
            graph_api_version=graph_api_version,
            video_path=video_path,
            title=title,
            description=fb_description,
            published=publish_facebook,
        )

    if upload_instagram and instagram_user_id:
        caption = title if not fb_description else f"{title}\n\n{fb_description}"

        if instagram_use_resumable_upload:
            try:
                result["instagram_media_id"] = _publish_instagram_reel_resumable(
                    ig_user_id=instagram_user_id,
                    access_token=access_token,
                    graph_api_version=graph_api_version,
                    caption=caption[:2200],
                    video_path=video_path,
                )
                return result
            except RuntimeError as e:
                if instagram_transcode_retry and "status `ERROR`" in str(e):
                    transcoded_path = _transcode_video_for_instagram(video_path)
                    try:
                        result["instagram_media_id"] = _publish_instagram_reel_resumable(
                            ig_user_id=instagram_user_id,
                            access_token=access_token,
                            graph_api_version=graph_api_version,
                            caption=caption[:2200],
                            video_path=transcoded_path,
                            ingest_context=" | retry=transcoded_local",
                        )
                        return result
                    finally:
                        try:
                            os.remove(transcoded_path)
                        except OSError:
                            pass
                raise

        effective_drive_folder_url = drive_folder_url or config.get("google_drive_folder_url")
        if not instagram_video_url and not effective_drive_folder_url:
            if upload_facebook:
                result["instagram_error"] = (
                    "Instagram skipped: missing public URL. "
                    "Set `instagram_video_url`/`public_video_url` or `google_drive_folder_url`."
                )
                return result
            raise ValueError(
                "Instagram posting needs a public video URL. "
                "Set `instagram_video_url`/`public_video_url` or `google_drive_folder_url`."
            )
        normalized_instagram_video_url = _resolve_instagram_video_url(
            instagram_video_url=instagram_video_url,
            drive_folder_url=effective_drive_folder_url,
            video_filename=os.path.basename(video_path),
            drive_api_key=drive_api_key,
            drive_service_account_json=drive_service_account_json,
            drive_auto_make_public=drive_auto_make_public,
        )
        preflight = _assert_public_video_url_fetchable(normalized_instagram_video_url)
        publish_video_url = _add_cache_buster(
            preflight.get("final_url") or normalized_instagram_video_url
        )
        ingest_context = (
            " | URL preflight: "
            f"content_type={preflight.get('content_type')}, "
            f"content_length={preflight.get('content_length') or 'unknown'}, "
            f"final_url={preflight.get('final_url')}"
        )
        try:
            result["instagram_media_id"] = _publish_instagram_reel(
                ig_user_id=instagram_user_id,
                access_token=access_token,
                graph_api_version=graph_api_version,
                caption=caption[:2200],
                video_url=publish_video_url,
                ingest_context=ingest_context,
            )
        except RuntimeError as e:
            last_error = e
            msg = str(last_error)
            folder_id = _extract_google_drive_folder_id(effective_drive_folder_url or "")
            if (
                instagram_transcode_retry
                and "status `ERROR`" in msg
                and folder_id
                and drive_service_account_json
            ):
                transcoded_path = _transcode_video_for_instagram(video_path)
                try:
                    upload_name = f"{os.path.splitext(os.path.basename(video_path))[0]}_igsafe.mp4"
                    source_file_id = _extract_google_drive_file_id_from_direct_url(
                        normalized_instagram_video_url
                    )
                    if source_file_id:
                        _update_drive_file_content(
                            file_id=source_file_id,
                            file_path=transcoded_path,
                            drive_service_account_json=drive_service_account_json,
                        )
                        fallback_file_id = source_file_id
                    else:
                        try:
                            fallback_file_id = _upload_file_to_drive_folder(
                                file_path=transcoded_path,
                                folder_id=folder_id,
                                drive_service_account_json=drive_service_account_json,
                                upload_name=upload_name,
                            )
                        except RuntimeError as upload_err:
                            upload_msg = str(upload_err)
                            if "Service Accounts do not have storage quota" not in upload_msg:
                                raise
                            raise RuntimeError(
                                "Drive upload quota blocked fallback transcode upload, and source "
                                "Drive file ID could not be inferred for in-place update."
                            ) from upload_err
                    if drive_auto_make_public:
                        _ensure_drive_file_public(fallback_file_id, drive_service_account_json)
                    fallback_url = _build_drive_download_url(fallback_file_id, drive_api_key)
                    fallback_preflight = _assert_public_video_url_fetchable(fallback_url)
                    fallback_publish_url = _add_cache_buster(
                        fallback_preflight.get("final_url") or fallback_url
                    )
                    fallback_context = (
                        " | retry=transcoded_upload"
                        f" | fallback_file_id={fallback_file_id}"
                        f" | content_type={fallback_preflight.get('content_type')}"
                        f" | content_length={fallback_preflight.get('content_length') or 'unknown'}"
                        f" | final_url={fallback_preflight.get('final_url')}"
                    )
                    try:
                        result["instagram_media_id"] = _publish_instagram_reel(
                            ig_user_id=instagram_user_id,
                            access_token=access_token,
                            graph_api_version=graph_api_version,
                            caption=caption[:2200],
                            video_url=fallback_publish_url,
                            ingest_context=fallback_context,
                        )
                        return result
                    except RuntimeError as e2:
                        last_error = e2
                finally:
                    try:
                        os.remove(transcoded_path)
                    except OSError:
                        pass

            # Final fallback: host source video on Facebook CDN (unpublished) and retry IG.
            if (
                instagram_facebook_source_fallback
                and "status `ERROR`" in str(last_error)
                and facebook_page_id
            ):
                fb_fallback_input_path = _transcode_video_for_instagram(video_path)
                try:
                    fallback_fb_video_id = _upload_facebook_video(
                        page_id=facebook_page_id,
                        access_token=access_token,
                        graph_api_version=graph_api_version,
                        video_path=fb_fallback_input_path,
                        title=title,
                        description=fb_description,
                        published=False,
                    )
                    fb_source_url = _get_facebook_video_source_url(
                        video_id=fallback_fb_video_id,
                        page_id=facebook_page_id,
                        access_token=access_token,
                        graph_api_version=graph_api_version,
                    )
                    fb_preflight = _assert_public_video_url_fetchable(fb_source_url)
                    fb_publish_url = _add_cache_buster(fb_preflight.get("final_url") or fb_source_url)
                    fb_context = (
                        " | retry=facebook_source_fallback"
                        f" | fallback_fb_video_id={fallback_fb_video_id}"
                        f" | content_type={fb_preflight.get('content_type')}"
                        f" | content_length={fb_preflight.get('content_length') or 'unknown'}"
                        f" | final_url={fb_preflight.get('final_url')}"
                    )
                    result["instagram_media_id"] = _publish_instagram_reel(
                        ig_user_id=instagram_user_id,
                        access_token=access_token,
                        graph_api_version=graph_api_version,
                        caption=caption[:2200],
                        video_url=fb_publish_url,
                        ingest_context=fb_context,
                    )
                    return result
                finally:
                    try:
                        os.remove(fb_fallback_input_path)
                    except OSError:
                        pass

            raise last_error

    return result
