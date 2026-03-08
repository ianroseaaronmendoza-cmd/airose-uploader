"""TikTok video uploader using the Content Posting API (direct post).

Workflow (two-step):
1. POST /v2/post/publish/inbox/video/init  → get upload_url
2. PUT the video file to upload_url         → TikTok processes it

Reference: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
"""

import os
import requests
from core.tiktok_auth import get_tiktok_access_token

PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


def upload_tiktok_video(video_path: str, title: str, description: str = "") -> str:
    """Upload a video to TikTok and return the publish_id.

    Parameters
    ----------
    video_path : str
        Absolute path to the .mp4 file.
    title : str
        Video title / caption text (max ~2200 chars).
    description : str
        Extra description (appended to title as TikTok only has a caption field).

    Returns
    -------
    str
        The publish_id returned by TikTok.
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    access_token = get_tiktok_access_token()
    file_size = os.path.getsize(video_path)

    # Build caption from title + description
    caption = title
    if description:
        caption = f"{title}\n\n{description}"

    # ── Step 1: Initialise the upload ──
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    init_body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": "SELF_ONLY",        # safe default — change to PUBLIC_TO_EVERYONE when ready
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,              # single-chunk upload
            "total_chunk_count": 1,
        },
    }

    resp = requests.post(PUBLISH_URL, headers=headers, json=init_body)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data and data["error"].get("code") != "ok":
        raise RuntimeError(f"TikTok init failed: {data['error']}")

    upload_url = data["data"]["upload_url"]
    publish_id = data["data"]["publish_id"]

    # ── Step 2: Upload the video bytes ──
    with open(video_path, "rb") as f:
        put_headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(file_size),
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        }
        put_resp = requests.put(upload_url, headers=put_headers, data=f)
        put_resp.raise_for_status()

    print(f"TikTok upload complete. publish_id: {publish_id}")
    return publish_id
