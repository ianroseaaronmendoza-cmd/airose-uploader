import argparse
import json
import os
import random
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
from core.pinterest_uploader import (
    explain_pinterest_readiness,
    resolve_pinterest_board_id,
    resolve_pinterest_link,
    resolve_pinterest_media_url,
    sanitize_pinterest_text,
    upload_pinterest_pin,
)
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


def _describe_url_host(url: str) -> str:
    candidate = (url or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except Exception:
        return ""
    return parsed.netloc.lower()


def _is_approved_for_upload(data: dict) -> bool:
    upload_status = data.get("upload_status", {})
    for platform in ("youtube", "tiktok", "instagram_facebook", "pinterest"):
        if upload_status.get(platform, {}).get("approved", False):
            return True
    return False


def _needs_youtube_upload(data: dict) -> bool:
    return not bool(
        data.get("upload_status", {})
        .get("youtube", {})
        .get("uploaded", False)
    )


def _needs_igfb_upload(data: dict) -> bool:
    igfb_status = data.get("upload_status", {}).get("instagram_facebook", {})
    has_facebook = bool(igfb_status.get("facebook_video_id"))
    has_instagram = bool(igfb_status.get("instagram_media_id"))
    return not (has_facebook and has_instagram)


def _has_pending_selected_platforms(data: dict, selected_platforms: set[str]) -> bool:
    if "youtube" in selected_platforms and _needs_youtube_upload(data):
        return True
    if "igfb" in selected_platforms and _needs_igfb_upload(data):
        return True
    if "pinterest" in selected_platforms and _needs_pinterest_upload(data):
        return True
    return False


def _needs_pinterest_upload(data: dict) -> bool:
    return not bool(
        data.get("upload_status", {})
        .get("pinterest", {})
        .get("uploaded", False)
    )


def _get_pending_selected_platforms(data: dict, selected_platforms: set[str]) -> set[str]:
    pending: set[str] = set()
    if "youtube" in selected_platforms and _needs_youtube_upload(data):
        pending.add("youtube")
    if "igfb" in selected_platforms and _needs_igfb_upload(data):
        pending.add("igfb")
    if "pinterest" in selected_platforms and _needs_pinterest_upload(data):
        pending.add("pinterest")
    return pending


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
    allowed = {"youtube", "igfb", "pinterest"}
    parts = {item.strip().lower() for item in value.split(",") if item.strip()}
    unknown = parts - allowed
    if unknown:
        raise ValueError(f"Unsupported platform(s): {sorted(unknown)}")
    return parts or {"youtube", "igfb"}


def _parse_ids(value: str) -> set[str]:
    """Parse comma-separated asset IDs. Returns empty set if value is empty."""
    return {item.strip() for item in value.split(",") if item.strip()}


def _parse_preset(value: str) -> str:
    preset = (value or "").strip().lower()
    allowed = {"faith", "love", "sentimental", "neutral"}
    if preset not in allowed:
        raise ValueError(f"Unsupported preset: {preset!r}. Expected one of {sorted(allowed)}")
    return preset


def _get_preset_for_current_time() -> str:
    """Return the preset to use based on the current scheduled time."""
    now = datetime.utcnow()
    hour = now.hour
    minute = now.minute
    
    # Map scheduled times to presets
    if hour == 0 and minute < 9:  # 00:00-00:08
        return "faith"
    elif hour == 6 and minute >= 9:  # 06:09+
        return "love"
    elif hour == 12 and minute >= 18:  # 12:18+
        return "sentimental"
    elif hour == 18 and minute >= 27:  # 18:27+
        return "neutral"
    elif hour < 6:  # 00:09-05:59
        return "faith"
    elif hour < 12:  # 06:00-11:59
        return "love"
    elif hour < 18:  # 12:00-17:59
        return "sentimental"
    else:  # 18:00-23:59
        return "neutral"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless uploader for scheduled runs (YouTube + IG/FB + Pinterest)."
    )
    parser.add_argument(
        "--platforms",
        default="youtube,igfb",
        help="Comma-separated list: youtube,igfb,pinterest (default: youtube,igfb).",
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
    parser.add_argument(
        "--random-one",
        action="store_true",
        help="Pick exactly one approved asset at random from the filtered pool.",
    )
    parser.add_argument(
        "--preset",
        default="",
        help="Optional preset override for --random-one: faith,love,sentimental,neutral.",
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
    if args.random_one:
        target_preset = (
            _parse_preset(args.preset)
            if args.preset.strip()
            else _get_preset_for_current_time()
        )
        print(f"Target preset for this run: {target_preset}")

        candidate_assets: list[tuple] = []
        max_pending_count = 0
        for asset in assets:
            if asset.error_state:
                continue
            if not _is_approved_for_upload({"upload_status": asset.upload_status}):
                continue
            pending_platforms = _get_pending_selected_platforms(
                {"upload_status": asset.upload_status},
                selected_platforms,
            )
            if not pending_platforms:
                continue

            # Load metadata to check preset
            try:
                data = _load_json(asset.metadata_path)
                asset_preset = data.get("preset", "").strip().lower()
                if asset_preset == target_preset.lower():
                    pending_count = len(pending_platforms)
                    max_pending_count = max(max_pending_count, pending_count)
                    candidate_assets.append((asset, pending_platforms))
            except Exception:
                continue

        prioritized_assets = [
            asset
            for asset, pending_platforms in candidate_assets
            if len(pending_platforms) == max_pending_count
        ]
        print(
            f"Random selection candidates with preset '{target_preset}': "
            f"{len(candidate_assets)} total, {len(prioritized_assets)} prioritized "
            f"with {max_pending_count} pending selected platform(s)"
        )
        if prioritized_assets:
            selected_asset = random.SystemRandom().choice(prioritized_assets)
            print(f"Randomly selected asset: {selected_asset.id}")
            assets = [selected_asset]
        else:
            print(f"Random selection skipped: no approved assets with preset '{target_preset}' pending selected platforms.")
            assets = []

    totals = {
        "assets_seen": len(assets),
        "assets_processed": 0,
        "assets_skipped_unapproved": 0,
        "youtube_uploaded": 0,
        "igfb_updated": 0,
        "pinterest_uploaded": 0,
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
                local_video_exists = bool(asset.video_path) and os.path.isfile(asset.video_path)
                if not source_video_url and not local_video_exists:
                    message = (
                        "YouTube upload requires a synced local video file or one of: "
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
                    if source_video_url:
                        print(f"  YouTube: would upload from {source_video_url}")
                    else:
                        print(f"  YouTube: would upload local file {asset.video_path}")
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

        if not stop_now and "pinterest" in selected_platforms:
            pinterest_status = data.setdefault("upload_status", {}).setdefault("pinterest", {})
            if pinterest_status.get("uploaded"):
                print("  Pinterest: already uploaded")
            elif args.dry_run:
                print("  Pinterest: would create pin")
            else:
                pinterest_title = sanitize_pinterest_text(
                    style_title_text(data.get("pinterest_title", "")) or title
                )
                pinterest_description = sanitize_pinterest_text(
                    style_description_text(data.get("pinterest_description", ""))
                    or description
                )
                effective_board_id = resolve_pinterest_board_id(data)
                pinterest_media_url = resolve_pinterest_media_url(
                    data,
                    video_path=asset.video_path,
                )
                pinterest_media_source = data.get("pinterest_media_source")
                pinterest_link = resolve_pinterest_link(data)
                pinterest_link_host = _describe_url_host(pinterest_link)
                pinterest_media_host = _describe_url_host(pinterest_media_url or "")
                pinterest_media_origin = (
                    "explicit_media_source"
                    if isinstance(pinterest_media_source, dict)
                    else "local_video"
                    if os.path.isfile(asset.video_path)
                    else "remote_media_url"
                    if pinterest_media_url
                    else "missing"
                )
                print(
                    "  Pinterest Debug: "
                    f"board_id={effective_board_id or '(missing)'} "
                    f"section_id={_get_first_non_empty(data, ('pinterest_board_section_id',)) or '(none)'} "
                    f"link_present={'yes' if pinterest_link else 'no'} "
                    f"link_host={pinterest_link_host or '(none)'} "
                    f"media_origin={pinterest_media_origin} "
                    f"media_host={pinterest_media_host or '(local)'}"
                )
                try:
                    pin_id = upload_pinterest_pin(
                        title=pinterest_title,
                        description=pinterest_description,
                        video_path=asset.video_path,
                        media_url=pinterest_media_url or "",
                        link=pinterest_link,
                        alt_text=_get_first_non_empty(
                            data,
                            ("pinterest_alt_text", "description"),
                        ) or "",
                        board_id=effective_board_id,
                        board_section_id=_get_first_non_empty(
                            data,
                            ("pinterest_board_section_id",),
                        ) or "",
                        media_source=(
                            pinterest_media_source
                            if isinstance(pinterest_media_source, dict)
                            else None
                        ),
                        cover_image_key_frame_time=data.get("pinterest_cover_image_key_frame_time"),
                    )
                    pinterest_status["uploaded"] = True
                    pinterest_status["uploaded_at"] = _utcnow_iso()
                    pinterest_status["pin_id"] = pin_id
                    pinterest_status["board_id"] = effective_board_id or pinterest_status.get("board_id")
                    pinterest_status["board_section_id"] = _get_first_non_empty(
                        data,
                        ("pinterest_board_section_id",),
                    ) or pinterest_status.get("board_section_id")
                    pinterest_status["error"] = None
                    data["pinterest_title"] = pinterest_title
                    data["pinterest_description"] = pinterest_description
                    data["pinterest_board_id"] = effective_board_id
                    totals["pinterest_uploaded"] += 1
                    changed = True
                    print(f"  Pinterest: created pin_id={pin_id}")
                except Exception as exc:
                    message = str(exc)
                    totals["errors"] += 1
                    print(f"  Pinterest Error: {message}")
                    print(
                        "  Pinterest Hint: "
                        + explain_pinterest_readiness(
                            data,
                            approved=True,
                            already_uploaded=False,
                            video_path=asset.video_path,
                        )
                    )
                    if not args.dry_run:
                        pinterest_status["error"] = message
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
