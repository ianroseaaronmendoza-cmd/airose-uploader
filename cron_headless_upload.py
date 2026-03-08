import argparse
import json
import os
import re
import tempfile
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

from core.metadata_loader import (
    METADATA_FOLDER,
    VIDEO_FOLDER,
    build_youtube_title_with_hashtags,
    load_metadata,
    style_description_text,
    style_title_text,
)
from core.meta_uploader import upload_instagram_facebook_video
from core.youtube_uploader import upload_video

PUBLIC_VIDEO_URL_KEYS = (
    "youtube_video_url",
    "public_video_url",
    "instagram_video_url",
    "google_drive_link",
    "google_drive_url",
)
DRIVE_FOLDER_URL_KEYS = (
    "google_drive_folder_url",
    "google_drive_folder_link",
    "drive_folder_url",
)


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _get_first_non_empty(data: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _get_public_video_url(data: dict) -> str | None:
    return _get_first_non_empty(data, PUBLIC_VIDEO_URL_KEYS)


def _get_drive_folder_url(data: dict) -> str | None:
    return _get_first_non_empty(data, DRIVE_FOLDER_URL_KEYS)


def _is_approved_for_upload(data: dict) -> bool:
    return bool(
        data.get("upload_status", {})
        .get("youtube", {})
        .get("approved", False)
    )


def _extract_google_drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" not in host and "drive.usercontent.google.com" not in host:
        return None

    file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", path)
    if file_match:
        return file_match.group(1)

    return parse_qs(parsed.query).get("id", [None])[0]


def _normalize_public_video_url(video_url: str) -> str:
    url = (video_url or "").strip()
    if not url:
        raise ValueError("Missing public video URL.")

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "drive.google.com" in host and "/drive/folders/" in path:
        raise ValueError("Expected Google Drive file URL, but got folder URL.")

    file_id = _extract_google_drive_file_id(url)
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    return url


def _download_public_video_to_temp(video_url: str, temp_prefix: str) -> str:
    normalized_url = _normalize_public_video_url(video_url)
    try:
        resp = requests.get(normalized_url, stream=True, timeout=300, allow_redirects=True)
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to fetch source video URL: {exc}") from exc

    if not resp.ok:
        raise RuntimeError(
            f"Source video URL returned HTTP {resp.status_code}: {normalized_url}"
        )

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", temp_prefix)[:24] or "asset"
    fd, temp_path = tempfile.mkstemp(prefix=f"{safe_prefix}_", suffix=".mp4")
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
            "Source URL resolved to HTML instead of video bytes. "
            "Ensure it is publicly downloadable."
        )

    if os.path.getsize(temp_path) == 0:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise RuntimeError("Downloaded source video is empty.")

    return temp_path


def _resolve_video_path_for_igfb(
    asset_id: str,
    local_video_path: str,
    source_video_url: str | None,
    allow_temp_download: bool,
    temp_files: list[str],
) -> str:
    if os.path.isfile(local_video_path):
        return local_video_path

    if not allow_temp_download:
        raise FileNotFoundError(
            f"Local video file missing for {asset_id}: {local_video_path}"
        )

    if not source_video_url:
        raise FileNotFoundError(
            f"Local video file missing for {asset_id}, and no public video URL was provided."
        )

    temp_path = _download_public_video_to_temp(
        video_url=source_video_url,
        temp_prefix=f"igfb_{asset_id}",
    )
    temp_files.append(temp_path)
    return temp_path


def _parse_platforms(value: str) -> set[str]:
    allowed = {"youtube", "igfb"}
    parts = {item.strip().lower() for item in value.split(",") if item.strip()}
    unknown = parts - allowed
    if unknown:
        raise ValueError(f"Unsupported platform(s): {sorted(unknown)}")
    return parts or {"youtube", "igfb"}


def _parse_ids(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless uploader for scheduled runs (YouTube + IG/FB)."
    )
    parser.add_argument(
        "--platforms",
        default="youtube,igfb",
        help="Comma-separated list: youtube,igfb (default: youtube,igfb).",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Optional comma-separated asset IDs to process.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of assets to process (0 = no limit).",
    )
    parser.add_argument(
        "--allow-interactive-auth",
        action="store_true",
        help="Allow browser-based YouTube auth fallback (usually disabled for CI).",
    )
    parser.add_argument(
        "--no-temp-download",
        action="store_true",
        help="Disable temporary source-video download when local files are missing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run without performing uploads or metadata writes.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first upload error.",
    )
    args = parser.parse_args()

    selected_platforms = _parse_platforms(args.platforms)
    selected_ids = _parse_ids(args.ids)
    allow_temp_download = not args.no_temp_download

    print("Headless upload start")
    print(f"Metadata folder: {METADATA_FOLDER}")
    print(f"Video folder: {VIDEO_FOLDER}")
    print(f"Platforms: {','.join(sorted(selected_platforms))}")
    print(f"Dry run: {args.dry_run}")

    assets = load_metadata()
    if selected_ids:
        assets = [asset for asset in assets if asset.id in selected_ids]
    if args.limit > 0:
        assets = assets[:args.limit]

    totals = {
        "assets_seen": len(assets),
        "assets_processed": 0,
        "assets_skipped_unapproved": 0,
        "youtube_uploaded": 0,
        "igfb_updated": 0,
        "errors": 0,
    }

    for asset in assets:
        print(f"\nAsset: {asset.id}")
        if asset.error_state:
            totals["errors"] += 1
            print(f"  Skip: metadata load error: {asset.error_message}")
            if args.fail_fast:
                break
            continue

        data = _load_json(asset.metadata_path)
        if not _is_approved_for_upload(data):
            totals["assets_skipped_unapproved"] += 1
            print("  Skip: not approved")
            continue

        totals["assets_processed"] += 1
        changed = False
        temp_files: list[str] = []

        title = style_title_text(data.get("title", "")) or style_title_text(asset.id)
        description = style_description_text(data.get("description", ""))

        stop_now = False

        if "youtube" in selected_platforms:
            yt_status = data.setdefault("upload_status", {}).setdefault("youtube", {})
            if yt_status.get("uploaded"):
                print("  YouTube: already uploaded")
            else:
                source_video_url = _get_public_video_url(data)
                if not source_video_url:
                    message = (
                        "YouTube upload requires one of: "
                        "youtube_video_url/public_video_url/instagram_video_url/google_drive_link/google_drive_url"
                    )
                    totals["errors"] += 1
                    print(f"  YouTube Error: {message}")
                    if not args.dry_run:
                        yt_status["error"] = message
                        changed = True
                    if args.fail_fast:
                        stop_now = True
                elif args.dry_run:
                    print(f"  YouTube: would upload from {source_video_url}")
                else:
                    try:
                        youtube_title = build_youtube_title_with_hashtags(title, description)
                        video_id = upload_video(
                            video_path=asset.video_path,
                            title=youtube_title,
                            description=description,
                            video_url=source_video_url,
                            allow_interactive_auth=args.allow_interactive_auth,
                        )
                        yt_status["uploaded"] = True
                        yt_status["uploaded_at"] = _utcnow_iso()
                        yt_status["video_id"] = video_id
                        yt_status["error"] = None
                        totals["youtube_uploaded"] += 1
                        changed = True
                        print(f"  YouTube: uploaded video_id={video_id}")
                    except Exception as exc:
                        message = str(exc)
                        totals["errors"] += 1
                        print(f"  YouTube Error: {message}")
                        if not args.dry_run:
                            yt_status["error"] = message
                            changed = True
                        if args.fail_fast:
                            stop_now = True

        if not stop_now and "igfb" in selected_platforms:
            igfb_status = data.setdefault("upload_status", {}).setdefault("instagram_facebook", {})
            do_fb = not bool(igfb_status.get("facebook_video_id"))
            do_ig = not bool(igfb_status.get("instagram_media_id"))

            if not do_fb and not do_ig:
                print("  IG/FB: already uploaded")
            elif args.dry_run:
                print("  IG/FB: would upload pending targets")
            else:
                instagram_video_url = _get_first_non_empty(data, (
                    "instagram_video_url",
                    "public_video_url",
                    "google_drive_link",
                    "google_drive_url",
                ))
                drive_folder_url = _get_drive_folder_url(data)
                try:
                    video_path_for_meta = _resolve_video_path_for_igfb(
                        asset_id=asset.id,
                        local_video_path=asset.video_path,
                        source_video_url=instagram_video_url,
                        allow_temp_download=allow_temp_download,
                        temp_files=temp_files,
                    )
                    result = upload_instagram_facebook_video(
                        video_path=video_path_for_meta,
                        title=title,
                        description=description,
                        instagram_video_url=instagram_video_url,
                        drive_folder_url=drive_folder_url,
                        upload_facebook=do_fb,
                        upload_instagram=do_ig,
                    )
                    if result.get("facebook_video_id"):
                        igfb_status["facebook_video_id"] = result.get("facebook_video_id")
                    if result.get("instagram_media_id"):
                        igfb_status["instagram_media_id"] = result.get("instagram_media_id")
                    igfb_status["uploaded"] = bool(
                        igfb_status.get("facebook_video_id") or igfb_status.get("instagram_media_id")
                    )
                    igfb_status["uploaded_at"] = _utcnow_iso()
                    igfb_status["error"] = result.get("instagram_error")
                    totals["igfb_updated"] += 1
                    changed = True
                    print(
                        "  IG/FB: updated "
                        f"facebook_video_id={igfb_status.get('facebook_video_id')} "
                        f"instagram_media_id={igfb_status.get('instagram_media_id')}"
                    )
                except Exception as exc:
                    message = str(exc)
                    totals["errors"] += 1
                    print(f"  IG/FB Error: {message}")
                    if not args.dry_run:
                        igfb_status["error"] = message
                        changed = True
                    if args.fail_fast:
                        stop_now = True

        if changed and not args.dry_run:
            _save_json(asset.metadata_path, data)

        for temp_file in temp_files:
            try:
                os.remove(temp_file)
            except OSError:
                pass

        if stop_now:
            break

    print("\nHeadless upload summary")
    for key, value in totals.items():
        print(f"  {key}: {value}")

    return 1 if totals["errors"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
