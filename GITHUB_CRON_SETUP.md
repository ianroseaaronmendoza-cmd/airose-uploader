# GitHub Scheduled Upload Setup (YouTube + IG/FB)

## 1. One-time local auth (YouTube)

Run this locally once:

```powershell
python refresh_youtube_token.py
```

This creates/updates `token.pkl` with your refresh token.

## 2. Create GitHub Secrets (base64)

From PowerShell in project root
(replace `google_service_account.json` with your actual local service-account key filename if needed):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.pkl"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("meta_credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("google_service_account.json"))
```

Add these as repository secrets:

- `YOUTUBE_CREDENTIALS_JSON_B64`
- `YOUTUBE_TOKEN_PKL_B64`
- `META_CREDENTIALS_JSON_B64`
- `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (required when syncing metadata/video folders from Google Drive)

Notes:

- `YOUTUBE_TOKEN_PKL_B64` must be the full single-line base64 value.
- The JSON-based secrets can be stored either as the full single-line base64 value or as raw JSON text.

## 3. Set GitHub Variables

Repository Variables used by workflow:

- `UPLOADER_METADATA_FOLDER`
  - Path to metadata JSON folder on runner.
  - Example (repo folder): `metadata`
- `UPLOADER_VIDEO_FOLDER` (optional)
  - Path to local mp4 files on runner.
  - If videos are not local, the headless runner can download from public URL fields in metadata.
- `GOOGLE_DRIVE_METADATA_FOLDER_URL` (optional)
  - Google Drive folder URL for metadata JSON files.
  - When set, the workflow downloads metadata from Drive into a temporary runner folder and syncs upload-status updates back to Drive after the run.
- `GOOGLE_DRIVE_VIDEO_FOLDER_URL` (optional)
  - Google Drive folder URL for source video files.
  - When set, the workflow downloads mp4/mov/m4v files into a temporary runner folder before the upload job starts.

If you use Google Drive folders, share those folders with the service-account email from `google_service_account.json`.

## 4. Workflow

Workflow file:

- `.github/workflows/scheduled_upload.yml`

It supports:

- Scheduled run (daily at 8:00 PM Asia/Singapore, which is 12:00 UTC)
- Manual run (`workflow_dispatch`) with optional:
  - `platforms` (default: `youtube,igfb`)
  - `dry_run` (`true/false`)

Drive-backed mode:

- If `GOOGLE_DRIVE_METADATA_FOLDER_URL` is set, you do not need a `metadata/` folder in the repo.
- If `GOOGLE_DRIVE_VIDEO_FOLDER_URL` is set, you do not need local videos checked into the repo.
- Metadata files are downloaded from Drive before the job and pushed back to the same Drive folder after upload-status changes.

## 5. Metadata requirements

An asset is processed only when:

- `upload_status.youtube.approved == true`

Source video URL can come from any of:

- `youtube_video_url`
- `public_video_url`
- `instagram_video_url`
- `google_drive_link`
- `google_drive_url`

## 6. Notes

- TikTok is untouched.
- YouTube runs non-interactive by default in CI (no browser popup).
- IG/FB system token is used as-is from `meta_credentials.json`.
- For CI, set `google_service_account_json` in `meta_credentials.json` to `google_service_account.json`.
