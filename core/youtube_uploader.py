import os
import re
import tempfile
from urllib.parse import parse_qs, urlparse

import requests
from googleapiclient.http import MediaFileUpload

from core.youtube_auth import get_authenticated_service


def _extract_google_drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" not in host and "drive.usercontent.google.com" not in host:
        return None

    file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", path)
    if file_match:
        return file_match.group(1)

    file_id = parse_qs(parsed.query).get("id", [None])[0]
    if file_id:
        return file_id

    return None


def _normalize_public_video_url(video_url: str) -> str:
    url = (video_url or "").strip()
    if not url:
        raise ValueError(
            "YouTube upload now expects a Google Drive/public URL in metadata "
            "(youtube_video_url/public_video_url/google_drive_link)."
        )

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" in host and "/drive/folders/" in path:
        raise ValueError("Use a Google Drive file link for YouTube, not a folder link.")

    file_id = _extract_google_drive_file_id(url)
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    return url


def _download_public_video_to_temp(video_url: str) -> str:
    normalized_url = _normalize_public_video_url(video_url)
    try:
        resp = requests.get(normalized_url, stream=True, timeout=300, allow_redirects=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch YouTube source video URL: {exc}") from exc

    if not resp.ok:
        raise RuntimeError(
            f"YouTube source URL returned HTTP {resp.status_code}: {normalized_url}"
        )

    fd, temp_path = tempfile.mkstemp(prefix="yt_src_", suffix=".mp4")
    os.close(fd)
    first_sample = b""

    try:
        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                if not first_sample:
                    first_sample = chunk[:256]
                f.write(chunk)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    finally:
        resp.close()

    content_type = (resp.headers.get("Content-Type") or "").lower()
    sample_text = first_sample.decode("utf-8", errors="ignore").strip().lower()
    looks_like_html = sample_text.startswith("<!doctype html") or sample_text.startswith("<html")

    if "text/html" in content_type or looks_like_html:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError(
            "Google Drive URL resolved to HTML instead of raw video bytes. "
            "Ensure the link is a public, direct file download URL."
        )

    if os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError("Downloaded YouTube source video is empty.")

    return temp_path


def upload_video(
    video_path: str,
    title: str,
    description: str,
    video_url: str | None = None,
    allow_interactive_auth: bool = True,
) -> str:
    """
    Upload a video to YouTube.
    Prefer public URL source when provided; otherwise use local video path.
    Returns the uploaded video's ID.
    """
    temp_path = None
    source_path = None

    normalized_url = (video_url or "").strip()
    if normalized_url:
        source_path = _download_public_video_to_temp(normalized_url)
        temp_path = source_path
    else:
        source_path = (video_path or "").strip()
        if not source_path:
            raise ValueError(
                "Missing video source. Provide a local file path or a public video URL."
            )
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Local video file not found: {source_path}")

    try:
        youtube = get_authenticated_service(allow_interactive=allow_interactive_auth)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22"  # People & Blogs
            },
            "status": {
                "privacyStatus": "public",  # SAFE MODE
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            source_path,
            chunksize=-1,
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None

        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Uploading... {int(status.progress() * 100)}%")

        print("Upload complete.")
        return response["id"]
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
