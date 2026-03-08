import argparse
import mimetypes
import re
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


SCOPES = ["https://www.googleapis.com/auth/drive"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull or push Google Drive folder contents for CI workflows."
    )
    parser.add_argument("mode", choices={"pull", "push"})
    parser.add_argument("--folder-url", required=True, help="Google Drive folder URL or ID.")
    parser.add_argument(
        "--service-account-json",
        required=True,
        help="Path to the Google service account key JSON file.",
    )
    parser.add_argument(
        "--extensions",
        default=".json",
        help="Comma-separated list of file extensions to sync.",
    )
    parser.add_argument(
        "--dest",
        help="Local destination directory for pull mode.",
    )
    parser.add_argument(
        "--src",
        help="Local source directory for push mode.",
    )
    parser.add_argument(
        "--clear-dest",
        action="store_true",
        help="Delete existing local files with matching extensions before pull.",
    )
    return parser.parse_args()


def _normalize_extensions(raw_value: str) -> set[str]:
    items = [item.strip().lower() for item in raw_value.split(",") if item.strip()]
    if not items:
        raise ValueError("At least one extension is required.")
    return {item if item.startswith(".") else f".{item}" for item in items}


def _extract_drive_folder_id(folder_ref: str) -> str:
    value = (folder_ref or "").strip()
    if not value:
        raise ValueError("Missing Google Drive folder reference.")

    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value

    match = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)

    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)

    raise ValueError(f"Could not extract a Google Drive folder ID from: {value}")


def _build_drive_service(service_account_json: str):
    creds = service_account.Credentials.from_service_account_file(
        service_account_json,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _iter_folder_files(service, folder_id: str):
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("files", []):
            yield item
        page_token = response.get("nextPageToken")
        if not page_token:
            break


def _file_map_by_name(service, folder_id: str, extensions: set[str]) -> dict[str, dict]:
    file_map: dict[str, dict] = {}
    for item in _iter_folder_files(service, folder_id):
        name = str(item.get("name") or "")
        if Path(name).suffix.lower() not in extensions:
            continue
        if name in file_map:
            raise RuntimeError(f"Duplicate Google Drive file name found: {name}")
        file_map[name] = item
    return file_map


def _iter_local_files(folder: Path, extensions: set[str]):
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue
        yield path


def _download_file(service, file_id: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with destination.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _guess_mime_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def pull_folder(
    folder_url: str,
    destination_dir: str,
    service_account_json: str,
    extensions: set[str],
    clear_dest: bool,
) -> None:
    service = _build_drive_service(service_account_json)
    folder_id = _extract_drive_folder_id(folder_url)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    if clear_dest:
        for path in _iter_local_files(destination, extensions):
            path.unlink()

    remote_files = _file_map_by_name(service, folder_id, extensions)
    for name, item in sorted(remote_files.items()):
        target = destination / name
        _download_file(service, str(item["id"]), target)
        print(f"Downloaded {name}")

    print(
        f"Pull complete: {len(remote_files)} file(s) downloaded from folder {folder_id} "
        f"to {destination}"
    )


def _update_remote_file(service, file_id: str, source: Path) -> None:
    media = MediaFileUpload(str(source), mimetype=_guess_mime_type(source), resumable=False)
    (
        service.files()
        .update(fileId=file_id, media_body=media, supportsAllDrives=True)
        .execute()
    )


def _create_remote_file(service, folder_id: str, source: Path) -> None:
    media = MediaFileUpload(str(source), mimetype=_guess_mime_type(source), resumable=False)
    metadata = {"name": source.name, "parents": [folder_id]}
    (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name",
            supportsAllDrives=True,
        )
        .execute()
    )


def push_folder(
    folder_url: str,
    source_dir: str,
    service_account_json: str,
    extensions: set[str],
) -> None:
    service = _build_drive_service(service_account_json)
    folder_id = _extract_drive_folder_id(folder_url)
    source = Path(source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source}")

    remote_files = _file_map_by_name(service, folder_id, extensions)
    pushed = 0

    for path in _iter_local_files(source, extensions):
        remote = remote_files.get(path.name)
        if remote:
            _update_remote_file(service, str(remote["id"]), path)
            print(f"Updated {path.name}")
        else:
            _create_remote_file(service, folder_id, path)
            print(f"Created {path.name}")
        pushed += 1

    print(
        f"Push complete: {pushed} file(s) synced from {source} to folder {folder_id}"
    )


def main() -> int:
    args = _parse_args()
    extensions = _normalize_extensions(args.extensions)

    if args.mode == "pull":
        if not args.dest:
            raise SystemExit("--dest is required for pull mode.")
        pull_folder(
            folder_url=args.folder_url,
            destination_dir=args.dest,
            service_account_json=args.service_account_json,
            extensions=extensions,
            clear_dest=args.clear_dest,
        )
        return 0

    if not args.src:
        raise SystemExit("--src is required for push mode.")
    push_folder(
        folder_url=args.folder_url,
        source_dir=args.src,
        service_account_json=args.service_account_json,
        extensions=extensions,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
