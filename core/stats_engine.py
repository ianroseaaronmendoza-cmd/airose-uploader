from typing import List
from core.models import VideoAsset


def compute_stats(assets: List[VideoAsset]):
    total = len(assets)
    valid = sum(1 for a in assets if a.video_exists and not a.error_state)
    missing = sum(1 for a in assets if not a.video_exists)

    yt_uploaded = sum(
        1 for a in assets
        if a.upload_status.get("youtube", {}).get("uploaded") is True
    )

    tt_uploaded = sum(
        1 for a in assets
        if a.upload_status.get("tiktok", {}).get("uploaded") is True
    )

    ig_uploaded = sum(
        1 for a in assets
        if a.upload_status.get("instagram_facebook", {}).get("uploaded") is True
    )

    pin_uploaded = sum(
        1 for a in assets
        if a.upload_status.get("pinterest", {}).get("uploaded") is True
    )

    return {
        "total": total,
        "valid": valid,
        "missing": missing,
        "yt_uploaded": yt_uploaded,
        "tt_uploaded": tt_uploaded,
        "ig_uploaded": ig_uploaded,
        "pin_uploaded": pin_uploaded,
    }
